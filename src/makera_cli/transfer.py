"""File upload and download over Makera's framed transfer protocol.

Upload: ``upload <path>`` goes in a FILE_START frame. The client then sends the file's MD5,
the machine asks for a VIEW (packet count and the 8192-byte packet size), requests each DATA
packet by sequence number, and sends FILE_END. The machine then prints
``Info: upload success: <path>.``, which this module requires before it reports success.

Download is the same exchange in reverse, started by ``download <path>``.

The Z1 answers ``md5sum`` with a fixed placeholder, so the byte count from ``ls`` is the check
available after an upload. An upload overwrites an existing file of the same name without
asking.
"""

import hashlib
import os
import time

from makera_cli import MakeraError
from makera_cli._vendor.carvera.XMODEM import XMODEM
from makera_cli._vendor.carvera.protocols.framing import PTYPE_FILE_START, PTYPE_NORMAL_INFO, build_frame
from makera_cli.protocol import path_arg

PACKET_SIZE = 8192


def upload(link, path, remote_dir="/sd/gcodes", prefix="", log=print):
    """Upload ``path`` to ``remote_dir/prefix+basename`` and return the remote path."""
    name = prefix + os.path.basename(path)
    remote = "%s/%s" % (remote_dir.rstrip("/"), name)
    with open(path, "rb") as f:
        md5 = hashlib.md5(f.read()).hexdigest()
    size = os.path.getsize(path)
    log("%s -> %s  (%d bytes, %d packets, md5 %s)"
        % (name, remote, size, -(-size // PACKET_SIZE), md5))
    link.frames(0.2)  # drop anything pending
    link.send(build_frame(PTYPE_FILE_START, ("upload %s\n" % path_arg(remote)).encode()))
    modem = XMODEM(link.getc, link.putc, "xmodem8k")
    last = [0]

    def progress(packet_size, seq, _a, _b):
        pct = int(100 * min(seq * packet_size, size) / size) if size else 100
        if pct >= last[0] + 10 or seq * packet_size >= size:
            last[0] = pct
            log("   %3d%%" % pct)

    start = time.time()
    with open(path, "rb") as stream:
        ok = modem.send(stream, md5=md5, retry=50, callback=progress)
    if not ok:
        reason = getattr(modem, "last_file_error", None)
        raise MakeraError("transfer %s%s" % ("cancelled" if ok is None else "failed",
                                              ": %s" % reason if reason else ""))
    text = ""
    for _ in range(10):  # up to 5 s for the machine's verdict
        for ptype, payload in link.frames(0.5):
            if ptype == PTYPE_NORMAL_INFO:
                text += payload.decode(errors="replace")
        if "upload success" in text or "rror" in text:
            break
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    for ln in lines:
        log("   machine: %s" % ln)
    if not any("upload success" in ln and name in ln for ln in lines):
        raise MakeraError('no "upload success" from the machine for %s' % name)
    log("   OK in %.1f s" % (time.time() - start))
    return remote


def download(link, remote, local):
    """Download ``remote`` into ``local`` and return the byte count.

    Writes to ``local.part`` first and renames on success, so a failed transfer leaves no
    partial file at ``local``.
    """
    link.frames(0.2)
    link.send(build_frame(PTYPE_FILE_START, ("download %s\n" % path_arg(remote)).encode()))
    modem = XMODEM(link.getc, link.putc, "xmodem8k")
    tmp = local + ".part"
    with open(tmp, "wb") as stream:
        result = modem.recv(stream, md5="", retry=50)
    if result is None or result < 0:
        os.remove(tmp)
        raise MakeraError("download of %s %s" % (
            remote, "cancelled by the machine" if result is None else "failed"))
    os.replace(tmp, local)
    return os.path.getsize(local)
