import unittest

from makera_cli import MakeraError
from makera_cli.cli import build_parser
from makera_cli.motion import (WARNINGS, limit_errors, plan, soft_limits, wait_idle,
                               work_to_machine)
from makera_cli.protocol import parse_status

STATUS = ("<Idle|MPos:-11.600,-14.600,-3.000,0.000|WPos:90.688,92.132,65.486,-50.000"
          "|F:0,2000,100|S:0,10000,100|T:1,6.525>")
CONFIG = {"soft_endstop.enable": "true", "soft_endstop.x_min": "-207.0",
          "soft_endstop.y_min": "-206.0", "soft_endstop.z_min": "-102.0"}


def lines(*argv, status=STATUS, config=CONFIG):
    a = build_parser().parse_args(list(argv))
    return [line for line, _ in plan(a, parse_status(status), soft_limits(config))]


class Limits(unittest.TestCase):
    def test_soft_limits(self):
        self.assertEqual(soft_limits(CONFIG),
                         {"x": (-207.0, 0.0), "y": (-206.0, 0.0), "z": (-102.0, 0.0)})
        self.assertIsNone(soft_limits({"soft_endstop.enable": "false"}))

    def test_limit_errors(self):
        lim = soft_limits(CONFIG)
        self.assertEqual(limit_errors(lim, {"x": -207.0, "y": 0.0}), [])
        self.assertEqual(len(limit_errors(lim, {"x": -207.1, "z": 0.5})), 2)
        self.assertEqual(limit_errors(None, {"x": 500}), [])


class Coordinates(unittest.TestCase):
    def test_work_origin(self):
        # Work offset = MPos - WPos. On the Z1 the Z part includes the tool length offset.
        m = work_to_machine(parse_status(STATUS), {"x": 0, "y": 0, "z": 0})
        self.assertAlmostEqual(m["x"], -102.288)
        self.assertAlmostEqual(m["y"], -106.732)
        self.assertAlmostEqual(m["z"], -68.486)

    def test_rotated_wcs(self):
        st = parse_status("<Idle|MPos:-100,-100,-3|WPos:10,0,5|R:90>")
        here = work_to_machine(st, {"x": 10, "y": 0})
        self.assertAlmostEqual(here["x"], -100)
        self.assertAlmostEqual(here["y"], -100)
        origin = work_to_machine(st, {"x": 0, "y": 0})
        self.assertAlmostEqual(origin["x"], -100)
        self.assertAlmostEqual(origin["y"], -110)


class Plans(unittest.TestCase):
    def test_jog(self):
        self.assertEqual(lines("jog", "X-10", "Y5"), ["$J X-10", "$J Y5"])
        self.assertEqual(lines("jog", "Z1.5", "--feed", "300"), ["$J Z1.5 F300"])

    def test_jog_past_limit_is_refused(self):
        with self.assertRaisesRegex(MakeraError, "Z -103.000 is outside"):
            lines("jog", "Z-100")
        with self.assertRaises(MakeraError):
            lines("jog", "X12")  # machine X would become +0.4, above the 0 max
        self.assertEqual(lines("jog", "X11.6"), ["$J X11.6"])  # exactly 0 is allowed

    def test_goto_raises_z_first(self):
        self.assertEqual(lines("goto", "X0", "Y0", "Z5"),
                         ["G53 G0 Z-2", "G90 G0 X0Y0", "G90 G0 Z5"])
        self.assertEqual(lines("goto", "--direct", "X0", "Y0"), ["G90 G0 X0Y0"])
        self.assertEqual(lines("goto", "Z10"), ["G90 G0 Z10"])

    def test_goto_machine(self):
        self.assertEqual(lines("goto", "--machine", "X-150", "Y-100"),
                         ["G53 G0 Z-2", "G53 G0 X-150Y-100"])

    def test_goto_outside_limits_is_refused(self):
        with self.assertRaisesRegex(MakeraError, "X 97.712 is outside"):
            lines("goto", "X200")
        with self.assertRaises(MakeraError):
            lines("goto", "--machine", "X-250")

    def test_goto_rejects_a_axis(self):
        with self.assertRaises(MakeraError):
            lines("goto", "A90")

    def test_zero(self):
        self.assertEqual(lines("zero", "xy"), ["G10L20P0X0Y0"])
        self.assertEqual(lines("zero", "z", "--value", "12.5"), ["G10L20P0Z12.5"])
        with self.assertRaises(MakeraError):
            lines("zero", "q")

    def test_spindle(self):
        self.assertEqual(lines("spindle", "on", "12000"), ["M3 S12000"])
        self.assertEqual(lines("spindle", "off"), ["M5"])
        with self.assertRaises(MakeraError):
            lines("spindle", "on")

    def test_spindle_refused_for_probe_laser_or_no_tool(self):
        for tool in (0, 9999, 999990, 8888, -1):
            status = STATUS.replace("T:1,6.525", "T:%d,0" % tool)
            with self.assertRaises(MakeraError, msg="tool %d" % tool):
                lines("spindle", "on", "12000", status=status)

    def test_outputs_and_job_control(self):
        self.assertEqual(lines("light", "on"), ["M821"])
        self.assertEqual(lines("light", "off"), ["M822"])
        self.assertEqual(lines("home"), ["$H"])
        self.assertEqual(lines("park", "clearance"), ["M496.1"])
        self.assertEqual(lines("abort"), ["abort"])
        self.assertEqual(lines("play", "my job.nc"), ["play /sd/gcodes/my\x01job.nc"])
        self.assertEqual(lines("send", "G4 P1"), ["G4 P1"])


class Interrupt(unittest.TestCase):
    def test_ctrl_c_sends_nothing(self):
        # A feed hold does not stop motion on the Z1, so Ctrl-C must not pretend it does.
        class Fake:
            sent = []

            def realtime(self, char):
                self.sent.append(char)
                if char == "?":
                    raise KeyboardInterrupt

        fake = Fake()
        with self.assertRaisesRegex(MakeraError, "stop button"):
            wait_idle(fake, timeout=5)
        self.assertEqual(fake.sent, ["?"])


class Warnings(unittest.TestCase):
    def test_risky_actions_warn(self):
        self.assertIn("bed cleaning", WARNINGS["play"])
        self.assertIn("does not stop motion", WARNINGS["pause"])
        self.assertIn("spindle", WARNINGS["send"])
        self.assertIn("spindle", WARNINGS["spindle"])


if __name__ == "__main__":
    unittest.main()
