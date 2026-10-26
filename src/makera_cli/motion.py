"""Planning and safety checks for commands that act on the machine.

``plan()`` turns an action into the console lines it would send. It reads machine status and
soft limits and sends nothing, so every safety check can be tested offline.

Coordinates: on the Z1, home is 0 on every axis and travel runs negative, so the soft-limit
box is ``soft_endstop.<axis>_min`` to 0. The offset between machine and work coordinates comes
from the status report and already includes the active tool length offset.
"""

import math
import re
import sys
import time

from makera_cli import MakeraError
from makera_cli.protocol import parse_status, path_arg

SAFE_Z = -2.0  # machine Z for travel moves, as in the community controller's gotoSafeZ
LASER_TOOL = 8888
NO_TOOL = -1
OUTPUTS = {
    "light": ("M821", "M822"),
    "vacuum": ("M801 S100", "M802"),
    "ext": ("M851 S100", "M852"),
    "air": ("M7", "M9"),
}
PARK = {"clearance": "M496.1", "origin": "M496.2", "anchor1": "M496.3", "anchor2": "M496.4"}

# Printed with the plan, in dry runs and real runs alike, for actions whose risk the safety
# checks cannot cover.
WARNINGS = {
    "play": "WARNING: the cutter can spin at the end of this job. When playback ends, the Z1 "
            "runs its automatic bed cleaning (config bed_cleaning.enable). It moves to the front "
            "of the bed and can spin the loaded tool, even if the file has no M3. Keep clear of "
            "the machine and keep the stop button in reach.",
    "pause": "WARNING: on Z1 firmware 1.1.3.0.1.13 a feed hold does not stop motion. The state "
             "reads Hold, but the axes keep moving to the end of the queued moves. Do not rely "
             "on this to stop the machine. Use its stop button.",
    "send": "WARNING: raw lines get no safety checks. A line can move the machine or spin the "
            "spindle.",
    "spindle": "WARNING: the spindle will spin.",
}


def is_probe_tool(tool):
    """Tool numbers the firmware treats as a 3D probe."""
    return tool in (0, 9999) or tool >= 999990


def soft_limits(conf):
    """{axis: (min, max)} in machine coordinates from config.txt, or None when they are off."""
    if conf.get("soft_endstop.enable", "").lower() != "true":
        return None
    return {ax: (float(conf["soft_endstop.%s_min" % ax]), 0.0)
            for ax in "xyz" if "soft_endstop.%s_min" % ax in conf}


def work_to_machine(st, work):
    """Machine coordinates for work coordinates {axis: value}.

    Uses the offset and rotation (``R``) from the status report, the same way the community
    controller derives the work offset. Only the axes given are returned.
    """
    m, w = st["mpos"], st["wpos"]
    r_raw = st["raw"].get("R")
    r = math.radians(r_raw[0] if isinstance(r_raw, list) else 0.0)
    ox = m["x"] - (math.cos(r) * w["x"] - math.sin(r) * w["y"])
    oy = m["y"] - (math.sin(r) * w["x"] + math.cos(r) * w["y"])
    wx, wy = work.get("x", w["x"]), work.get("y", w["y"])
    out = {"x": ox + math.cos(r) * wx - math.sin(r) * wy,
           "y": oy + math.sin(r) * wx + math.cos(r) * wy,
           "z": m["z"] - w["z"] + work.get("z", w["z"])}
    return {k: out[k] for k in work}


def limit_errors(limits, target):
    """One message per axis of a machine-coordinate target outside the soft limits."""
    if limits is None:
        return []
    return ["%s %.3f is outside the soft limits %.1f to %.1f" % (ax.upper(), v, *limits[ax])
            for ax, v in target.items()
            if ax in limits and not limits[ax][0] <= round(v, 4) <= limits[ax][1]]


def parse_axes(words, allowed="xyza"):
    """``['X10', 'y-5']`` as ``{'x': 10.0, 'y': -5.0}``."""
    out = {}
    for w in words:
        m = re.fullmatch(r"([A-Za-z])(-?\d+(?:\.\d+)?)", w)
        if not m or m.group(1).lower() not in allowed:
            raise MakeraError("bad axis word %r: use forms like X10 or Z-2.5 (axes %s)"
                              % (w, allowed.upper()))
        out[m.group(1).lower()] = float(m.group(2))
    return out


