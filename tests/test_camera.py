import struct
import unittest

from makera_cli import MakeraError
from makera_cli.camera import jpeg_size, stable_frame


def jpeg(width, height, pad=1000):
    """A minimal JPEG header with a start-of-frame marker, padded to about ``pad`` bytes."""
    app0 = b"\xff\xe0" + struct.pack(">H", 4) + b"\x00\x00"
    sof = b"\xff\xc0" + struct.pack(">HBHH", 8, 8, height, width) + b"\x00"
    return b"\xff\xd8" + app0 + sof + b"\xff\xda" + b"\x00" * pad


LOW, HIGH = jpeg(640, 480, 15000), jpeg(1600, 1200, 72000)
# As measured: the header gives the new size while the data is still the size of an old frame.
BAD = jpeg(640, 480, 72000)


class JpegSize(unittest.TestCase):
    def test_reads_size(self):
        self.assertEqual(jpeg_size(LOW), (640, 480))
        self.assertEqual(jpeg_size(HIGH), (1600, 1200))

    def test_unreadable(self):
        self.assertIsNone(jpeg_size(b"\xff\xd8"))
        self.assertIsNone(jpeg_size(b"\xff\xd8\xff\xda" + b"\x00" * 20))


class StableFrame(unittest.TestCase):
    def test_skips_first_frames(self):
        first = jpeg(640, 480, 15001)
        frames = [first, first, first, LOW, jpeg(640, 480, 15002)]
        self.assertEqual(stable_frame(frames), LOW)

    def test_waits_for_requested_size(self):
        self.assertEqual(stable_frame([LOW] * 4 + [HIGH] * 3, (1600, 1200)), HIGH)

    def test_rejects_bad_frame_after_size_change(self):
        good = jpeg(640, 480, 15001)
        # BAD has the wanted size, and the frame after it differs too much in bytes.
        got = stable_frame([HIGH] * 4 + [BAD, LOW, good], (640, 480))
        self.assertEqual(got, good)

    def test_rejects_bad_frame_without_a_request(self):
        got = stable_frame([BAD, BAD, BAD, LOW, LOW])
        self.assertEqual(got, LOW)

    def test_wrong_size_is_an_error(self):
        with self.assertRaisesRegex(MakeraError, "did not change to 1600x1200.*640x480"):
            stable_frame([LOW] * 10, (1600, 1200))

    def test_no_stable_frame_is_an_error(self):
        with self.assertRaisesRegex(MakeraError, "no stable frame"):
            stable_frame([LOW, HIGH] * 5)
