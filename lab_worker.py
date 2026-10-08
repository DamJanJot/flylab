"""Isolated Brian2/MuJoCo process; pause only at complete control intervals."""

import argparse
from dataclasses import asdict
import importlib.metadata
from pathlib import Path
import time

import numpy as np

from flylab import ROOT, write_json
from flywire_data import Connectome, file_sha256
from behavior_experiment import (BehaviorRig, CODE_FILES, save_video,
                                technical_checks)
from behavior_model import TrialProtocol, CONDITIONS, behavior_metrics, validate_suite
from closed_loop_experiment import episode
from brain_model import ModelParameters
from lab_data import ARCHIVE, Catalog, read_json, experiment_request, write_live_json as write_checkpoint


class Control:
    def __init__(self, directory):
        self.directory = directory
        self.progress = 0.0

    def status(self, state, progress=None, **extra):
        if progress is not None:
            self.progress = progress
        write_checkpoint(self.directory / "status.json", {"state": state, "progress": self.progress, **extra})

    def checkpoint(self, progress):
        self.progress = progress
        while True:
            action = read_json(self.directory / "control.json")["action"]
            if action == "cancel":
                raise InterruptedError("Anulowano obliczenia")
            if action != "pause":
                self.status("running")
                return
            self.status("paused")
            time.sleep(0.1)


def load_graph():
    manifest = read_json(ARCHIVE / "manifest.json")
    for name in ("mapping.json", "circuit.npz"):
        if file_sha256(ARCHIVE / name) != manifest["source_artifacts"][name]:
            raise ValueError("Archive circuit checksum mismatch")
    with np.load(ARCHIVE / "circuit.npz", allow_pickle=False) as data:
        graph = Connectome(*(data[key].copy() for key in ("ids", "nt", "pre", "post", "counts")),
                           {"source": "verified stage-6 circuit", "complete_brain": False}).validate()
    return graph, read_json(ARCHIVE / "mapping.json")


def simulate(directory, request, control):
    config, trial = experiment_request(request)
    settings, parameters = validate_suite(config)
    settings = {**settings, "seed": trial.seed}
    graph, mapping = load_graph()
    target = directory / "trials" / trial.key
    target.mkdir(parents=True)
    write_json(target / "trial.json", {"trial": asdict(trial), "configuration": config})
    write_json(directory / "provenance.json", {
        "stage": 7, "scope": "single exploratory trial, not a paired suite", "configuration": config,
        "mapping": mapping, "parameters": asdict(ModelParameters()),
        "source_circuit_sha256": file_sha256(ARCHIVE / "circuit.npz"),
        "versions": {name: importlib.metadata.version(name) for name in ("flygym", "mujoco", "brian2", "numpy", "imageio")},
        "code_sha256": {name: file_sha256(ROOT / name) for name in CODE_FILES + ("lab_worker.py", "lab_data.py", "lab_server.py")}})
    rig = BehaviorRig(settings, config["obstacle"] if trial.scenario == "obstacle" else None)
    steps = 0
    stride = round(settings["sensory_dt_s"] / settings["physics_dt_s"])
    total = round(settings["duration_s"] / settings["physics_dt_s"])

    def observe():
        nonlocal steps
        rig.record_physics()
        steps += 1
        if steps % stride == 0:
            control.checkpoint(steps / total * 0.9)

    try:
        control.checkpoint(0)
        mode = trial.condition if trial.condition in CONDITIONS else "connected"
        state, metrics, frames = episode(rig, graph, parameters, mode, target, capture=True,
                                        protocol=TrialProtocol(config, trial), physics_observer=observe)
        physics = rig.physics_arrays()
        warnings = np.asarray(rig.sim.mj_data.warning.number).astype(int).tolist()
        pairs = rig.obstacle_pairs
    finally:
        rig.close()
    control.checkpoint(0.92)
    control.status("encoding")
    np.savez_compressed(target / "physics.npz", **physics)
    checks = technical_checks(state, physics, trial, config, warnings)
    checks["rendered_views_nonblank_and_changing"] = save_video(frames, target, trial, settings["sensory_dt_s"])["passed"]
    if trial.scenario == "obstacle":
        checks["obstacle_has_collision_pairs"] = pairs > 0
    files = ["trial.json", "physics.npz", f"{mode}/recording.npz", "preview.mp4", "preview.png", "media-validation.json"]
    write_json(target / "result.json", {
        "trial": asdict(trial), "technical_status": "passed" if all(checks.values()) else "failed", "checks": checks,
        "behavior": behavior_metrics(state, physics, config, trial.scenario), "mujoco_warnings": warnings,
        "episode": metrics, "obstacle_collision_pairs": pairs, "files": {name: file_sha256(target / name) for name in files}})
    if not all(checks.values()):
        raise ValueError("Kontrole techniczne proby nie przeszly")
    control.checkpoint(1)
    write_checkpoint(directory / "complete.json", {"trial_key": trial.key, "result_sha256": file_sha256(target / "result.json")})
    control.status("completed", 1, recording_id=directory.name)


def render_recording(directory, identifier, control):
    import mujoco

    catalog = Catalog()
    source, result = catalog.locate(identifier)
    config = read_json(source / "trial.json")["configuration"]
    settings = {**config["simulation"]["sensors"], "seed": result["trial"]["seed"]}
    mode = result["trial"]["condition"] if result["trial"]["condition"] in CONDITIONS else "connected"
    with np.load(source / mode / "recording.npz", allow_pickle=False) as data:
        poses, panels = data["qpos_native"], data["panels"]
    rig = BehaviorRig(settings, config["obstacle"] if result["trial"]["scenario"] == "obstacle" else None)
    frames = []
    try:
        for index, pose in enumerate(poses[:-1]):
            control.checkpoint(index / (len(poses) - 1) * 0.9)
            rig.sim.mj_data.qpos[:] = pose
            rig.sim.mj_data.time = settings["warmup_s"] + index * settings["sensory_dt_s"]
            rig.set_panels(*panels[index])
            mujoco.mj_forward(rig.sim.mj_model, rig.sim.mj_data)
            frames.append(rig.frame())
    finally:
        rig.close()
    from behavior_model import Trial
    control.status("encoding", 0.92)
    media = save_video(frames, directory, Trial(**result["trial"]), settings["sensory_dt_s"])
    if not media["passed"]:
        raise ValueError("Kontrola obrazu nie przeszla")
    control.checkpoint(1)
    write_checkpoint(directory / "complete.json", {
        "source_sha256": file_sha256(source / "result.json"), "video_sha256": file_sha256(directory / "preview.mp4"),
        "method": "MuJoCo render of recorded qpos and panel states; no physics or brain rerun",
        "code_sha256": file_sha256(ROOT / "lab_worker.py")})
    control.status("completed", 1, recording_id=identifier)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    directory = args.directory
    control = Control(directory)
    try:
        request = read_json(directory / "request.json")
        control.status("preparing", 0)
        if request["kind"] == "simulate":
            simulate(directory, request["parameters"], control)
        else:
            render_recording(directory, request["recording_id"], control)
    except InterruptedError:
        control.status("cancelled")
    except Exception:
        control.status("failed", error="Blad obliczen; szczegoly w worker.log w katalogu proby")
        raise


if __name__ == "__main__":
    main()
