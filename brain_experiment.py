"""Run a configured Brian2 experiment or the stage-3 verification suite."""

import argparse
from decimal import Decimal
import json
from pathlib import Path
import platform
import sys

from brain_model import ModelParameters, polarity, simulate
from flylab import ROOT, write_json
from flywire_data import file_sha256, from_packet, load_cache
import matplotlib.pyplot as plt
import numpy as np


def save_run(output, graph, config, record_ids, parameters=None):
    output.mkdir(parents=True, exist_ok=True)
    write_json(output / "report.json", {"status": "running", "config": config})
    try:
        data, report = simulate(graph, config, parameters, record_ids=record_ids)
        np.savez_compressed(output / "activity.npz", **data)
        report.update(python=platform.python_version(), executable=sys.executable,
                      code_sha256={name: file_sha256(ROOT / name) for name in
                                   ("brain_model.py", "brain_experiment.py", "flywire_data.py", "flylab.py")},
                      activity_sha256=file_sha256(output / "activity.npz"))
        write_json(output / "config.json", config)
        write_json(output / "parameters.json", report["parameters_si"])
        write_json(output / "report.json", report)
        print(f"{output.name}: {report['neurons']} neurons, {report['spikes']} spikes, {report['run_and_compile_s']:.3f}s", flush=True)
        return data, report
    except Exception as error:
        write_json(output / "report.json", {"status": "failed", "error": str(error), "config": config})
        raise


def plot_comparison(output, cases, target, graph):
    figure, axes = plt.subplots(3, 3, figsize=(15, 9), sharex=True, sharey="row", layout="constrained")
    titles = ["Control: no stimulus", "Stimulate CT1 (GABA assumption)", "Same input + CT1 silenced"]
    for column, ((data, report), title) in enumerate(zip(cases, titles)):
        axes[0, column].scatter(data["spike_times_s"] * 1000, data["spike_indices"], s=13, color="#b53d65")
        axes[0, column].set(ylim=(-0.6, len(graph.ids) - 0.4), title=f"{title}\n{report['spikes']} spikes")
        axes[0, column].set_yticks(range(len(graph.ids)), [str(i) for i in range(len(graph.ids))])
        for row, variable, scale in ((1, "voltage_v", 1000), (2, "synaptic_drive_v", 1000)):
            for i, neuron in enumerate(data["record_ids"]):
                label = "CT1 / stimulated" if neuron == target else f"target ...{neuron[-6:]}"
                axes[row, column].plot(data["trace_time_s"] * 1000, data[variable][i] * scale,
                                       label=label, linewidth=1.1)
        for row in range(3):
            axes[row, column].axvspan(25, 175, color="#228b76", alpha=0.09)
            axes[row, column].grid(alpha=0.18)
            axes[row, column].set_xlim(0, 250)
        axes[2, column].set_xlabel("Time [ms]")
    axes[0, 0].set_ylabel("Neuron index in activity.npz")
    axes[1, 0].set_ylabel("Membrane potential [mV]")
    axes[2, 0].set_ylabel("Synaptic drive [mV]")
    axes[1, 1].legend(loc="lower right", fontsize=8)
    figure.suptitle("FlyLab | Brian2, real 13-neuron FlyWire subgraph\nModel output, not measured activity. Body not connected. Shaded interval: intervention.", fontsize=14)
    figure.savefig(output / "comparison.png", dpi=145)
    plt.close(figure)


