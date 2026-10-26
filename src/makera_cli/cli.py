"""The ``makera`` command. Run ``makera --help`` or see README.md for the full reference."""

import argparse
import hashlib
import json
import os
import sys
import tempfile
import time

from makera_cli import MakeraError, __version__
from makera_cli.camera import RESOLUTIONS, snapshot
from makera_cli.link import PORT, discover
from makera_cli.motion import OUTPUTS, PARK, WARNINGS, plan, soft_limits, wait_cycle, wait_idle
from makera_cli.protocol import (Session, parse_active_wcs, parse_config, parse_diag, parse_ls,
                                 parse_wcs, path_arg)
from makera_cli.transfer import download, upload

READ_STATES = ("Idle", "Sleep", "Alarm")
ACTIONS = ("home", "unlock", "jog", "goto", "park", "zero", "spindle", "play", "pause",
           "resume", "abort", "send") + tuple(OUTPUTS)
ANY_STATE = ("pause", "resume", "abort")


def fmt_axes(d):
    return "  ".join("%s %9.3f" % (k.upper(), v) for k, v in d.items())


def show_status(s):
    print("state     %s" % s["state"])
    for k in ("mpos", "wpos"):
        if k in s:
            print("%-9s %s" % (k, fmt_axes(s[k])))
    if "tool" in s:
        print("tool      T%d  TLO %.3f" % (s["tool"], s.get("tlo", 0.0)))
    if "feed" in s:
        print("feed      %(current).0f mm/min  (target %(target).0f, override %(override).0f%%)"
              % s["feed"])
    if "spindle" in s:
        print("spindle   %(rpm).0f rpm  (target %(target).0f, override %(override).0f%%)"
              % s["spindle"])
    if "playing" in s:
        print("playing   line %(line)d, %(percent)d%%, %(seconds)d s" % s["playing"])


def show_wcs(wcs, other):
    for name, v in wcs.items():
        if name == "active":
            print("active %s" % v)
        elif name == "TLO":
            print("TLO    %.4f" % v)
        elif name == "PRB":
            print("PRB    %s  (%s)" % (fmt_axes({k: v[k] for k in "xyz"}),
                                       "hit" if v["ok"] else "no hit"))
        else:
            print("%-6s %s" % (name, fmt_axes(v)))
    for line in other:
        print(line)


def fetch_config(sess, save=None):
    """config.txt from the machine as {key: value}. Keeps the raw file at ``save`` if given."""
    if save:
        download(sess.link, "/sd/config.txt", save)
        path = save
    else:
        fd, path = tempfile.mkstemp(suffix="-config.txt")
        os.close(fd)
        download(sess.link, "/sd/config.txt", path)
    try:
        with open(path, errors="replace") as f:
            return parse_config(f.read())
    finally:
        if not save:
            os.remove(path)


def run_action(sess, a, st):
    """Check the machine state, plan, print the plan, and send it only with --yes."""
    need = None if a.cmd in ANY_STATE else ("Alarm",) if a.cmd == "unlock" else ("Idle",)
    if need and st["state"] not in need and not a.force:
        raise MakeraError("machine state is %s. %s needs %s (or pass --force)."
                          % (st["state"], a.cmd, " or ".join(need)))
    if a.cmd in WARNINGS and not (a.cmd == "spindle" and a.state == "off"):
        print(WARNINGS[a.cmd])
    if a.cmd in ("pause", "resume"):
        char = "!" if a.cmd == "pause" else "~"
        print("%s realtime %r" % ("send" if a.yes else "would send", char))
        if a.yes:
            sess.realtime(char)
            time.sleep(0.5)
            print("state now %s" % sess.status()["state"])
        else:
            print("dry run: add --yes to send")
        return
    limits = None
    if a.cmd in ("jog", "goto"):
        limits = soft_limits(fetch_config(sess))
        if limits is None:
            print("warning: soft limits are off in config.txt, so moves are not pre-checked",
                  file=sys.stderr)
    if a.cmd == "play":
        sess.command("ls -e -s %s" % path_arg(a.dir.rstrip("/")))
        if a.name not in [r["name"] for r in parse_ls(sess.load()) if not r["dir"]]:
            raise MakeraError("no file %s in %s" % (a.name, a.dir))
    steps = plan(a, st, limits)
    for line, _ in steps:
        print("%s %s" % ("send" if a.yes else "would send", line))
    if not a.yes:
        print("dry run: add --yes to send")
        return
    for line, moves in steps:
        sess.command(line)
        if moves:
            wait_idle(sess, a.timeout)
        elif getattr(a, "wait", False):
            wait_cycle(sess, timeout=a.timeout)
        else:
            for text in sess.lines(quiet=0.5, timeout=3.0):
                print("machine: %s" % text)
    st = sess.status()
    print("state %s  machine %s" % (st["state"], fmt_axes(st.get("mpos", {}))))


