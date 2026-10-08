"""Read-only archive adapter and bounded requests for the local laboratory."""

import copy
import json
from pathlib import Path
import re
import time

import numpy as np

from flylab import ROOT, write_json
from flywire_data import file_sha256
from behavior_model import Trial, SCENARIOS, CONDITIONS, STIMULATIONS, validate_suite
from behavior_experiment import load_verified_result
from closed_loop_model import CELLS

ARCHIVE = ROOT / "outputs/flylab/behavior-stage6"
JOBS = ROOT / "outputs/flylab/lab-stage7"


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_live_json(path, value):
    """Windows readers can briefly deny replacement; never expose partial JSON."""
    pending = path.with_suffix(".pending.json")
    write_json(pending, value)
    for attempt in range(50):
        try:
            pending.replace(path)
            return
        except PermissionError:
            if attempt == 49:
                raise
            time.sleep(0.02)


def experiment_request(request):
    fields = {"scenario", "condition", "seed", "duration_s", "stimulation_hz"}
    if not isinstance(request, dict) or set(request) != fields:
        raise ValueError("Niepoprawne pola eksperymentu")
    if request["scenario"] not in SCENARIOS or request["condition"] not in CONDITIONS + STIMULATIONS:
        raise ValueError("Nieznany scenariusz lub warunek")
    if request["condition"] in STIMULATIONS and request["scenario"] != "neutral":
        raise ValueError("Pobudzenie DNa02 wymaga areny neutralnej")
    seed = request["seed"]
    if type(seed) is not int or not 0 <= seed < 2**32:
        raise ValueError("Seed musi byc liczba uint32")
    if type(request["duration_s"]) not in (float, int) or request["duration_s"] not in (0.8, 1.0, 1.5, 2.0):
        raise ValueError("Dozwolony czas: 0.8, 1, 1.5 lub 2 s")
    if type(request["stimulation_hz"]) not in (float, int) or not 50 <= request["stimulation_hz"] <= 300:
        raise ValueError("Pobudzenie musi miescic sie w 50..300 Hz")
    config = copy.deepcopy(read_json(ROOT / "experiments/behavior-suite.json"))
    config["simulation"]["sensors"].update(seed=seed, duration_s=request["duration_s"], experiment_id="lab-stage7")
    config["seeds"] = [seed, (seed + 1) % 2**32]
    config["stimulation_hz"] = request["stimulation_hz"]
    validate_suite(config)
    return config, Trial(request["scenario"], request["condition"], seed)


def safe_key(key):
    if not isinstance(key, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,100}", key):
        raise ValueError("Niepoprawny identyfikator")
    return key


class Catalog:
    def __init__(self, archive=ARCHIVE, jobs=JOBS):
        self.archive, self.jobs = Path(archive), Path(jobs)

    def locate(self, identifier, verify=True):
        safe_key(identifier)
        if identifier.startswith("archive_"):
            key = identifier[len("archive_"):]
            manifest = read_json(self.archive / "manifest.json")
            if key not in manifest["completed"]:
                raise FileNotFoundError("Nieznana proba")
            directory = self.archive / "trials" / key
            checksum = manifest["completed"][key]
        elif identifier.startswith("run_"):
            directory = self.jobs / identifier
            completion = read_json(directory / "complete.json")
            directory = directory / "trials" / safe_key(completion["trial_key"])
            checksum = completion["result_sha256"]
        else:
            raise FileNotFoundError("Nieznany zapis")
        result = load_verified_result(directory, checksum) if verify else read_json(directory / "result.json")
        return directory, result

    def media(self, identifier):
        directory, result = self.locate(identifier)
        original = directory / "preview.mp4"
        if "preview.mp4" in result["files"]:
            return original
        derived = self.jobs / ("media_" + safe_key(identifier))
        if (derived / "complete.json").exists():
            done = read_json(derived / "complete.json")
            if done["source_sha256"] != file_sha256(directory / "result.json"):
                raise ValueError("Zmieniono zrodlo filmu")
            target = derived / "preview.mp4"
            if file_sha256(target) != done["video_sha256"]:
                raise ValueError("Niepoprawna suma filmu")
            return target
        return None

    def entries(self):
        manifest = read_json(self.archive / "manifest.json")
        ids = ["archive_" + key for key in manifest["completed"]]
        ids += [p.parent.name for p in sorted(self.jobs.glob("run_*/complete.json"), reverse=True)]
        rows = []
        for identifier in ids:
            _, result = self.locate(identifier)
            rows.append({"id": identifier, **result["trial"], "metrics": result["behavior"],
                         "has_video": "preview.mp4" in result["files"] or (self.jobs / ("media_" + identifier) / "complete.json").exists()})
        return rows

    def detail(self, identifier):
        directory, result = self.locate(identifier)
        trial = Trial(**result["trial"])
        config = read_json(directory / "trial.json")["configuration"]
        mode = trial.condition if trial.condition in CONDITIONS else "connected"
        with np.load(directory / mode / "recording.npz", allow_pickle=False) as data:
            payload = recording_payload(data)
        return {"id": identifier, "trial": result["trial"], "metrics": result["behavior"],
                "checks": result["checks"], "configuration": config, "recording": payload,
                "has_video": self.media(identifier) is not None,
                "result_sha256": file_sha256(directory / "result.json"), "biologically_validated": False}


def recording_payload(data):
    # Preserve observation vs interval clocks. Membrane samples belong to bin END.
    keys = ("time_s", "bin_start_s", "bin_end_s", "panels", "contacts", "input_rates_hz", "counts",
            "neuron_ids", "input_ids", "spike_times_s", "spike_indices", "applied_command", "next_command",
            "filtered_dn_hz", "joint_rad", "joint_velocity_rad_s")
    result = {key: data[key].tolist() for key in keys}
    result["position_mm"] = (data["thorax_m"] * 1000).tolist()
    result["voltage_mv"] = (data["voltage_v"] * 1000).tolist()
    result["heading_deg"] = np.rad2deg(data["heading_rad"] - data["heading_rad"][0]).tolist()
    result["eye_mean"] = data["ommatidia"].mean(axis=(2, 3)).tolist()
    # Ommatidium index is an index, not an invented anatomical retina layout.
    result["retina"] = data["ommatidia"].mean(axis=3).tolist()
    labels = {root: f"{name} {'L' if side == 'left' else 'R'}" for root, name, side in CELLS}
    result["neuron_labels"] = [labels[str(root)] for root in data["neuron_ids"]]
    result["input_labels"] = [labels[str(root)] for root in data["input_ids"]]
    return result
