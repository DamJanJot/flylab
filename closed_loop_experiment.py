"""Stage 5: a causal sensor -> real FlyWire fragment -> surrogate CPG loop."""

import argparse
from dataclasses import asdict
from decimal import Decimal
import importlib.metadata
import json
from pathlib import Path
import time

import numpy as np

from flylab import ROOT, digest, write_json
from flywire_data import file_sha256
from sensory_experiment import SensorRig, validate_settings
from sensory_model import EncoderParameters, SensoryEncoder
from brain_model import ModelParameters, NeuralRuntime, b2
from closed_loop_model import (
    CELLS, INPUT_IDS, OUTPUT_IDS, CouplingParameters, SteeringAdapter,
    load_steering_circuit, without_edges,
)


MODES = ("connected", "repeat", "disconnected", "silenced", "no_edges", "no_vision")


def validate_configuration(config):
    if not isinstance(config, dict) or set(config) != {"sensors", "coupling"}:
        raise ValueError("Expected sensors and coupling settings")
    settings = validate_settings(config["sensors"])
    if not isinstance(config["coupling"], dict) or set(config["coupling"]) != set(asdict(CouplingParameters())):
        raise ValueError("Invalid coupling fields")
    parameters = CouplingParameters(**config["coupling"]).validate()
    if (parameters.baseline_input_hz + parameters.visual_gain_hz + parameters.input_quantum_hz / 2) * settings["brain_dt_s"] > 0.1:
        raise ValueError("Coupling input exceeds Poisson rate*dt limit")
    return settings, parameters