def build_parser():
    ap = argparse.ArgumentParser(
        prog="makera",
        description="Unofficial command line interface for the Makera Z1 over WiFi. "
                    "Not affiliated with or endorsed by Makera.",
        epilog="Actions print what they would send and stop unless --yes is given. "
               "Global options go before the command: makera --yes jog X10")
    ap.add_argument("--version", action="version", version="%(prog)s " + __version__)
    ap.add_argument("--host", help="machine address; skips discovery")
    ap.add_argument("--port", type=int, default=PORT, help="machine TCP port (default %(default)s)")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--yes", action="store_true", help="send actions; without it they only print")
    ap.add_argument("--force", action="store_true", help="skip the machine-state check")
    ap.add_argument("--timeout", type=float, default=120.0,
                    help="seconds to wait for a move to finish (default %(default)s)")
    sub = ap.add_subparsers(dest="cmd", metavar="COMMAND", required=True)

    sub.add_parser("find", help="listen for the machine's broadcast (no connection)")
    sub.add_parser("status", help="state, positions, tool, feed, spindle")
    sub.add_parser("version", help="firmware version")
    sub.add_parser("wcs", help="work offsets, tool length and last probe point ($#)")
    sub.add_parser("diagnose", help="switch and sensor states")
    p = sub.add_parser("ls", help="list a directory on the SD card")
    p.add_argument("dir", nargs="?", default="/sd/gcodes")
    p = sub.add_parser("config", help="download config.txt and print it, or the named keys")
    p.add_argument("keys", nargs="*")
    p.add_argument("--save", metavar="PATH", help="also keep the raw config.txt here")
    p = sub.add_parser("download", help="copy a file from the SD card")
    p.add_argument("remote")
    p.add_argument("local", nargs="?")
    p = sub.add_parser("upload", help="copy files to the SD card")
    p.add_argument("files", nargs="+")
    p.add_argument("--dir", default="/sd/gcodes")
    p.add_argument("--prefix", default="", help="prepended to each remote file name")
    p = sub.add_parser("snapshot", help="save one camera frame as JPEG")
    p.add_argument("out", nargs="?", default="makera-snapshot.jpg")
    p.add_argument("--resolution", type=int, choices=sorted(RESOLUTIONS),
                   help="persistent camera framesize: "
                        + ", ".join("%d=%s" % kv for kv in RESOLUTIONS.items()))

    sub.add_parser("home", help="action: home all axes ($H)")
    sub.add_parser("unlock", help="action: clear an alarm ($X)")
    p = sub.add_parser("jog", help="action: relative move, one $J per axis")
    p.add_argument("axes", nargs="+", metavar="AXIS", help="for example X10 Y-5 Z2")
    p.add_argument("--feed", type=float, help="mm/min")
    p = sub.add_parser("goto", help="action: rapid move to a position")
    p.add_argument("axes", nargs="+", metavar="AXIS", help="for example X0 Y0 Z5")
    p.add_argument("--machine", action="store_true", help="machine coordinates (G53)")
    p.add_argument("--direct", action="store_true", help="skip the raise to machine Z-2 first")
    p = sub.add_parser("park", help="action: move to a stored position (M496.x)")
    p.add_argument("where", choices=sorted(PARK))
    p = sub.add_parser("zero", help="action: set the current position in the active WCS")
    p.add_argument("axes", help="axis letters, for example xy or z")
    p.add_argument("--value", type=float, default=0.0, help="value to assign (default 0)")
    p = sub.add_parser("spindle", help="action: spindle on at RPM, or off")
    p.add_argument("state", choices=("on", "off"))
    p.add_argument("rpm", nargs="?", type=int)
    for name in OUTPUTS:
        sub.add_parser(name, help="action: %s on or off" % name).add_argument(
            "state", choices=("on", "off"))
    p = sub.add_parser("play", help="action: start a file (bed cleaning can spin the cutter at the end)")
    p.add_argument("name")
    p.add_argument("--dir", default="/sd/gcodes")
    sub.add_parser("pause", help="action: feed hold (!), does not stop motion on Z1 firmware 1.1.3")
    sub.add_parser("resume", help="action: cycle start (~)")
    sub.add_parser("abort", help="action: abort the running job")
    p = sub.add_parser("send", help="action: send one raw console line")
    p.add_argument("line")
    p.add_argument("--wait", action="store_true",
                   help="wait for the machine to start moving and settle back to Idle, for "
                        "macros such as probe cycles (uses --timeout)")
    return ap


