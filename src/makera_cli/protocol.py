"""Command session and reply parsers.

Reply types, as the machine sends them:

- ``?`` (a realtime character in a CTRL_SINGLE frame) is answered with a STATUS_RES frame
  holding one ``<State|MPos:...|WPos:...|...>`` report.
- ``diagnose`` is answered with a DIAG_RES frame holding one ``{S:...|L:...|...}`` report.
- ``version``, ``$#`` and general machine messages arrive as NORMAL_INFO fragments. They are
  joined into lines at each newline.
- ``ls`` arrives as LOAD_INFO chunks and ends with LOAD_FINISH (or LOAD_ERROR).

Commands go out as CTRL_MULTI frames with no trailing newline, matching Makera's own client.
Formats follow the community Carvera controller (``Controller.parseBracketAngle``,
``parseBigParentheses``, ``parseWCSParameters`` and ``process_loaded_dir``).
"""

import re
import sys
import time

from makera_cli import MakeraError
from makera_cli._vendor.carvera.protocols.framing import (
    PTYPE_CTRL_MULTI,
    PTYPE_CTRL_SINGLE,
    PTYPE_DIAG_RES,
    PTYPE_LOAD_ERROR,
    PTYPE_LOAD_FINISH,
    PTYPE_LOAD_INFO,
    PTYPE_NORMAL_INFO,
    PTYPE_STATUS_RES,
    build_frame,
)
from makera_cli.link import PORT, Link, detect_framed


def path_arg(path):
    """Escape a path for a console command, as the community controller does.

    Spaces become 0x01, and ``? & ! ~`` become 0x02 to 0x05, since the firmware treats those
    characters as realtime commands.
    """
    return (path.replace(" ", "\x01").replace("?", "\x02").replace("&", "\x03")
                .replace("!", "\x04").replace("~", "\x05"))


class Session:
    """A framed connection that sorts replies into status, diagnose, text lines and load data."""

    def __init__(self, host, port=PORT):
        self.link = Link(host, port)
        if not detect_framed(self.link):
            self.link.close()
            raise MakeraError(
                "the machine answered in plain text (Smoothie protocol). This CLI only speaks "
                "Makera's framed protocol, which the Z1 uses."
            )
        self.partial = bytearray()

    def command(self, text):
        self.link.send(build_frame(PTYPE_CTRL_MULTI, text.encode()))

    def realtime(self, char):
        self.link.send(build_frame(PTYPE_CTRL_SINGLE, char.encode()))

    def read(self, seconds):
        """Replies for ``seconds`` as (kind, text). Kinds: status, diag, line, load, eof, error."""
        out = []
        for ptype, payload in self.link.frames(seconds):
            if ptype == PTYPE_NORMAL_INFO:
                for b in payload:
                    if b == 0x0A:
                        out.append(("line", self.partial.decode(errors="replace").rstrip("\r")))
                        self.partial.clear()
                    else:
                        self.partial.append(b)
            elif ptype == PTYPE_LOAD_FINISH:
                out.append(("eof", ""))
            elif ptype == PTYPE_LOAD_ERROR:
                out.append(("error", ""))
            else:
                text = payload.decode(errors="replace").strip()
                kind = {PTYPE_STATUS_RES: "status", PTYPE_DIAG_RES: "diag",
                        PTYPE_LOAD_INFO: "load"}.get(ptype, "line")
                if text.startswith("<"):
                    kind = "status"
                out.append((kind, text))
        return out

    def wait_for(self, kind, timeout=3.0):
        """The first reply of ``kind``. Other replies are printed to stderr."""
        end = time.time() + timeout
        while time.time() < end:
            for k, text in self.read(0.2):
                if k == kind:
                    return text
                if text:
                    print("machine: %s" % text, file=sys.stderr)
        raise MakeraError("no %s reply in %.0f s" % (kind, timeout))

    def lines(self, quiet=0.8, timeout=5.0):
        """Text lines until none arrive for ``quiet`` seconds."""
        got, end, last = [], time.time() + timeout, time.time()
        while time.time() < end and time.time() - last < quiet:
            for k, text in self.read(0.1):
                if k == "line" and text:
                    got.append(text)
                    last = time.time()
        return got

    def load(self, timeout=10.0):
        """LOAD_INFO text up to LOAD_FINISH, split into lines."""
        chunks, end = [], time.time() + timeout
        while time.time() < end:
            for k, text in self.read(0.2):
                if k == "load":
                    chunks.append(text)
                elif k == "eof":
                    return "\n".join(chunks).replace("\r", "").split("\n")
                elif k == "error":
                    raise MakeraError("the machine reported a load error")
                elif k == "line" and text:
                    print("machine: %s" % text, file=sys.stderr)
        raise MakeraError("no end of listing in %.0f s" % timeout)

    def status(self):
        self.realtime("?")
        return parse_status(self.wait_for("status"))

    def close(self):
        self.link.close()