def panel_state(index, bins):
    # Entire bins only; first sixth is neutral, then left, then right.
    return (bins // 6 <= index < bins * 7 // 12, bins * 7 // 12 <= index)


def neural_arrays(runtime, graph):
    return {
        "neuron_ids": graph.ids.copy(), "input_ids": np.array(runtime.input_ids),
        "output_ids": np.array(OUTPUT_IDS),
        # Brian2 monitor slices can expose non-owning buffers invalidated with
        # the network. Snapshot them before the next episode releases it.
        "spike_indices": np.array(runtime.spikes.i[:], dtype=np.int32, copy=True),
        "spike_times_s": np.asarray(runtime.spikes.t[:] / b2.second),
        "spike_counts": np.array(runtime.spikes.count[:], copy=True),
        "input_indices": np.array(runtime.input_spikes.i[:], dtype=np.int32, copy=True),
        "input_times_s": np.asarray(runtime.input_spikes.t[:] / b2.second),
        "final_voltage_v": np.asarray(runtime.neurons.v[:] / b2.volt),
        "final_synaptic_drive_v": np.asarray(runtime.neurons.g[:] / b2.volt),
    }


def episode(rig, graph, parameters, mode, output, capture=False, protocol=None, physics_observer=None):
    import mujoco
    from flygym_demo.complex_terrain import CPGController, make_tripod_cpg_network, apply_locomotion_action

    if mode not in MODES:
        raise ValueError("Unknown episode mode")
    started = time.perf_counter()
    settings = rig.settings
    dt, control_dt = settings["physics_dt_s"], settings["sensory_dt_s"]
    bins, stride = round(settings["duration_s"] / control_dt), round(control_dt / dt)
    rig.reset()
    reference = rig.observe()
    encoder = SensoryEncoder(rig.joint_names, rig.legs, reference["joint_rad"], EncoderParameters(**settings["encoder"]))
    reference_rates = encoder.encode(0, reference["ommatidia"], reference["contacts"], reference["joint_rad"], reference["joint_velocity_rad_s"])
    encoder.reset()
    adapter = SteeringAdapter(parameters)
    active_graph = without_edges(graph) if mode == "no_edges" else graph
    input_ids = INPUT_IDS if protocol is None else protocol.input_ids
    runtime = NeuralRuntime(active_graph, settings["brain_dt_s"], settings["seed"], input_ids)
    output_indices = [runtime.lookup[root] for root in OUTPUT_IDS]
    silent_ids = OUTPUT_IDS if mode == "silenced" else ()
    cpg = make_tripod_cpg_network(timestep=dt, seed=settings["seed"])
    controller = CPGController(cpg, rig.steps, rig.dofs)
    command = np.ones(2)
    observations, records, frames = [], [], []
    joint_targets = np.empty((bins * stride, len(rig.dofs)))
    adhesion = np.empty((bins * stride, 6), dtype=bool)
    for index in range(bins + 1):
        t = float(Decimal(index) * Decimal(str(control_dt)))
        # The terminal observation retains the last stimulus; it is not an extra input bin.
        panels = (panel_state(min(index, bins - 1), bins) if protocol is None
                  else protocol.panels(min(index, bins - 1)))
        rig.set_panels(*panels)
        observation = rig.observe()
        rates = encoder.encode(t, observation["ommatidia"], observation["contacts"],
                               observation["joint_rad"], observation["joint_velocity_rad_s"])
        quat = rig.sim.get_body_rotations(rig.fly.name)[rig.thorax]
        heading = np.arctan2(2 * (quat[0] * quat[3] + quat[1] * quat[2]),
                             1 - 2 * (quat[2] ** 2 + quat[3] ** 2))
        observations.append({key: observation[key] for key in (
            "ommatidia", "contacts", "joint_rad", "joint_velocity_rad_s", "thorax_m", "qpos_native")})
        observations[-1].update(time_s=t, encoded_rates_hz=rates, panels=panels,
                                up_axis_z=1 - 2 * (quat[1] ** 2 + quat[2] ** 2), heading_rad=heading)
        if index == bins:
            break
        if capture:
            frames.append(rig.frame())
        input_rates, contrast = adapter.sensory_drive(rates, reference_rates, encoder.channel_names,
                                                      encoder.parameters.maximum_rate_hz)
        if mode == "no_vision":
            input_rates[:] = parameters.baseline_input_hz
        active_silence = silent_ids
        if protocol is not None:
            input_rates, active_silence = protocol.neural_input(index, input_rates, silent_ids)
        # Both engines advance [t,t+dt) using values known at t. The new neural
        # command is only applied to the following body interval, never retroactively.
        counts = runtime.advance(control_dt, input_rates, active_silence)
        next_command = adapter.motor_command(counts[output_indices], control_dt, mode != "disconnected")
        records.append({"input_rates_hz": input_rates.copy(), "visual_contrast": contrast,
                        "counts": counts, "dn_counts": counts[output_indices],
                        "filtered_dn_hz": adapter.filtered_hz.copy(), "applied_command": command.copy(),
                        "next_command": next_command.copy(),
                        "voltage_v": np.asarray(runtime.neurons.v[:] / b2.volt).copy()})
        cpg.intrinsic_amps = np.repeat(command, 3)
        for substep in range(stride):
            action = controller.step()
            joint_targets[index * stride + substep] = action.joint_angles
            adhesion[index * stride + substep] = action.adhesion_onoff
            apply_locomotion_action(rig.sim, rig.fly.name, action)
            rig.sim.step()
            if physics_observer is not None:
                physics_observer()
        mujoco.mj_forward(rig.sim.mj_model, rig.sim.mj_data)
        body_time = rig.sim.mj_data.time - settings["warmup_s"]
        expected = (index + 1) * control_dt
        if abs(body_time - expected) > 1e-9 or abs(float(runtime.network.t / b2.second) - expected) > 1e-10:
            raise RuntimeError("Body and neural clocks diverged")
        command = next_command
    state = {key: np.asarray([row[key] for row in observations]) for key in observations[0]}
    state.update({key: np.asarray([row[key] for row in records]) for key in records[0]})
    state.update(neural_arrays(runtime, graph))
    state.update(joint_targets_rad=joint_targets, adhesion=adhesion,
                 channel_names=np.array(encoder.channel_names), reference_rates_hz=reference_rates)
    state["bin_start_s"] = state["time_s"][:-1]
    state["bin_end_s"] = state["time_s"][1:]
    state["heading_rad"] = np.unwrap(state["heading_rad"])
    directory = output / mode
    directory.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(directory / "recording.npz", **state)
    metrics = {
        "mode": mode, "neural_output_connected": mode != "disconnected",
        "silenced_ids": list(silent_ids), "ordered_pairs": len(active_graph.pre),
        "neural_spikes": int(state["spike_counts"].sum()), "dn_spikes_left_right": state["dn_counts"].sum(axis=0).tolist(),
        "final_xy_mm": (state["thorax_m"][-1, :2] * 1000).tolist(),
        "heading_change_deg": float(np.rad2deg(state["heading_rad"][-1] - state["heading_rad"][0])),
        "minimum_up_axis_z": float(state["up_axis_z"].min()),
        "ground_contact_fraction": float(state["contacts"].any(axis=1).mean()),
        "minimum_stride_amplitude": float(state["applied_command"].min()),
        "wall_s": time.perf_counter() - started,
        "wall_s_per_simulated_s": (time.perf_counter() - started) / settings["duration_s"],
        "recording_sha256": file_sha256(directory / "recording.npz"),
    }
    write_json(directory / "report.json", metrics)
    print(json.dumps(metrics), flush=True)
    return state, metrics, frames


def replay_neural_input(graph, settings, recording, output):
    runtime = NeuralRuntime(graph, settings["brain_dt_s"], settings["seed"], INPUT_IDS)
    for rates in recording["input_rates_hz"]:
        runtime.advance(settings["sensory_dt_s"], rates)
    data = neural_arrays(runtime, graph)
    np.savez_compressed(output / "neural-replay.npz", **data)
    return all(np.array_equal(data[key], recording[key]) for key in data)


def compare_runs(runs, parameters):
    baseline, repeated, disconnected, quiet, ablated, no_vision = [runs[name] for name in MODES]
    checks = {}
    for name, state in runs.items():
        checks[f"{name}_finite"] = all(bool(np.isfinite(v).all()) for v in state.values() if v.dtype.kind in "fiu")
        checks[f"{name}_upright"] = bool(state["up_axis_z"].min() > 0.9)
        checks[f"{name}_ground_contact"] = bool(state["contacts"].any(axis=1).all())
        checks[f"{name}_command_bounds"] = bool(np.all(state["applied_command"] >= 1 - parameters.maximum_stride_reduction - 1e-12)
                                                  and np.all(state["applied_command"] <= 1))
        checks[f"{name}_one_bin_latency"] = bool(np.array_equal(state["applied_command"][0], [1, 1]) and
                                                   np.array_equal(state["applied_command"][1:], state["next_command"][:-1]))
    checks.update({
        "dn_output_active": bool(np.all(baseline["dn_counts"].sum(axis=0) > 0)),
        "visual_inputs_vary": bool(np.ptp(baseline["input_rates_hz"], axis=0).min() > 10),
        "visual_input_removal_changes_neurons": not np.array_equal(baseline["spike_times_s"], no_vision["spike_times_s"]),
        "visual_input_removal_changes_command": bool(np.max(np.abs(baseline["applied_command"] - no_vision["applied_command"])) > 0.01),
        "neural_repetition_exact": all(np.array_equal(baseline[k], repeated[k]) for k in ("spike_times_s", "spike_indices", "input_rates_hz", "applied_command")),
        "body_repetition_exact": np.array_equal(baseline["qpos_native"], repeated["qpos_native"]),
        "retina_repetition_within_one_gray_level": bool(np.max(np.abs(baseline["ommatidia"] - repeated["ommatidia"])) < 1 / 255),
        "disconnected_brain_still_active": bool(disconnected["dn_counts"].sum() > 0),
        "disconnected_command_reference": bool(np.all(disconnected["applied_command"] == 1)),
        "silenced_dn_no_spikes": bool(quiet["dn_counts"].sum() == 0),
        "silenced_upstream_still_active": bool(quiet["counts"].sum() > 0),
        "silenced_command_reference": bool(np.all(quiet["applied_command"] == 1)),
        "no_edges_no_dn_spikes": bool(ablated["dn_counts"].sum() == 0),
        "no_edges_upstream_still_active": bool(ablated["counts"].sum() > 0),
        "controls_same_physics": np.array_equal(disconnected["qpos_native"], quiet["qpos_native"]) and
                                 np.array_equal(disconnected["qpos_native"], ablated["qpos_native"]),
        "silencing_changes_command": bool(np.max(np.abs(baseline["applied_command"] - quiet["applied_command"])) > 0.01),
        "silencing_changes_trajectory": bool(np.max(np.linalg.norm(baseline["thorax_m"] - quiet["thorax_m"], axis=1)) > 0.00005),
        "disconnection_changes_heading": bool(np.max(np.abs(baseline["heading_rad"] - disconnected["heading_rad"])) > np.deg2rad(1)),
        "movement_changes_retinal_feedback": bool(np.max(np.abs(baseline["ommatidia"][2:] - disconnected["ommatidia"][2:])) > 1 / 255),
    })
    measurements = {
        "repeat_qpos_max_abs": float(np.max(np.abs(baseline["qpos_native"] - repeated["qpos_native"]))),
        "repeat_retina_max_abs": float(np.max(np.abs(baseline["ommatidia"] - repeated["ommatidia"]))),
        "connected_vs_disconnected_max_position_delta_mm": float(np.max(np.linalg.norm(baseline["thorax_m"] - disconnected["thorax_m"], axis=1)) * 1000),
        "connected_vs_disconnected_max_heading_delta_deg": float(np.rad2deg(np.max(np.abs(baseline["heading_rad"] - disconnected["heading_rad"])))),
        "input_rates_range_hz": [baseline["input_rates_hz"].min(axis=0).tolist(), baseline["input_rates_hz"].max(axis=0).tolist()],
    }
    return checks, measurements


def render_results(output, runs, frames, settings):
    import imageio.v2 as imageio
    import matplotlib.pyplot as plt
    from PIL import Image, ImageDraw, ImageFont

    baseline = runs["connected"]
    t = baseline["bin_start_s"]
    colors = {"connected": "#098568", "disconnected": "#bb5444", "silenced": "#5b5b66", "no_vision": "#aa7a13"}
    fig, axes = plt.subplots(2, 2, figsize=(13, 8), layout="constrained")
    for name, color in colors.items():
        state = runs[name]
        xy = state["thorax_m"][:, :2] * 1000
        axes[0, 0].plot(xy[:, 0], xy[:, 1], label=name, color=color)
        axes[0, 0].scatter(*xy[-1], color=color, s=18)
    axes[0, 0].set(xlabel="x [mm]", ylabel="y [mm]", title="MuJoCo trajectory", aspect="equal")
    axes[0, 0].legend(fontsize=8)
    names = {root: f"{kind} {side}" for root, kind, side in CELLS}
    axes[0, 1].scatter(baseline["spike_times_s"], baseline["spike_indices"], s=18, marker="|", color="#205c48")
    axes[0, 1].set(yticks=np.arange(4), yticklabels=[names[root] for root in baseline["neuron_ids"]],
                   xlabel="time [s]", title="Spikes in the real 4-cell fragment")
    for i, side, color in ((0, "left", "#098568"), (1, "right", "#bb5444")):
        axes[1, 0].step(t, baseline["input_rates_hz"][:, i], where="post", label=side, color=color)
        axes[1, 1].step(t, baseline["applied_command"][:, i], where="post", label=f"connected {side}", color=color)
    axes[1, 0].set(xlabel="time [s]", ylabel="Poisson input [Hz]", title="Surrogate visual drive into DNa03")
    axes[1, 1].plot(t, runs["disconnected"]["applied_command"][:, 0], "--", color="#5b5b66", label="disconnected / silenced / no edges")
    axes[1, 1].set(xlabel="time [s]", ylabel="CPG amplitude [unitless]", ylim=(0.3, 1.05), title="Command applied to legs (one-bin delay)")
    for ax in axes.flat:
        ax.grid(alpha=0.18)
    axes[1, 0].legend(fontsize=8)
    axes[1, 1].legend(fontsize=8)
    fig.suptitle("FlyLab | Stage 5 technical closed loop | surrogate vision and VNC interfaces", fontsize=14)
    fig.savefig(output / "comparison.png", dpi=150)
    plt.close(fig)

    names_video = ("connected", "disconnected", "silenced")
    font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 18)
    small = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 14)
    composed = []
    for index in range(len(frames["connected"])):
        canvas = Image.new("RGB", (1200, 420), "#f4f5f5")
        draw = ImageDraw.Draw(canvas)
        draw.text((16, 10), f"FlyLab 05  |  t = {t[index]:.2f} s  |  10x slower playback", fill="#222222", font=font)
        draw.text((16, 36), "Real FlyWire fragment: 4 neurons. Visual input + leg controller are surrogate models.", fill="#555555", font=small)
        for column, name in enumerate(names_video):
            x = column * 400
            canvas.paste(Image.fromarray(frames[name][index]).resize((400, 300)), (x, 90))
            command = runs[name]["applied_command"][index]
            count = runs[name]["dn_counts"][index]
            draw.text((x + 14, 66), name.upper(), fill=colors[name], font=font)
            draw.text((x + 14, 394), f"CPG L/R {command[0]:.2f}/{command[1]:.2f}  |  DN spikes {count[0]}/{count[1]}", fill="#333333", font=small)
        composed.append(np.asarray(canvas))
    imageio.imwrite(output / "preview.png", composed[len(composed) // 2])
    fps = 0.1 / settings["sensory_dt_s"]
    with imageio.get_writer(output / "closed-loop.mp4", fps=fps, codec="libx264", quality=8, macro_block_size=2) as writer:
        for frame in composed:
            writer.append_data(frame)
    decoded = []
    with imageio.get_reader(output / "closed-loop.mp4") as reader:
        actual_fps = reader.get_meta_data()["fps"]
        for frame in reader:
            decoded.append(frame)
    metrics = []
    for column in range(3):
        crop = np.asarray([frame[90:390, column * 400:(column + 1) * 400] for frame in decoded], dtype=np.float32)
        metrics.append({"panel": names_video[column], "pixel_std": float(crop[len(crop) // 2].std()),
                        "mean_frame_motion": float(np.abs(np.diff(crop, axis=0)).mean())})
    result = {"frames": len(decoded), "expected_frames": len(composed), "fps": actual_fps,
              "size": list(decoded[0].shape), "panels": metrics,
              "passed": len(decoded) == len(composed) and abs(actual_fps - fps) < 0.001 and
                        all(m["pixel_std"] > 5 and m["mean_frame_motion"] > 0.1 for m in metrics)}
    write_json(output / "media-validation.json", result)
    return result


def run(config, cache, data_dir, output):
    settings, parameters = validate_configuration(config)
    output.mkdir(parents=True, exist_ok=True)
    write_json(output / "report.json", {"stage": 5, "status": "running", "biologically_validated": False})
    graph, mapping = load_steering_circuit(cache, data_dir)
    write_json(output / "mapping.json", mapping)
    write_json(output / "settings.json", config)
    np.savez_compressed(output / "circuit.npz", ids=graph.ids, nt=graph.nt, pre=graph.pre, post=graph.post, counts=graph.counts)
    print(f"Verified circuit: {len(graph.ids)} neurons, {len(graph.pre)} pairs, {graph.counts.sum()} contacts", flush=True)
    rig = SensorRig(settings)
    try:
        runs, reports, frames = {}, {}, {}
        for mode in MODES:
            print(f"Running {mode}...", flush=True)
            runs[mode], reports[mode], frames[mode] = episode(rig, graph, parameters, mode, output,
                                                            mode in ("connected", "disconnected", "silenced"))
    finally:
        rig.close()
    checks, measurements = compare_runs(runs, parameters)
    checks["recorded_input_replays_neural_state_exactly"] = replay_neural_input(graph, settings, runs["connected"], output)
    media = render_results(output, runs, frames, settings)
    checks["encoded_video_nonblank_and_moving"] = media["passed"]
    write_json(output / "report.json", {
        "stage": 5, "status": "passed" if all(checks.values()) else "failed",
        "biologically_validated": False, "full_brain": False, "checks": checks, "measurements": measurements,
        "runs": reports, "mapping_sha256": mapping["mapping_sha256"], "config_sha256": digest(config),
        "neural_parameters_si": asdict(ModelParameters()), "coupling_parameters": asdict(parameters),
        "timing": {"policy": "sample sensors at t; advance brain/body [t,t+dt); apply new DN command from t+dt",
                   "warmup_excluded_s": settings["warmup_s"], "control_dt_s": settings["sensory_dt_s"],
                   "terminal_observation_not_applied": True},
        "units": {"thorax_m": "m", "qpos_native": "MuJoCo mm/rad/quaternion", "joint_targets_rad": "rad",
                  "input_rates_hz": "Hz", "applied_command": "unitless CPG stride amplitude, left/right"},
        "limitations": ["Four-cell steering fragment, not a whole-brain simulation",
                        "Darkening difference directly drives DNa03 by hypothesis, not reconstructed sensory anatomy",
                        "Tonic 20 Hz and gains are engineering choices, not biological calibration",
                        "Forward gait and adhesion are supplied by an independent CPG, even when brain is disconnected",
                        "Only visual features close the brain loop; contact/proprioception are recorded but not wired to brain",
                        "No VNC, no neural motor units, no flight, no behavioral validation, one seed only"],
        "versions": {name: importlib.metadata.version(name) for name in ("flygym", "mujoco", "brian2", "numpy", "scipy", "imageio")},
        "code_sha256": {name: file_sha256(ROOT / name) for name in (
            "closed_loop_experiment.py", "closed_loop_model.py", "brain_model.py", "sensory_experiment.py", "sensory_model.py", "flywire_data.py")},
        "artifact_sha256": {name: file_sha256(output / name) for name in (
            "mapping.json", "settings.json", "circuit.npz", "neural-replay.npz", "comparison.png", "closed-loop.mp4", "media-validation.json")},
    })
    failed = [name for name, passed in checks.items() if not passed]
    print(json.dumps({"passed": not failed, "checks": len(checks), "failed": failed, "measurements": measurements}), flush=True)
    if failed:
        raise RuntimeError("Closed-loop checks failed; inspect report.json")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "experiments/closed-loop.json")
    parser.add_argument("--cache", type=Path, default=ROOT / "outputs/flylab/connectome-cache")
    parser.add_argument("--data-dir", type=Path, default=Path.home() / "Downloads/mind")
    parser.add_argument("--output", type=Path, default=ROOT / "outputs/flylab/closed-loop-stage5")
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    run(config, args.cache, args.data_dir, args.output)


if __name__ == "__main__":
    main()