def run(a):
    found = None
    if not a.host:
        found = discover()
        a.host, a.port = found.host, found.port
    if a.cmd == "find":
        if a.json:
            print(json.dumps(found._asdict() if found else {"host": a.host, "port": a.port}))
        elif found:
            print("%s at %s:%d, state %s%s" % (found.model, found.host, found.port, found.state,
                                              ", BUSY" if found.busy else ""))
        else:
            print("--host given, nothing to discover: %s:%d" % (a.host, a.port))
        return
    if a.cmd == "snapshot":
        print("%s  (%d bytes)" % (a.out, snapshot(a.host, a.out, a.resolution)))
        return
    if found and found.busy:
        raise MakeraError("the machine reports a client already connected. "
                          "Disconnect Makera Studio or any other client first.")
    for f in getattr(a, "files", []):
        if not os.path.isfile(f):
            raise MakeraError("no such file: %s" % f)

    sess = Session(a.host, a.port)
    try:
        st = sess.status()
        if a.cmd == "status":
            if a.json:
                print(json.dumps(st))
            else:
                show_status(st)
            return
        if a.cmd in ACTIONS:
            run_action(sess, a, st)
            return
        if st["state"] not in READ_STATES and not a.force:
            raise MakeraError("machine state is %s. %s waits for %s (or pass --force)."
                              % (st["state"], a.cmd, ", ".join(READ_STATES)))
        if a.cmd == "version":
            sess.command("version")
            for line in sess.lines():
                print(line)
        elif a.cmd == "wcs":
            sess.command("$#")
            wcs, other = parse_wcs(sess.lines())
            sess.command("get wcs")
            active = parse_active_wcs(sess.lines())
            if active:
                wcs["active"] = active
            if a.json:
                print(json.dumps(wcs))
            else:
                show_wcs(wcs, other)
        elif a.cmd == "diagnose":
            sess.command("diagnose")
            d = parse_diag(sess.wait_for("diag"))
            print(json.dumps(d) if a.json else "\n".join("%-6s %s" % kv for kv in d.items()))
        elif a.cmd == "ls":
            sess.command("ls -e -s %s" % path_arg(a.dir.rstrip("/") or "/"))
            rows = parse_ls(sess.load())
            if a.json:
                print(json.dumps(rows))
            else:
                for r in sorted(rows, key=lambda r: (not r["dir"], r["name"].lower())):
                    print("%10s  %s  %s%s" % ("<dir>" if r["dir"] else r["size"], r["date"],
                                              r["name"], "/" if r["dir"] else ""))
        elif a.cmd == "config":
            conf = fetch_config(sess, a.save)
            if a.keys:
                for k in a.keys:
                    if k not in conf:
                        print("not in config.txt: %s" % k, file=sys.stderr)
                conf = {k: conf[k] for k in a.keys if k in conf}
            if a.json:
                print(json.dumps(conf))
            else:
                for k, v in conf.items():
                    print("%-40s %s" % (k, v))
        elif a.cmd == "download":
            local = a.local or os.path.basename(a.remote)
            n = download(sess.link, a.remote, local)
            with open(local, "rb") as f:
                md5 = hashlib.md5(f.read()).hexdigest()
            print("%s -> %s  (%d bytes, md5 %s)" % (a.remote, local, n, md5))
        elif a.cmd == "upload":
            for f in a.files:
                upload(sess.link, f, a.dir, a.prefix)
    finally:
        sess.close()


def main(argv=None):
    a = build_parser().parse_args(argv)
    try:
        run(a)
    except MakeraError as e:
        print("makera: %s" % e, file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("makera: interrupted", file=sys.stderr)
        sys.exit(130)