def plot_full(output, data, report):
    active = np.flatnonzero(data["spike_counts"])
    figure, axes = plt.subplots(3, 1, figsize=(12, 8), sharex=True, layout="constrained",
                               gridspec_kw={"height_ratios": [2, 1, 1]})
    ranks = np.searchsorted(active, data["spike_indices"])
    is_input = np.isin(data["neuron_ids"][data["spike_indices"]], data["input_ids"])
    for mask, label, color in ((~is_input, "Other cells", "#247d76"), (is_input, "Directly stimulated", "#bd3863")):
        axes[0].scatter(data["spike_times_s"][mask] * 1000, ranks[mask], s=6, color=color, label=label)
    axes[0].set_ylabel(f"Active cell rank ({len(active)} cells)")
    axes[0].legend(loc="upper right", fontsize=9)
    bins = np.arange(0, report["config"]["duration_s"] + 0.0005, 0.001)
    counts, edges = np.histogram(data["spike_times_s"], bins=bins)
    axes[1].stairs(counts, edges * 1000, fill=True, color="#247d76", alpha=0.8)
    axes[1].set_ylabel("Spikes / 1 ms bin")
    for i, neuron in enumerate(data["input_ids"]):
        times = data["input_times_s"][data["input_indices"] == i] * 1000
        axes[2].scatter(times, np.full(len(times), i), marker="|", s=100, color="#bd3863")
    axes[2].set_yticks(range(len(data["input_ids"])), [f"...{i[-6:]}" for i in data["input_ids"]])
    axes[2].set_ylabel("Input events / cell")
    axes[2].set_xlabel("Simulation time [ms]")
    axes[2].set_ylim(-0.5, len(data["input_ids"]) - 0.5)
    stimulus = report["config"]["interventions"][0]
    for axis in axes:
        axis.axvspan(stimulus["start_s"] * 1000, stimulus["end_s"] * 1000, color="#228b76", alpha=0.07)
        axis.grid(alpha=0.15)
        axis.set_xlim(0, report["config"]["duration_s"] * 1000)
    figure.suptitle(f"FlyLab | {report['neurons']:,} neurons, {report['ordered_pairs']:,} directed pairs\n"
                   f"{report['spikes']} simulated spikes. Synthetic input on real connectivity; no body or sensory loop.", fontsize=13)
    figure.savefig(output / "full-activity.png", dpi=145)
    plt.close(figure)


