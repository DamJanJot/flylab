"""Read-only validation panel refuses incomplete or altered scientific reports."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from flylab import digest, write_json
from flywire_data import file_sha256
import lab_validation
from lab_launch import is_lab


class ValidationPanelTests(unittest.TestCase):
    def fixture(self, root, state="running"):
        config = {"training_seeds": [101, 102], "validation_seeds": [201, 202, 203], "stride_reductions": [.35, .5, .65]}
        identity = {"configuration": config}
        manifest = {"identity": identity, "fingerprint": digest(identity), "status": state, "completed": {"trial": "hash"}}
        write_json(root / "manifest.json", manifest)
        return manifest

    def test_missing_and_partial_report(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.assertEqual(lab_validation.status(root)["status"], "not_started")
            self.fixture(root)
            report = lab_validation.status(root)
            self.assertEqual(report["planned"], 68)
            self.assertEqual(report["completed"], 1)
            self.assertIsNone(report["summary"])
            with self.assertRaises(FileNotFoundError):
                lab_validation.plot("calibration", root)

    def test_summary_and_plot_integrity(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest = self.fixture(root, "passed")
            write_json(root / "calibration.png", {"test_fixture": True})
            summary = {"fingerprint": manifest["fingerprint"], "status": "passed",
                       "artifact_sha256": {"calibration.png": file_sha256(root / "calibration.png")}}
            write_json(root / "summary.json", summary)
            manifest["summary_sha256"] = file_sha256(root / "summary.json")
            write_json(root / "manifest.json", manifest)
            self.assertEqual(lab_validation.status(root)["summary"], summary)
            self.assertEqual(lab_validation.plot("calibration", root), root / "calibration.png")
            write_json(root / "calibration.png", {"tampered": True})
            with self.assertRaisesRegex(ValueError, "plot checksum"):
                lab_validation.plot("calibration", root)
            write_json(root / "summary.json", {})
            with self.assertRaisesRegex(ValueError, "summary checksum"):
                lab_validation.status(root)

    def test_manifest_and_path_validation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest = self.fixture(root)
            manifest["identity"]["configuration"]["training_seeds"].append(103)
            write_json(root / "manifest.json", manifest)
            with self.assertRaisesRegex(ValueError, "manifest checksum"):
                lab_validation.status(root)
            for name in ("../manifest.json", "calibration.png", "file", ""):
                with self.assertRaisesRegex(ValueError, "Unknown validation plot"):
                    lab_validation.plot(name, root)

    def test_launcher_requires_current_server_version(self):
        from io import BytesIO
        for version, expected in ((7, False), (8, True)):
            with patch("lab_launch.urlopen", return_value=BytesIO(json.dumps({"app": "flylab", "version": version}).encode())):
                self.assertEqual(is_lab("http://127.0.0.1:8767"), expected)


if __name__ == "__main__":
    unittest.main()
