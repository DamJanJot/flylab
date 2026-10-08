"""Stage 8: frozen training selection, held-out controls and isolated-process benchmarks."""

import argparse
import copy
from dataclasses import asdict
import importlib.metadata
import json
from pathlib import Path
import platform
import subprocess
import sys
import time

import numpy as np
import psutil

from flylab import ROOT, digest, write_json
from flywire_data import Connectome, file_sha256
from brain_model import ModelParameters
from closed_loop_model import load_steering_circuit
from closed_loop_experiment import episode
from behavior_model import Trial, TrialProtocol, behavior_metrics, validate_suite
from behavior_experiment import BehaviorRig, CODE_FILES, technical_checks, load_verified_result, write_checkpoint
from validation_model import Assay, ABLATIONS, SCENARIOS, validate_config, training_plan, evaluation_plan, select_candidate, objective, without_feedback


SOURCE_FILES = (*CODE_FILES, "brain_experiment.py", "validation_model.py", "validation_experiment.py")


def verified_files(directory, files):
    for name, checksum in files.items():
        path = (directory / name).resolve()
        if not path.is_relative_to(directory.resolve()) or file_sha256(path) != checksum:
            raise ValueError(f"Artifact mismatch: {name}")


def measured_process(command, log_path):
    """Include Windows venv redirector children, where the real Python worker lives."""
    started = time.perf_counter()
    peak, os_peak, samples = 0, 0, 0
    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.Popen(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
        monitored = psutil.Process(process.pid)
        try:
            while process.poll() is None:
                try:
                    memories = []
                    for child in [monitored, *monitored.children(recursive=True)]:
                        try:
                            memories.append(child.memory_info())
                        except psutil.NoSuchProcess:
                            pass
                    peak = max(peak, sum(m.rss for m in memories))
                    os_peak = max([os_peak, *(getattr(m, "peak_wset", 0) for m in memories)])
                    samples += 1
                except psutil.NoSuchProcess:
                    pass
                time.sleep(0.05)
            if process.returncode != 0:
                raise RuntimeError(f"Worker failed ({process.returncode}); inspect {log_path.name}")
        finally:
            if process.poll() is None:
                children = monitored.children(recursive=True)
                for child in [*reversed(children), monitored]:
                    try:
                        child.terminate()
                    except psutil.NoSuchProcess:
                        pass
                _, alive = psutil.wait_procs([*children, monitored], timeout=10)
                for child in alive:
                    try:
                        child.kill()
                    except psutil.NoSuchProcess:
                        pass
                process.wait()
    return {"worker_wall_s": time.perf_counter() - started, "sampled_peak_rss_bytes": peak,
            "largest_process_os_peak_working_set_bytes": os_peak or None, "memory_samples": samples,
            "sampling_interval_s": 0.05,
            "scope": "sampled sum of worker-tree RSS including Windows launcher children; may double-count shared pages; OS peak is largest individual process; includes imports, setup, eyes, physics and export; excludes orchestrator and GPU"}


def execute_worker(task_path):
    task = json.loads(task_path.read_text(encoding="utf-8"))
    assay = Assay(**task["assay"])
    config = task["behavior_config"]
    settings, parameters = validate_suite(config)
    settings = {**settings, "seed": assay.seed}
    with np.load(task["circuit"], allow_pickle=False) as data:
        graph = Connectome(*(data[k].copy() for k in ("ids", "nt", "pre", "post", "counts")), {"source": "audited stage-8 circuit.npz"}).validate()
    if assay.condition == "no_feedback":
        graph = without_feedback(graph)
    mode = "connected" if assay.condition == "no_feedback" else assay.condition
    # Reuse the same four Poisson sources and protocol clocks in every condition.
    condition = assay.condition if assay.condition in ("connected", "disconnected", "silenced") else "connected"
    trial = Trial(assay.scenario, condition, assay.seed)
    directory = task_path.parent
    started = time.perf_counter()
    rig = BehaviorRig(settings)
    setup_s = time.perf_counter() - started
    try:
        state, metrics, _ = episode(rig, graph, parameters, mode, directory,
                                    protocol=TrialProtocol(config, trial), physics_observer=rig.record_physics)
        physics = rig.physics_arrays()
        warnings = np.asarray(rig.sim.mj_data.warning.number).astype(int).tolist()
    finally:
        rig.close()
    np.savez_compressed(directory / "physics.npz", **physics)
    checks = technical_checks(state, physics, trial, config, warnings)
    if assay.condition == "no_edges":
        checks["zero_edges_zero_dn_spikes"] = metrics["ordered_pairs"] == 0 and int(state["dn_counts"].sum()) == 0
    if assay.condition == "no_feedback":
        checks["only_existing_feedback_removed"] = not bool(np.any(np.isin(graph.ids[graph.pre], state["output_ids"])))
    if assay.condition == "no_vision":
        checks["tonic_input_only"] = bool(np.all(state["input_rates_hz"][:, :2] == parameters.baseline_input_hz))
    if assay.condition == "disconnected":
        checks["disconnected_brain_still_active"] = int(state["dn_counts"].sum()) > 0
    recording = f"{mode}/recording.npz"
    files = {name: file_sha256(directory / name) for name in ("task.json", "physics.npz", recording, f"{mode}/report.json")}
    result = {"assay": asdict(assay), "technical_status": "passed" if all(checks.values()) else "failed", "checks": checks,
              "behavior": behavior_metrics(state, physics, config, assay.scenario), "episode": metrics,
              "body_setup_s": setup_s, "recording": recording, "files": files}
    write_json(directory / "result.json", result)
    if not all(checks.values()):
        raise RuntimeError(f"Failed checks: {[k for k, v in checks.items() if not v]}")


def execute_assay(assay, behavior, config, output):
    directory = output / "trials" / assay.key
    directory.mkdir(parents=True, exist_ok=True)
    base = copy.deepcopy(behavior)
    base["seeds"] = config["training_seeds"] if assay.phase == "training" else config["validation_seeds"]
    base["simulation"]["coupling"]["maximum_stride_reduction"] = assay.gain
    write_json(directory / "task.json", {"assay": asdict(assay), "behavior_config": base,
                                        "circuit": str((output / "circuit.npz").resolve())})
    performance = measured_process([sys.executable, str(ROOT / "validation_experiment.py"), "--worker", str(directory / "task.json")], directory / "worker.log")
    result = json.loads((directory / "result.json").read_text(encoding="utf-8"))
    duration = base["simulation"]["sensors"]["duration_s"]
    performance["wall_s_per_simulated_s"] = performance["worker_wall_s"] / duration
    result["performance"] = performance
    result["files"]["worker.log"] = file_sha256(directory / "worker.log")
    write_json(directory / "result.json", result)
    return load_verified_result(directory, file_sha256(directory / "result.json"))


def load_recording(output, result):
    path = output / "trials" / Assay(**result["assay"]).key / result["recording"]
    with np.load(path, allow_pickle=False) as data:
        return {key: data[key].copy() for key in data.files}


def compare_results(config, selection, behavior, reports, output):
    gain = selection["gain"]
    default = behavior["simulation"]["coupling"]["maximum_stride_reduction"]
    plan = training_plan(config) + evaluation_plan(config, gain, default)
    if set(reports) != {a.key for a in plan}:
        raise ValueError("Cannot summarize incomplete or extra assays")
    checks, paired, held_out, repeats = {}, [], [], []

    def result(profile, scenario, seed, condition="connected", repeat=False):
        g = default if profile == "default" else gain
        return reports[Assay("validation", profile, g, scenario, condition, seed, repeat).key]

    for seed in config["validation_seeds"]:
        row = {"seed": seed}
        for profile in ("default", "selected"):
            rows = {s: result(profile, s, seed)["behavior"] for s in SCENARIOS}
            row[profile + "_cost"] = objective(rows, config)
            row[profile + "_falls"] = sum(r["fell"] for r in rows.values())
            row[profile + "_turns_relative_neutral_deg"] = [rows[s]["heading_change_deg"] - rows["neutral"]["heading_change_deg"] for s in ("left", "right")]
        held_out.append(row)
        for scenario in ("left", "right"):
            connected = result("selected", scenario, seed)
            disconnected = load_recording(output, result("selected", scenario, seed, "disconnected"))
            for condition in ABLATIONS:
                ablated = result("selected", scenario, seed, condition)
                current = load_recording(output, ablated)
                if condition in ("silenced", "no_edges"):
                    checks[f"{scenario}_{seed}_{condition}_same_cpg_physics"] = bool(np.array_equal(current["qpos_native"], disconnected["qpos_native"]))
                paired.append({"seed": seed, "scenario": scenario, "condition": condition,
                               "heading_deg": ablated["behavior"]["heading_change_deg"],
                               "connected_minus_ablation_heading_deg": connected["behavior"]["heading_change_deg"] - ablated["behavior"]["heading_change_deg"],
                               "progress_mm": ablated["behavior"]["forward_progress_mm"],
                               "dn_spikes_left_right": ablated["behavior"]["dn_spikes_left_right"], "fell": ablated["behavior"]["fell"]})
        left = load_recording(output, result("selected", "left", seed, "no_vision"))
        right = load_recording(output, result("selected", "right", seed, "no_vision"))
        checks[f"{seed}_no_vision_ignores_panel_side"] = all(np.array_equal(left[k], right[k]) for k in ("qpos_native", "input_rates_hz", "spike_times_s", "spike_indices"))
    for i, scenario in enumerate(("left", "right")):
        seed = config["validation_seeds"][i]
        first = load_recording(output, result("selected", scenario, seed))
        again = load_recording(output, result("selected", scenario, seed, repeat=True))
        exact = all(np.array_equal(first[k], again[k]) for k in ("qpos_native", "input_rates_hz", "spike_times_s", "spike_indices", "voltage_v", "applied_command"))
        retina = float(np.max(np.abs(first["ommatidia"] - again["ommatidia"])))
        checks[f"{scenario}_{seed}_repeat_exact"] = exact
        checks[f"{scenario}_{seed}_repeat_retina_tolerance"] = retina <= 1 / 255
        repeats.append({"scenario": scenario, "seed": seed, "state_exact": exact, "retina_max_abs": retina})
    checks["all_trials_passed"] = all(r["technical_status"] == "passed" and all(r["checks"].values()) for r in reports.values())
    checks["all_workers_measured"] = all(r["performance"]["sampled_peak_rss_bytes"] > 0 and r["performance"]["worker_wall_s"] > 0 for r in reports.values())
    return checks, {"held_out": held_out, "ablations": paired, "repeats": repeats,
                    "mean_default_cost": float(np.mean([r["default_cost"] for r in held_out])),
                    "mean_selected_cost": float(np.mean([r["selected_cost"] for r in held_out])),
                    "no_exclusions": True, "statistics": "descriptive paired model seeds; not biological replicates or significance tests"}


def plot_report(output, selection, outcomes, reports):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(1, 2, figsize=(11, 4.5), layout="constrained")
    for i, score in enumerate(selection["scores"]):
        axes[0].scatter([i] * len(score["cost_per_seed"]), score["cost_per_seed"], color="#197c71", s=45)
        axes[0].plot([i - .2, i + .2], [score["mean_cost"]] * 2, color="#222222")
    axes[0].set(xticks=range(len(selection["scores"])), xticklabels=[s["gain"] for s in selection["scores"]],
                xlabel="Maximum stride reduction", ylabel="Engineering cost [deg-equivalent]", title="Training only; bars = means")
    for row in outcomes["held_out"]:
        axes[1].plot([0, 1], [row["default_cost"], row["selected_cost"]], "o-", label=f"seed {row['seed']}")
    axes[1].set(xticks=[0, 1], xticklabels=["Unchanged default", "Frozen selection"], ylabel="Same predeclared cost", title="Held-out seeds; no retuning")
    axes[1].legend()
    fig.suptitle(f"FlyLab 08 | Engineering target: +/-{selection['target_turn_deg']:g} deg relative to neutral; NOT biological calibration")
    fig.savefig(output / "calibration.png", dpi=150)
    plt.close(fig)

    fig, axes = plt.subplots(1, 2, figsize=(13, 5), sharey=True, layout="constrained")
    conditions = ("connected", *ABLATIONS)
    for ax, scenario in zip(axes, ("left", "right")):
        for i, condition in enumerate(conditions):
            rows = [r for r in reports.values() if r["assay"]["phase"] == "validation" and r["assay"]["profile"] == "selected"
                    and r["assay"]["scenario"] == scenario and r["assay"]["condition"] == condition and not r["assay"]["repeat"]]
            values = [r["behavior"]["heading_change_deg"] for r in rows]
            ax.scatter([i] * len(values), values, color="#ae4061" if i == 0 else "#217d72", s=35)
            ax.plot([i - .22, i + .22], [np.mean(values)] * 2, color="#252525")
        ax.axhline(0, color="#aaaaaa", linewidth=1)
        ax.set(xticks=range(len(conditions)), xticklabels=[c.replace("_", "\n") for c in conditions], title=f"{scenario.title()} panel", ylabel="Final heading change [deg]")
    fig.suptitle("Held-out ablations | points = seeds; bars = means | same independent walking CPG")
    fig.savefig(output / "ablations.png", dpi=150)
    plt.close(fig)


def benchmark_brain(output, duration, manifest):
    directory = output / "brain-benchmark"
    if "brain_benchmark" in manifest:
        verified_files(output, manifest["brain_benchmark"]["files"])
        return manifest["brain_benchmark"]
    performance = measured_process([sys.executable, str(ROOT / "brain_experiment.py"), "suite", "--cache", manifest["cache_path"],
                                    "--output", str(directory), "--full-duration", str(duration)], output / "brain-benchmark.log")
    report = json.loads((directory / "report.json").read_text(encoding="utf-8"))
    if report["status"] != "passed" or not all(report["checks"].values()):
        raise ValueError("Brain benchmark verification failed")
    files = {p.relative_to(output).as_posix(): file_sha256(p) for p in directory.rglob("*") if p.is_file()}
    files["brain-benchmark.log"] = file_sha256(output / "brain-benchmark.log")
    return {"performance": performance, "files": files, "checks": report["checks"],
            "runs": {key: {k: value[k] for k in ("neurons", "ordered_pairs", "spikes", "run_and_compile_s")}
                     for key, value in report["runs"].items()},
            "scope": "isolated full-connectome neural suite; NOT the four-cell body loop"}


def run_suite(config, behavior, cache, data_dir, output, resume=False, max_trials=None):
    validate_config(config)
    validate_suite(behavior)
    if max_trials is not None and (type(max_trials) is not int or max_trials < 1):
        raise ValueError("max-trials must be positive")
    output = output.resolve()
    graph, mapping = load_steering_circuit(cache, data_dir)
    identity = {"configuration": config, "behavior": behavior, "mapping_sha256": mapping["mapping_sha256"],
                "code_sha256": {name: file_sha256(ROOT / name) for name in SOURCE_FILES},
                "versions": {name: importlib.metadata.version(name) for name in ("numpy", "scipy", "brian2", "flygym", "mujoco", "psutil", "matplotlib")},
                "python": platform.python_version(), "platform": platform.platform(), "machine": platform.machine(),
                "logical_cpus": psutil.cpu_count(), "model_parameters": asdict(ModelParameters()),
                "packet_sha256": file_sha256(ROOT / "outputs/flylab/prepared-baseline.json")}
    fingerprint = digest(identity)
    manifest_path = output / "manifest.json"
    if manifest_path.exists():
        if not resume:
            raise ValueError("Existing validation suite; use --resume or a fresh output")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest["fingerprint"] != fingerprint or digest(manifest["identity"]) != fingerprint:
            raise ValueError("Resume refused: code, settings, data or environment changed")
        if set(manifest["source_files"]) != {"mapping.json", "settings.json", "circuit.npz"}:
            raise ValueError("Source artifact set mismatch")
        verified_files(output, manifest["source_files"])
    else:
        if output.exists() and any(output.iterdir()):
            raise ValueError("Nonempty output without valid manifest")
        output.mkdir(parents=True, exist_ok=True)
        write_json(output / "mapping.json", mapping)
        write_json(output / "settings.json", {"validation": config, "behavior": behavior})
        np.savez_compressed(output / "circuit.npz", **{key: getattr(graph, key) for key in ("ids", "nt", "pre", "post", "counts")})
        manifest = {"stage": 8, "status": "running", "fingerprint": fingerprint, "identity": identity,
                    "completed": {}, "cache_path": str(cache.resolve()),
                    "source_files": {name: file_sha256(output / name) for name in ("mapping.json", "settings.json", "circuit.npz")}}
        write_checkpoint(manifest_path, manifest)
    # Validate keys before they are used as filesystem paths.
    possible = training_plan(config)
    default = behavior["simulation"]["coupling"]["maximum_stride_reduction"]
    for gain in config["stride_reductions"]:
        possible.extend(evaluation_plan(config, gain, default))
    if not set(manifest["completed"]) <= {a.key for a in possible}:
        raise ValueError("Unknown checkpoint trial")
    reports = {key: load_verified_result(output / "trials" / key, checksum) for key, checksum in manifest["completed"].items()}
    executed = 0

    def run_plan(plan):
        nonlocal executed
        for assay in plan:
            if assay.key in reports:
                if reports[assay.key]["assay"] != asdict(assay):
                    raise ValueError("Checkpoint assay identity mismatch")
                continue
            if max_trials is not None and executed >= max_trials:
                return False
            manifest.update(status="running", current_trial=assay.key)
            write_checkpoint(manifest_path, manifest)
            print(f"[{len(reports) + 1}] {assay.key} (gain={assay.gain})", flush=True)
            reports[assay.key] = execute_assay(assay, behavior, config, output)
            manifest["completed"][assay.key] = file_sha256(output / "trials" / assay.key / "result.json")
            executed += 1
            write_checkpoint(manifest_path, manifest)
        return True

    try:
        train = training_plan(config)
        complete = run_plan(train)
        if complete:
            selected = select_candidate({a.key: reports[a.key] for a in train}, config)
            selected["training_result_sha256"] = {a.key: manifest["completed"][a.key] for a in train}
            if "selection_sha256" in manifest:
                verified_files(output, {"selection.json": manifest["selection_sha256"]})
                if json.loads((output / "selection.json").read_text(encoding="utf-8")) != selected:
                    raise ValueError("Frozen selection changed")
            else:
                if set(reports) != {a.key for a in train}:
                    raise ValueError("Validation results cannot precede frozen selection")
                write_json(output / "selection.json", selected)
                manifest["selection_sha256"] = file_sha256(output / "selection.json")
                write_checkpoint(manifest_path, manifest)
            selected_behavior = copy.deepcopy(behavior)
            selected_behavior["simulation"]["coupling"]["maximum_stride_reduction"] = selected["gain"]
            write_json(output / "selected-behavior.json", selected_behavior)
            complete = run_plan(evaluation_plan(config, selected["gain"], default))
        if not complete:
            manifest.update(status="partial", current_trial=None)
            write_checkpoint(manifest_path, manifest)
            print(json.dumps({"status": "partial", "completed": len(reports)}), flush=True)
            return
        checks, outcomes = compare_results(config, selected, behavior, reports, output)
        brain = benchmark_brain(output, config["full_brain_duration_s"], manifest)
        manifest["brain_benchmark"] = brain
        write_checkpoint(manifest_path, manifest)
        checks["full_brain_suite_passed"] = all(brain["checks"].values())
        plot_report(output, selected, outcomes, reports)
        rows = [{"key": key, **r["performance"]} for key, r in reports.items()]
        report = {"stage": 8, "status": "passed" if all(checks.values()) else "failed", "biologically_validated": False,
                  "fingerprint": fingerprint, "trials": len(reports), "checks": checks,
                  "per_trial_checks": sum(len(r["checks"]) for r in reports.values()),
                  "selection": selected, "outcomes": outcomes, "brain_benchmark": brain,
                  "performance": {"per_trial": rows, "median_worker_wall_s": float(np.median([r["worker_wall_s"] for r in rows])),
                                  "median_wall_s_per_simulated_s": float(np.median([r["wall_s_per_simulated_s"] for r in rows])),
                                  "max_sampled_peak_rss_bytes": max(r["sampled_peak_rss_bytes"] for r in rows)},
                  "limitations": ["Four cells in the body loop; full graph benchmark is separate", "Engineering +/-30 degree target, not measured animal data",
                                  "Gain selection never uses held-out seeds; defaults remain unchanged", "CPG and neural seeds co-vary; controls identify interventions, not independent variance components",
                                  "No obstacle improvement claimed; stage-6 failures remain", "No VNC, olfaction, flight or biological parameter calibration",
                                  "No real-time guarantee; RSS is whole worker process, not neural allocation alone"],
                  "artifact_sha256": {name: file_sha256(output / name) for name in ("mapping.json", "settings.json", "circuit.npz", "selection.json", "selected-behavior.json", "calibration.png", "ablations.png")}}
        write_json(output / "report.json", report)
        summary = {key: report[key] for key in ("stage", "status", "biologically_validated", "fingerprint", "trials", "checks", "per_trial_checks", "selection", "outcomes", "limitations")}
        summary.update(configuration=config, duration_s=behavior["simulation"]["sensors"]["duration_s"],
                       code_sha256=identity["code_sha256"], versions=identity["versions"],
                       performance={k: v for k, v in report["performance"].items() if k != "per_trial"},
                       full_brain={k: v for k, v in brain.items() if k != "files"},
                       artifact_sha256=report["artifact_sha256"])
        write_json(output / "summary.json", summary)
        manifest.update(status=report["status"], current_trial=None, report_sha256=file_sha256(output / "report.json"))
        manifest["summary_sha256"] = file_sha256(output / "summary.json")
        manifest.pop("error", None)
        write_checkpoint(manifest_path, manifest)
        print(json.dumps({"status": report["status"], "trials": len(reports), "checks": len(checks), "failed": [k for k, v in checks.items() if not v]}), flush=True)
        if not all(checks.values()):
            raise RuntimeError("Stage-8 integration checks failed")
    except Exception as error:
        manifest.update(status="failed", error=f"{type(error).__name__}: {error}")
        write_checkpoint(manifest_path, manifest)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "experiments/validation-suite.json")
    parser.add_argument("--cache", type=Path, default=ROOT / "outputs/flylab/connectome-cache")
    parser.add_argument("--data-dir", type=Path, default=Path.home() / "Downloads/mind")
    parser.add_argument("--output", type=Path, default=ROOT / "outputs/flylab/validation-stage8")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--max-trials", type=int)
    parser.add_argument("--worker", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        execute_worker(args.worker)
    else:
        config = json.loads(args.config.read_text(encoding="utf-8"))
        behavior = json.loads((ROOT / "experiments/behavior-suite.json").read_text(encoding="utf-8"))
        run_suite(config, behavior, args.cache, args.data_dir, args.output, args.resume, args.max_trials)


if __name__ == "__main__":
    main()