def suite(cache, packet_path, output, full_duration):
    output.mkdir(parents=True, exist_ok=True)
    write_json(output / "report.json", {"status": "running"})
    try:
        packet = json.loads(packet_path.read_text(encoding="utf-8"))
        small = from_packet(packet)
        full = load_cache(cache)
        verified = full.subset(small.ids.tolist())
        for key in ("ids", "nt", "pre", "post", "counts"):
            if not np.array_equal(getattr(small, key), getattr(verified, key)):
                raise ValueError(f"Cached HTML subgraph disagrees with full raw import: {key}")
        small = verified
        base = {**packet["config"], "duration_s": 0.25, "interventions": []}
        target = base["graph_center_id"]
        target_index = small.ids.tolist().index(target)
        if small.nt[target_index] != "GABA":
            raise ValueError("This comparison expects the selected GABA CT1 center")
        outgoing = np.flatnonzero(small.pre == target_index)
        top_edges = outgoing[np.argsort(-small.counts[outgoing])[:3]]
        record_ids = [target, *(str(small.ids[small.post[i]]) for i in top_edges)]
        stimulus = {"kind": "stimulate", "neuron_ids": [target], "rate_hz": 150, "start_s": 0.025, "end_s": 0.175}
        silence = {"kind": "silence", "neuron_ids": [target], "start_s": 0, "end_s": 0.25}
        control = save_run(output / "small-control", small, {**base, "experiment_id": "brain-control"}, record_ids)
        active_config = {**base, "experiment_id": "brain-stimulated", "interventions": [stimulus]}
        active = save_run(output / "small-stimulated", small, active_config, record_ids)
        repeated = save_run(output / "small-repeat", small, active_config, record_ids)
        quiet = save_run(output / "small-silenced", small,
                          {**base, "experiment_id": "brain-silenced", "interventions": [stimulus, silence]}, record_ids)
        checks = {
            "small_matches_raw_import": True,
            "control_has_no_spikes": control[1]["spikes"] == 0,
            "stimulus_causes_spikes": active[1]["spikes"] > 0,
            "gaba_targets_hyperpolarize": bool(np.min(active[0]["voltage_v"][1:]) < ModelParameters().rest_v - 1e-6),
            "silencing_blocks_spikes": quiet[1]["spikes"] == 0,
            "same_external_input_when_silenced": bool(np.array_equal(active[0]["input_times_s"], quiet[0]["input_times_s"])),
            "repeat_all_arrays_identical": all(np.array_equal(active[0][key], repeated[0][key]) for key in active[0]),
        }
        plot_comparison(output, [control, active, quiet], target, small)
        if not all(checks.values()):
            raise RuntimeError(f"Small circuit verification failed: {checks}")
        # Stress-test inputs chosen by connectivity only, not as a sensory pathway.
        scores = np.bincount(full.pre, weights=full.counts, minlength=len(full.ids))
        candidates = np.flatnonzero(polarity(full.nt) == 1)
        chosen = candidates[np.argsort(-scores[candidates], kind="stable")[:3]]
        driven_ids = full.ids[chosen].tolist()
        full_stimulus = {"kind": "stimulate", "neuron_ids": driven_ids, "rate_hz": 150,
                         "start_s": 0.005, "end_s": float(Decimal(str(full_duration)) - Decimal("0.005"))}
        full_config = {**base, "duration_s": full_duration, "experiment_id": "brain-full-benchmark", "interventions": [full_stimulus]}
        full_run = save_run(output / "full-stimulated", full, full_config, driven_ids)
        full_repeat = save_run(output / "full-repeat", full, full_config, driven_ids)
        full_control = save_run(output / "full-control", full,
                                {**full_config, "experiment_id": "brain-full-control", "interventions": []}, driven_ids)
        checks["full_finite_state"] = full_run[1]["finite_state"]
        checks["full_stimulus_causes_spikes"] = full_run[1]["spikes"] > 0
        checks["full_repeat_all_arrays_identical"] = all(np.array_equal(full_run[0][key], full_repeat[0][key]) for key in full_run[0])
        checks["full_control_no_spikes"] = full_control[1]["spikes"] == 0
        plot_full(output, *full_run)
        summary = {"status": "passed" if all(checks.values()) else "failed", "checks": checks,
                   "scope": "Engineering validation, not behavioral or biological validation",
                   "body_connected": False, "full_benchmark_input_policy": "top 3 ACH cells by outgoing contact count",
                   "runs": {name: report for name, (_, report) in
                            (("control", control), ("stimulated", active), ("repeat", repeated), ("silenced", quiet),
                             ("full", full_run), ("full_repeat", full_repeat), ("full_control", full_control))}}
        write_json(output / "report.json", summary)
        if summary["status"] != "passed":
            raise RuntimeError("Full-scale verification failed")
        print(json.dumps({"status": summary["status"], "checks": checks}, indent=2))
    except Exception as error:
        write_json(output / "report.json", {"status": "failed", "error": str(error)})
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    verify = commands.add_parser("suite")
    verify.add_argument("--cache", type=Path, default=ROOT / "outputs/flylab/connectome-cache")
    verify.add_argument("--packet", type=Path, default=ROOT / "outputs/flylab/prepared-baseline.json")
    verify.add_argument("--output", type=Path, default=ROOT / "outputs/flylab/brain-stage3")
    verify.add_argument("--full-duration", type=float, default=0.05)
    run = commands.add_parser("run")
    run.add_argument("config", type=Path)
    run.add_argument("--cache", type=Path, default=ROOT / "outputs/flylab/connectome-cache")
    run.add_argument("--packet", type=Path, help="Use this small packet instead of the full cache")
    run.add_argument("--parameters", type=Path)
    run.add_argument("--record", nargs="*", default=[])
    run.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "suite":
        if not 0.02 <= args.full_duration <= 1:
            parser.error("full-duration must be between 0.02 and 1 second")
        suite(args.cache, args.packet, args.output, args.full_duration)
    else:
        graph = from_packet(json.loads(args.packet.read_text())) if args.packet else load_cache(args.cache)
        setup = json.loads(args.config.read_text())
        parameters = ModelParameters(**json.loads(args.parameters.read_text())) if args.parameters else None
        save_run(args.output, graph, setup, args.record, parameters)


if __name__ == "__main__":
    main()
