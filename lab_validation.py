"""Read-only stage-8 status and allowlisted, checksum-verified summary assets."""

import json
from pathlib import Path

from flylab import ROOT, digest
from flywire_data import file_sha256


DIRECTORY = ROOT / "outputs/flylab/validation-stage8"
PLOTS = {"calibration": "calibration.png", "ablations": "ablations.png"}


def status(directory=None):
    directory = Path(directory) if directory is not None else DIRECTORY
    manifest_path = directory / "manifest.json"
    if not manifest_path.exists():
        return {"status": "not_started", "completed": 0, "planned": None, "summary": None}
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if digest(manifest["identity"]) != manifest["fingerprint"]:
        raise ValueError("Validation manifest checksum mismatch")
    config = manifest["identity"]["configuration"]
    planned = len(config["training_seeds"]) * len(config["stride_reductions"]) * 3 + len(config["validation_seeds"]) * 16 + 2
    result = {"status": manifest["status"], "completed": len(manifest["completed"]), "planned": planned, "summary": None}
    if manifest["status"] not in ("passed", "failed") or "summary_sha256" not in manifest:
        return result
    path = directory / "summary.json"
    if file_sha256(path) != manifest["summary_sha256"]:
        raise ValueError("Validation summary checksum mismatch")
    summary = json.loads(path.read_text(encoding="utf-8"))
    if summary["fingerprint"] != manifest["fingerprint"] or summary["status"] != manifest["status"]:
        raise ValueError("Validation summary identity mismatch")
    result["summary"] = summary
    return result


def plot(name, directory=None):
    if name not in PLOTS:
        raise ValueError("Unknown validation plot")
    directory = Path(directory) if directory is not None else DIRECTORY
    summary = status(directory)["summary"]
    if summary is None:
        raise FileNotFoundError("Validation summary not complete")
    path = directory / PLOTS[name]
    if file_sha256(path) != summary["artifact_sha256"][path.name]:
        raise ValueError("Validation plot checksum mismatch")
    return path
