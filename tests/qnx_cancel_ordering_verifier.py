"""Exercise observational verdicts with newly generated snapshot fragments."""

from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from verify_qnx_cancel_ordering import observe, retirement
from verify_qnx_logs import TraceValidationError


class CancelOrderingEvidenceTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.stem = Path(directory.name) / "live"
        self.write("-before.threads", "43 2 REPLY 47\n")
        self.write("-after.threads", "43 2 REPLY 47\n")
        self.ordering()
        fds = " 0 1 input\n 1 1 output\n 2 1 errors\n 7s 46 worker\n"
        self.write("-baseline.fds", fds)
        self.write("-cancelled.fds", fds)
        self.write("-cancelled.processes", "1 procnto\n43 broker\n46 baseline\n")
        self.write("-finished.processes", "1 procnto\n")

    def write(self, extension, text):
        Path(f"{self.stem}{extension}").write_text(text)

    def ordering(self, before_tid=2, after_tid=2, watermark=21, final=21):
        self.write(".ordering.txt", f"broker=43 peer=47 before_tid={before_tid} after_tid={after_tid} "
                   f"before_observation={watermark} before_write={watermark} after_write={watermark} "
                   f"after_observation={final}\n")

    def test_same_live_exchange(self):
        self.assertEqual(observe(self.stem, 43, 47, 22), (47, True))

    def test_single_observation_is_inconclusive(self):
        self.write("-after.threads", "43 2 READY 0\n")
        self.ordering(after_tid=0)
        self.assertEqual(observe(self.stem, 43, 47, 22), (47, False))

    def test_sample_published_between_observations_is_inconclusive(self):
        self.ordering(final=22)
        self.assertEqual(observe(self.stem, 43, 47, 22), (47, False))

    def test_different_sender_is_inconclusive(self):
        self.write("-after.threads", "43 4 REPLY 47\n")
        self.ordering(after_tid=4)
        self.assertEqual(observe(self.stem, 43, 47, 22), (47, False))

    def test_pidin_must_support_written_claim(self):
        self.write("-after.threads", "43 2 REPLY 46\n")
        with self.assertRaisesRegex(TraceValidationError, "disagree"):
            observe(self.stem, 43, 47, 22)

    def test_resources_return_to_baseline(self):
        retirement(self.stem, 43, 46, 47, 48)

    def test_leaked_cancelled_connection_fails(self):
        initial = Path(f"{self.stem}-baseline.fds").read_text()
        self.write("-cancelled.fds", initial + " 8s 47 candidate\n")
        with self.assertRaisesRegex(TraceValidationError, "extra broker descriptors"):
            retirement(self.stem, 43, 46, 47, 48)

    def test_cancelled_worker_surviving_fails(self):
        self.write("-cancelled.processes", "1 procnto\n43 broker\n46 baseline\n47 candidate\n")
        with self.assertRaisesRegex(TraceValidationError, "cancelled worker survived"):
            retirement(self.stem, 43, 46, 47, 48)


if __name__ == "__main__":
    unittest.main()
