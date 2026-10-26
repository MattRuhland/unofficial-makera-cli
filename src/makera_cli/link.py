"""TCP link, network discovery and protocol detection.

The Z1 listens on TCP port 2222 and announces itself on the local network every second with a
UDP broadcast to port 3333: ``Makera_Z1,<ip>,<port>,<busy>,<state>``. ``busy`` is 1 while a
client holds the connection. The machine accepts one client at a time.
"""

import socket
import time
from collections import namedtuple

from makera_cli import MakeraError
from makera_cli._vendor.carvera.protocols.framing import FRAME_END, FRAME_HEADER, validate_packet_data

PORT = 2222
DISCOVERY_PORT = 3333

Machine = namedtuple("Machine", "model host port busy state")


class Link:
    """A socket with the ``getc``/``putc`` pair the vendored XMODEM transfer code expects."""

    def __init__(self, host, port=PORT, timeout=3.0):
        try:
            self.sock = socket.create_connection((host, port), timeout=timeout)
        except OSError as e:
            raise MakeraError(
                "cannot connect to %s:%d (%s). Is the machine on, and is no other client "
                "(such as Makera Studio) connected?" % (host, port, e)
            )
        self.sock.settimeout(0.3)
        self.rx = bytearray()

    def send(self, data):
        self.sock.sendall(data)

    def getc(self, size, timeout=0.5):
        start = time.time()
        data = bytearray()
        while len(data) < size and time.time() - start <= timeout:
            try:
                chunk = self.sock.recv(size - len(data))
                if not chunk:
                    break
                data.extend(chunk)
            except socket.timeout:
                pass
        return bytes(data) if len(data) == size else None

    def putc(self, data, timeout=0.5):
        self.sock.sendall(data)
        return len(data)

    def frames(self, seconds):
        """Read for ``seconds`` and return every complete, valid frame as (type, payload)."""
        end = time.time() + seconds
        while time.time() < end:
            try:
                chunk = self.sock.recv(4096)
                if chunk:
                    self.rx.extend(chunk)
            except socket.timeout:
                pass
        return split_frames(self.rx)

    def close(self):
        self.sock.close()


def split_frames(buf):
    """Remove complete frames from the front of ``buf`` and return them as (type, payload).

    A frame is header 0x8668, a 2-byte length, the body (type, payload, CRC16), and footer
    0x55AA. An incomplete frame at the end stays in ``buf`` for the next read.
    """
    out = []
    header = FRAME_HEADER.to_bytes(2, "big")
    i = 0
    while True:
        j = buf.find(header, i)
        if j < 0 or j + 4 > len(buf):
            break
        n = int.from_bytes(buf[j + 2:j + 4], "big")
        stop = j + 4 + n + 2
        if stop > len(buf):
            break
        body, foot = bytes(buf[j + 2:j + 4 + n]), bytes(buf[j + 4 + n:stop])
        if foot == FRAME_END.to_bytes(2, "big"):
            p = validate_packet_data(body)
            if p is not None:
                out.append((p.ptype, p.payload))
        i = stop
    del buf[:i]
    return out


def parse_announcement(data):
    """A discovery broadcast as a Machine, or None for anything else."""
    f = data.decode(errors="replace").strip().split(",")
    if len(f) < 4 or not f[0].startswith("Makera"):
        return None
    try:
        port = int(f[2])
    except ValueError:
        return None
    return Machine(f[0], f[1], port, f[3] == "1", f[4] if len(f) > 4 else "")


def discover(seconds=6.0):
    """Listen for a machine's broadcast and return the first Machine heard."""
    u = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    u.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if hasattr(socket, "SO_REUSEPORT"):
        u.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)  # Makera Studio may hold 3333 too
    u.bind(("0.0.0.0", DISCOVERY_PORT))
    u.settimeout(1)
    end = time.time() + seconds
    try:
        while time.time() < end:
            try:
                data, _ = u.recvfrom(256)
            except socket.timeout:
                continue
            m = parse_announcement(data)
            if m:
                return m
    finally:
        u.close()
    raise MakeraError(
        "no Makera broadcast on UDP %d in %.0f s. Is the machine on and on the same network? "
        "Use --host to skip discovery." % (DISCOVERY_PORT, seconds)
    )


def detect_framed(link):
    """True when the machine speaks Makera's framed protocol.

    Same test as the community controller: the plain-text (Smoothie) protocol echoes
    ``echo echo`` back, and the framed protocol stays silent.
    """
    link.frames(0.2)
    for _ in range(3):
        link.send(b"echo echo\n")
        time.sleep(0.1)
        got = link.getc(10, timeout=0.1)
        if got and b"echo" in got:
            return False
    return True
