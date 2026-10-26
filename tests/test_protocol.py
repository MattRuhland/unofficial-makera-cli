import socket
import threading
import unittest

from makera_cli import MakeraError
from makera_cli._vendor.carvera.protocols.framing import (PTYPE_NORMAL_INFO, PTYPE_STATUS_RES,
                                                          build_frame)
from makera_cli.camera import read_frame, text_frame
from makera_cli.link import parse_announcement, split_frames
from makera_cli.protocol import (parse_active_wcs, parse_config, parse_diag, parse_ls,
                                 parse_status, parse_wcs, path_arg)

# Replies in the shapes a Z1 sends them.
STATUS = ("<Idle|MPos:-11.600,-14.600,-3.000,0.000|WPos:90.688,92.132,65.486,-50.000"
          "|F:0,2000,100|S:0,10000,100|T:1,6.525>")
WCS = [
    "[G54:-102.2881,-106.7319,-75.0109,50.0000,0.0000]",
    "[G55:0.0000,0.0000,0.0000,0.0000,0.0000]",
    "[G28:0.0000,0.0000,0.0000]",
    "[G92:0.0000,0.0000,0.0000,0.0000,0.0000]",
    "[TL0:6.5247]",
    "[PRB:-9.3000,-12.5100,-70.7144:1]",
    "ok",
]


class Framing(unittest.TestCase):
    def test_round_trip_and_partial_frame(self):
        buf = bytearray(build_frame(PTYPE_STATUS_RES, b"<Idle|MPos:0,0,0>"))
        second = build_frame(PTYPE_NORMAL_INFO, b"hello\n")
        buf += second[:5]  # an incomplete frame stays in the buffer
        frames = split_frames(buf)
        self.assertEqual(frames, [(PTYPE_STATUS_RES, b"<Idle|MPos:0,0,0>")])
        self.assertEqual(bytes(buf), second[:5])
        buf += second[5:]
        self.assertEqual(split_frames(buf), [(PTYPE_NORMAL_INFO, b"hello\n")])
        self.assertEqual(len(buf), 0)

    def test_corrupt_frame_is_dropped(self):
        frame = bytearray(build_frame(PTYPE_STATUS_RES, b"<Idle>"))
        frame[6] ^= 0xFF  # break the CRC
        self.assertEqual(split_frames(frame), [])


class Announcement(unittest.TestCase):
    def test_parse(self):
        m = parse_announcement(b"Makera_Z1,192.0.2.10,2222,0,Idle")
        self.assertEqual((m.model, m.host, m.port, m.busy, m.state),
                         ("Makera_Z1", "192.0.2.10", 2222, False, "Idle"))
        self.assertTrue(parse_announcement(b"Makera_Z1,192.0.2.10,2222,1,Run").busy)

    def test_ignores_other_traffic(self):
        self.assertIsNone(parse_announcement(b"something else"))
        self.assertIsNone(parse_announcement(b"Makera_Z1,192.0.2.10,notaport,0"))


class Parsers(unittest.TestCase):
    def test_status(self):
        s = parse_status(STATUS)
        self.assertEqual(s["state"], "Idle")
        self.assertEqual(s["mpos"], {"x": -11.6, "y": -14.6, "z": -3.0, "a": 0.0})
        self.assertEqual(s["tool"], 1)
        self.assertAlmostEqual(s["tlo"], 6.525)
        self.assertEqual(s["spindle"]["target"], 10000)
        self.assertNotIn("playing", s)

    def test_status_while_playing(self):
        s = parse_status("<Run|MPos:1,2,3|WPos:1,2,3|P:120,45,600,1>")
        self.assertEqual(s["playing"], {"line": 120, "percent": 45, "seconds": 600})

    def test_malformed_status(self):
        with self.assertRaises(MakeraError):
            parse_status("Idle|MPos:1,2,3")

    def test_wcs(self):
        wcs, other = parse_wcs(WCS)
        self.assertEqual(wcs["G54"]["x"], -102.2881)
        self.assertEqual(wcs["TLO"], 6.5247)
        self.assertEqual(wcs["PRB"], {"x": -9.3, "y": -12.51, "z": -70.7144, "ok": True})
        self.assertIn("G28", wcs)
        self.assertEqual(other, [])

    def test_wcs_with_rotation(self):
        wcs, _ = parse_wcs(["[G54:-123.68,-123.68,-123.68,-50,0.000,25.123]"])
        self.assertEqual(wcs["G54"]["rotation"], 25.123)

    def test_active_wcs(self):
        self.assertEqual(parse_active_wcs(["[current WCS: G55]", "ok"]), "G55")
        self.assertEqual(parse_active_wcs(["[current WCS: G59.2]"]), "G59.2")
        self.assertIsNone(parse_active_wcs(["ok"]))

    def test_diag(self):
        d = parse_diag("{S:0,5000|L:0,0|V:1,38|RSSI:-46}")
        self.assertEqual(d["S"], [0, 5000])
        self.assertEqual(d["RSSI"], [-46])

    def test_ls(self):
        rows = parse_ls(["job one.cnc 837428 20260914171848".replace("job one", "job\x01one"),
                         "Examples/ 0 20260601084912", ".hidden 1 1", "<Idle|MPos:0,0,0>"])
        self.assertEqual(rows, [
            {"name": "job one.cnc", "dir": False, "size": 837428, "date": "20260914171848"},
            {"name": "Examples", "dir": True, "size": 0, "date": "20260601084912"},
        ])

    def test_config(self):
        conf = parse_config("# comment\nsoft_endstop.x_min  -207.0  # note\n"
                            "soft_endstop.enable true\nflag\n")
        self.assertEqual(conf, {"soft_endstop.x_min": "-207.0",
                                "soft_endstop.enable": "true", "flag": ""})

    def test_path_arg(self):
        self.assertEqual(path_arg("/sd/gcodes/a b?&!~.nc"), "/sd/gcodes/a\x01b\x02\x03\x04\x05.nc")


class Camera(unittest.TestCase):
    def test_websocket_frames(self):
        a, b = socket.socketpair()
        try:
            a.sendall(text_frame("start_stream"))
            op, data = read_frame(b)  # client frames are masked, and read_frame unmasks
            self.assertEqual((op, data), (1, b"start_stream"))
            jpeg = b"\xff\xd8" + bytes(70000)
            # Larger than a socket buffer, so the writer needs its own thread.
            t = threading.Thread(target=a.sendall,
                                 args=(bytes([0x82, 127]) + len(jpeg).to_bytes(8, "big") + jpeg,))
            t.start()
            self.assertEqual(read_frame(b), (2, jpeg))
            t.join(5)
        finally:
            a.close()
            b.close()


if __name__ == "__main__":
    unittest.main()
