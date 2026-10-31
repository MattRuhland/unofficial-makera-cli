"""Single-frame capture from the Z1's built-in camera.

The camera is served by the machine's ESP32 WiFi module: a WebSocket at
``ws://<host>:82/ws_video`` that starts sending frames after the client sends the text
``start_stream``. Each binary message is one whole JPEG.

The camera serves one stream and gives it to the newest client. Taking a snapshot cuts off a
live view open in Makera Studio, which may not reconnect by itself.

Resolution is the only camera setting. It is set over HTTP on port 80 and persists on the
camera, so other viewers see the change too.

A resolution change does not take effect at once. Measured on a Z1 on 2026-10-31: the stream sends
a few more frames at the old size. Then it sends one bad frame, whose header gives the new size
while its data is still the size of an old frame. That frame is a valid JPEG and decodes to garbage.
So ``stable_frame`` keeps only a frame that agrees with the one before it.
"""

import base64
import json
import os
import socket
import struct
import time
import urllib.request

from makera_cli import MakeraError

CAMERA_PORT = 82
CAMERA_PATH = "/ws_video"
CONTROL_PORT = 80
RESOLUTION_PATH = "/api/camera/resolution"

# Espressif framesize values. Frame rate falls as resolution rises: about 20 fps at 640x480 and
# about 10 fps at 1600x1200. The firmware answers 200 to other values and ignores them.
RESOLUTIONS = {10: "640x480", 11: "800x600", 12: "1024x768", 13: "1280x720",
               14: "1280x1024", 15: "1600x1200"}


SKIP_FRAMES = 3  # the first frames of a stream can be buffered or left over from a size change
MAX_FRAME_RATIO = 2.0  # neighbouring good frames are close in bytes; the bad frame is about 5x


def set_resolution(host, value):
    req = urllib.request.Request(
        "http://%s:%d%s" % (host, CONTROL_PORT, RESOLUTION_PATH),
        data=json.dumps({"resolution": value}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        if resp.status != 200:
            raise MakeraError("camera refused resolution %d (HTTP %d)" % (value, resp.status))


def _take(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise MakeraError("camera closed the connection")
        buf += chunk
    return buf


def read_frame(sock):
    """One WebSocket frame as (opcode, payload)."""
    b0, b1 = _take(sock, 2)
    n = b1 & 0x7F
    if n == 126:
        n = struct.unpack(">H", _take(sock, 2))[0]
    elif n == 127:
        n = struct.unpack(">Q", _take(sock, 8))[0]
    mask = _take(sock, 4) if b1 & 0x80 else None
    data = _take(sock, n)
    if mask:
        data = bytes(c ^ mask[i % 4] for i, c in enumerate(data))
    return b0 & 0x0F, data


def text_frame(text):
    """A masked client text frame, as RFC 6455 requires of clients."""
    msg, mask = text.encode(), os.urandom(4)
    if len(msg) > 125:
        raise ValueError("short messages only")
    return bytes([0x81, 0x80 | len(msg)]) + mask + bytes(c ^ mask[i % 4] for i, c in enumerate(msg))


def jpeg_size(data):
    """(width, height) from a JPEG header, or None if the header cannot be read."""
    i = 2
    while i + 9 <= len(data) and data[i] == 0xFF:
        marker = data[i + 1]
        if marker in (0xC0, 0xC1, 0xC2):
            height, width = struct.unpack(">HH", data[i + 5:i + 9])
            return width, height
        if marker == 0xDA:
            return None
        i += 2 + struct.unpack(">H", data[i + 2:i + 4])[0]
    return None


def stable_frame(frames, want=None, skip=SKIP_FRAMES):
    """The first trustworthy frame from an iterable of JPEGs. Raises MakeraError if none arrives.

    A frame is trusted when it comes after the first ``skip``, has the wanted (width, height) if
    one is given, and matches the previous frame in size and roughly in bytes.
    """
    prev, prev_size, size = None, None, None
    for i, data in enumerate(frames):
        size = jpeg_size(data)
        if (i >= skip and size is not None and (want is None or size == want)
                and size == prev_size
                and max(len(data), len(prev)) <= MAX_FRAME_RATIO * min(len(data), len(prev))):
            return data
        prev, prev_size = data, size
    if want is not None and size != want:
        raise MakeraError("the camera did not change to %dx%d. Its last frame was %s."
                          % (want[0], want[1], "%dx%d" % size if size else "unreadable"))
    raise MakeraError("no stable frame from the camera. Nothing was saved.")


def snapshot(host, out, resolution=None, timeout=8.0):
    """Save one JPEG from the camera to ``out`` and return its size in bytes."""
    if resolution is not None:
        set_resolution(host, resolution)
    try:
        sock = socket.create_connection((host, CAMERA_PORT), timeout=timeout)
    except OSError as e:
        raise MakeraError("cannot reach the camera at %s:%d (%s)" % (host, CAMERA_PORT, e))
    try:
        key = base64.b64encode(os.urandom(16)).decode()
        sock.sendall(("GET %s HTTP/1.1\r\nHost: %s:%d\r\nUpgrade: websocket\r\n"
                      "Connection: Upgrade\r\nSec-WebSocket-Key: %s\r\n"
                      "Sec-WebSocket-Version: 13\r\n\r\n"
                      % (CAMERA_PATH, host, CAMERA_PORT, key)).encode())
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = sock.recv(1024)
            if not chunk:
                raise MakeraError("camera closed the connection during the handshake")
            head += chunk
        status = head.split(b"\r\n", 1)[0]
        if b" 101 " not in status:
            raise MakeraError("camera refused the WebSocket: %s" % status.decode(errors="replace"))
        sock.sendall(text_frame("start_stream"))

        def jpegs():
            end = time.time() + timeout
            while time.time() < end:
                op, data = read_frame(sock)
                if op == 2 and data[:2] == b"\xff\xd8":
                    yield data
                if op == 8:
                    raise MakeraError("camera closed the stream")

        want = None
        if resolution is not None:
            want = tuple(int(v) for v in RESOLUTIONS[resolution].split("x"))
        try:
            data = stable_frame(jpegs(), want)
        except OSError as e:
            raise MakeraError("the camera stopped sending frames (%s)" % e)
        with open(out, "wb") as f:
            f.write(data)
        return len(data)
    finally:
        sock.close()