def parse_status(line):
    """A ``<Idle|MPos:x,y,z,a|WPos:...|T:tool,tlo|F:...|S:...>`` report as a dict."""
    a, b = line.find("<"), line.rfind(">")
    if a < 0 or b <= a:
        raise MakeraError("malformed status report: %r" % line)
    fields = line[a + 1:b].split("|")
    raw = {}
    for f in fields[1:]:
        if ":" in f:
            k, v = f.split(":", 1)
            try:
                raw[k] = [float(x) for x in v.split(",")]
            except ValueError:
                raw[k] = v
    s = {"state": fields[0], "raw": raw}
    for k in ("MPos", "WPos"):
        if isinstance(raw.get(k), list):
            s[k.lower()] = dict(zip("xyza", raw[k]))
    if isinstance(raw.get("T"), list):
        s["tool"] = int(raw["T"][0])
        if len(raw["T"]) > 1:
            s["tlo"] = raw["T"][1]
    if isinstance(raw.get("F"), list) and len(raw["F"]) >= 3:
        s["feed"] = {"current": raw["F"][0], "target": raw["F"][1], "override": raw["F"][2]}
    if isinstance(raw.get("S"), list) and len(raw["S"]) >= 3:
        s["spindle"] = {"rpm": raw["S"][0], "target": raw["S"][1], "override": raw["S"][2]}
    if isinstance(raw.get("P"), list) and len(raw["P"]) >= 3:
        s["playing"] = {"line": int(raw["P"][0]), "percent": int(raw["P"][1]),
                        "seconds": int(raw["P"][2])}
    return s


def parse_wcs(lines):
    """The ``$#`` reply as ({name: values}, unparsed lines).

    Parsed: ``[G54:x,y,z,a,b,rotation]`` through G59.3, G28, G30, G92, ``[TL0:length]`` (as
    "TLO") and ``[PRB:x,y,z:1]`` (as "PRB", the last probe point in machine coordinates, with
    ``ok`` set from the trailing flag).
    """
    wcs, other = {}, []
    for line in lines:
        m = re.match(r"\[(G5[4-9][.1-3]*|G28|G30|G92):([^\]]+)\]", line)
        p = re.match(r"\[PRB:([^:\]]+):(\d)\]", line)
        t = re.match(r"\[TL0:([^\]]+)\]", line)
        if m:
            v = [float(x) for x in m.group(2).split(",")]
            wcs[m.group(1)] = dict(zip(("x", "y", "z", "a", "b", "rotation"), v))
        elif p:
            wcs["PRB"] = dict(zip("xyz", (float(x) for x in p.group(1).split(","))))
            wcs["PRB"]["ok"] = p.group(2) == "1"
        elif t:
            wcs["TLO"] = float(t.group(1))
        elif line != "ok":
            other.append(line)
    return wcs, other


def parse_diag(line):
    """A ``{S:0,5000|L:0,0|...|RSSI:-57}`` report as {key: [ints]}."""
    a, b = line.find("{"), line.rfind("}")
    d = {}
    for f in line[a + 1:b].split("|"):
        if ":" in f:
            k, v = f.split(":", 1)
            try:
                d[k] = [int(x) for x in v.split(",")]
            except ValueError:
                d[k] = v
    return d


def parse_ls(lines):
    """``name size YYYYmmddHHMMSS`` rows as dicts. A trailing / marks a directory."""
    out = []
    for line in lines:
        f = line.split()
        if len(f) == 3 and not f[0].startswith(".") and f[1].isdigit() and f[2].isdigit():
            name = f[0].replace("\x01", " ")
            out.append({"name": name.rstrip("/"), "dir": name.endswith("/"),
                        "size": int(f[1]), "date": f[2]})
    return out


def parse_config(text):
    """``config.txt`` as {key: value}, with comments dropped."""
    conf = {}
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            parts = line.split(None, 1)
            conf[parts[0]] = parts[1].strip() if len(parts) > 1 else ""
    return conf