def axes_words(d):
    """``{'x': 10.0, 'y': -5.5}`` as ``X10Y-5.5``."""
    return "".join("%s%s" % (k.upper(), ("%.4f" % v).rstrip("0").rstrip(".")) for k, v in d.items())


def plan(a, st, limits):
    """The console lines an action sends, as [(line, moves)], after every safety check.

    ``a`` is the parsed command line, ``st`` a status dict from ``parse_status`` and
    ``limits`` the result of ``soft_limits``. Raises MakeraError when a check fails.
    """
    c = a.cmd
    if c == "home":
        return [("$H", True)]
    if c == "unlock":
        return [("$X", False)]
    if c == "park":
        return [(PARK[a.where], True)]
    if c == "jog":
        d = parse_axes(a.axes)
        target = {k: st["mpos"][k] + v for k, v in d.items() if k in "xyz"}
        errs = limit_errors(limits, target)
        if errs:
            raise MakeraError("jog refused: " + "; ".join(errs))
        feed = " F%g" % a.feed if a.feed else ""
        return [("$J %s%s" % (axes_words({k: v}), feed), True) for k, v in d.items()]
    if c == "goto":
        d = parse_axes(a.axes, "xyz")
        if not d:
            raise MakeraError("goto needs at least one of X, Y, Z")
        xy = {k: v for k, v in d.items() if k in "xy"}
        target = dict(d) if a.machine else work_to_machine(st, d)
        errs = limit_errors(limits, target)
        if errs:
            raise MakeraError("goto refused: " + "; ".join(errs))
        prefix = "G53 G0 " if a.machine else "G90 G0 "
        steps = []
        if not a.direct and xy and st["mpos"]["z"] < SAFE_Z:
            steps.append(("G53 G0 Z%g" % SAFE_Z, True))
        if xy:
            steps.append((prefix + axes_words(xy), True))
        if "z" in d:
            steps.append((prefix + axes_words({"z": d["z"]}), True))
        return steps
    if c == "zero":
        axes = a.axes.lower()
        if not axes or set(axes) - set("xyza"):
            raise MakeraError("zero takes axis letters, for example xy or z")
        return [("G10L20P0" + axes_words({k: a.value for k in axes}), False)]
    if c == "spindle":
        if a.state == "off":
            return [("M5", False)]
        tool = st.get("tool", NO_TOOL)
        if tool in (NO_TOOL, LASER_TOOL) or is_probe_tool(tool):
            raise MakeraError("spindle refused: the active tool is T%d (probe, laser or none)" % tool)
        if not a.rpm or a.rpm <= 0:
            raise MakeraError("spindle on needs an rpm, for example: spindle on 12000")
        return [("M3 S%d" % a.rpm, False)]
    if c in OUTPUTS:
        return [(OUTPUTS[c][0 if a.state == "on" else 1], False)]
    if c == "play":
        return [("play %s" % path_arg("%s/%s" % (a.dir.rstrip("/"), a.name)), False)]
    if c == "abort":
        return [("abort", False)]
    if c == "send":
        return [(a.line, False)]
    raise MakeraError("no plan for %s" % c)


def wait_idle(sess, timeout=120.0):
    """Poll status until the machine is Idle after a move, and return the last status.

    Machine messages are printed to stderr. Alarm ends the wait with an error. Ctrl-C stops
    waiting and sends nothing: on Z1 firmware 1.1.3.0.1.13 a feed hold does not stop motion,
    so sending one would only give false confidence. Stopping the machine is a physical action.
    """
    end, idle_seen, st = time.time() + timeout, 0, None
    time.sleep(0.3)
    try:
        while time.time() < end:
            sess.realtime("?")
            t = time.time() + 0.5
            while time.time() < t:
                for kind, text in sess.read(0.1):
                    if kind == "status":
                        st = parse_status(text)
                    elif text:
                        print("machine: %s" % text, file=sys.stderr)
            if st and st["state"] == "Alarm":
                raise MakeraError("the machine went to Alarm. Check it, then run: makera --yes unlock")
            idle_seen = idle_seen + 1 if st and st["state"] == "Idle" else 0
            if idle_seen >= 2:
                return st
    except KeyboardInterrupt:
        raise MakeraError("stopped waiting. Nothing was sent, and the machine may still be "
                          "moving. Use the machine's stop button to stop it.")
    raise MakeraError("still not Idle after %.0f s (state %s)" % (timeout, st and st["state"]))
