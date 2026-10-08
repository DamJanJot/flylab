"""Reproducible multi-seed assays, physical obstacle contacts and resumable checkpoints."""

import argparse
from dataclasses import asdict
import importlib.metadata
import json
from pathlib import Path
import time

import numpy as np

from flylab import ROOT, digest, write_json
from flywire_data import file_sha256
from sensory_experiment import SensorRig
from closed_loop_experiment import episode
from closed_loop_model import OUTPUT_IDS, load_steering_circuit
from brain_model import ModelParameters
from behavior_model import Trial, TrialProtocol, SCENARIOS, CONDITIONS, trial_plan, validate_suite, behavior_metrics, summarize_trials


CODE_FILES = ("behavior_model.py", "behavior_experiment.py", "closed_loop_experiment.py", "closed_loop_model.py",
              "sensory_experiment.py", "sensory_model.py", "brain_model.py", "flywire_data.py", "flylab.py")


def write_checkpoint(path, manifest):
    """Replace only a complete manifest; interrupted writes leave the previous one usable."""
    pending = path.with_suffix(".pending.json")
    write_json(pending, manifest)
    pending.replace(path)


class BehaviorRig(SensorRig):
    """FlyGym builds explicit collision pairs for every ground geom, including the box."""

    def __init__(self, settings, obstacle=None):
        import mujoco
        from flygym.compose import FlatGroundWorld

        world = FlatGroundWorld()
        box = None
        if obstacle is not None:
            box = world.mjcf_root.worldbody.add_geom(
                name="behavior_obstacle", type=mujoco.mjtGeom.mjGEOM_BOX,
                pos=obstacle["center_mm"], size=obstacle["half_size_mm"],
                rgba=(0.2, 0.65, 0.62, 1), contype=0, conaffinity=0)
            world.ground_geoms.append(box)
        top = world.mjcf_root.worldbody.add_camera(name="behavior_top", pos=(5, 0, 18), fovy=45)
        super().__init__(settings, world=world)
        self.obstacle_id = self.sim.mj_model.geom(box.name).id if box is not None else -1
        self.top_camera_id = self.sim.mj_model.camera(top.name).id
        self.ground_ids = np.array([self.sim.mj_model.geom(g.name).id for g in world.ground_geoms])
        self.geom_to_leg = np.full(self.sim.mj_model.ngeom, -1, dtype=int)
        self.fly_geom = np.zeros(self.sim.mj_model.ngeom, dtype=bool)
        for segment, geoms in self.fly.bodyseg_to_mjcfgeom.items():
            for geom in geoms:
                index = self.sim.mj_model.geom(geom.name).id
                self.fly_geom[index] = True
                if segment.is_leg():
                    self.geom_to_leg[index] = self.legs.index(segment.pos)
        self.physics_rows = []
        self.obstacle_pairs = int(np.count_nonzero(
            (self.sim.mj_model.pair_geom1 == self.obstacle_id) | (self.sim.mj_model.pair_geom2 == self.obstacle_id))) if box is not None else 0
        if box is not None and self.obstacle_pairs == 0:
            self.close()
            raise RuntimeError("Obstacle has no physical collision pairs")

    def contact_snapshot(self):
        data = self.sim.mj_data
        contact = data.contact
        active = (contact.dist[:data.ncon] <= 0) & (contact.efc_address[:data.ncon] >= 0)
        first, second = contact.geom1[:data.ncon][active], contact.geom2[:data.ncon][active]
        flags = np.zeros(6, dtype=bool)
        other_ground = np.isin(first, self.ground_ids)
        other_ground_reverse = np.isin(second, self.ground_ids)
        fly_indices = np.concatenate([second[other_ground], first[other_ground_reverse]])
        legs = self.geom_to_leg[fly_indices]
        flags[legs[legs >= 0]] = True
        partners = np.concatenate([second[first == self.obstacle_id], first[second == self.obstacle_id]])
        partners = partners[self.fly_geom[partners]]
        return flags, bool(len(partners)), bool(np.any(self.geom_to_leg[partners] < 0))

    def contact_flags(self):
        # FlyGym's native per-leg sensors cover only single-geom worlds.
        # Use the same contact-list measurement on flat and obstacle trials.
        return self.contact_snapshot()[0]

    def record_physics(self):
        import mujoco

        mujoco.mj_forward(self.sim.mj_model, self.sim.mj_data)
        flags, obstacle, body_obstacle = self.contact_snapshot()
        quat = self.sim.get_body_rotations(self.fly.name)[self.thorax]
        self.physics_rows.append({
            "time_s": float(self.sim.mj_data.time - self.settings["warmup_s"]),
            "thorax_m": (self.sim.get_body_positions(self.fly.name)[self.thorax] / 1000).copy(),
            "up_axis_z": float(1 - 2 * (quat[1] ** 2 + quat[2] ** 2)),
            "leg_contacts": flags, "obstacle_contact": obstacle, "body_obstacle_contact": body_obstacle,
        })
        if not np.isfinite(self.sim.mj_data.qpos).all() or not np.isfinite(self.sim.mj_data.qvel).all():
            raise RuntimeError("Nonfinite physical state")

    def frame(self):
        side = super().frame()
        self.overview.update_scene(self.sim.mj_data, camera=self.top_camera_id)
        return np.concatenate([side, self.overview.render().copy()], axis=1)

    def physics_arrays(self):
        return {key: np.asarray([row[key] for row in self.physics_rows]) for key in self.physics_rows[0]}


def should_capture(trial, first_seed):
    return not trial.repeat and trial.seed == first_seed and ((trial.scenario in ("left", "right", "obstacle") and trial.condition == "connected")
                                                              or (trial.scenario == "obstacle" and trial.condition == "disconnected"))


def save_video(frames, directory, trial, dt):
    import imageio.v2 as imageio
    from PIL import Image, ImageDraw, ImageFont

    path = directory / "preview.mp4"
    font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 20)
    small = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 16)
    with imageio.get_writer(path, fps=0.1 / dt, codec="libx264", quality=8, macro_block_size=2) as video:
        for index, frame in enumerate(frames):
            canvas = Image.new("RGB", (1280, 544), "#f5f6f6")
            canvas.paste(Image.fromarray(frame), (0, 64))
            draw = ImageDraw.Draw(canvas)
            draw.text((18, 8), f"FlyLab 06 | {trial.scenario} | {trial.condition} | seed {trial.seed} | t={index * dt:.2f} s", fill="#222222", font=font)
            draw.text((18, 37), "MuJoCo side + fixed overhead view | 4-cell circuit / surrogate visual and motor interfaces | 10x slower", fill="#555555", font=small)
            pixels = np.asarray(canvas)
            video.append_data(pixels)
            if index == len(frames) // 2:
                imageio.imwrite(directory / "preview.png", pixels)
    count, motion, variance = 0, np.zeros(2), np.zeros(2)
    previous = None
    with imageio.get_reader(path) as reader:
        fps = reader.get_meta_data()["fps"]
        for frame in reader:
            for i in range(2):
                crop = frame[64:, i * 640:(i + 1) * 640].astype(float)
                variance[i] = max(variance[i], crop.std())
                if previous is not None:
                    motion[i] += np.abs(crop - previous[64:, i * 640:(i + 1) * 640]).mean()
            previous = frame.astype(float)
            count += 1
    result = {"frames": count, "fps": fps, "pixel_std_by_view": variance.tolist(),
              "mean_frame_motion_by_view": (motion / max(1, count - 1)).tolist(),
              "passed": count == len(frames) and abs(fps - 0.1 / dt) < 1e-6 and bool(np.all(variance > 5) and np.all(motion > 0.1))}
    write_json(directory / "media-validation.json", result)
    return result


def technical_checks(state, physics, trial, config, warnings):
    dt = config["simulation"]["sensors"]["physics_dt_s"]
    duration = config["simulation"]["sensors"]["duration_s"]
    n = round(duration / dt)
    checks = {
        "finite_arrays": all(bool(np.isfinite(value).all()) for value in (*state.values(), *physics.values()) if value.dtype.kind in "fiu"),
        "physics_samples_complete": len(physics["time_s"]) == n,
        "physics_clock_aligned": bool(np.allclose(physics["time_s"], np.arange(1, n + 1) * dt, atol=1e-9, rtol=0)),
        "physics_endpoint_matches_observation": bool(np.array_equal(physics["thorax_m"][-1], state["thorax_m"][-1])),
        "one_bin_command_latency": bool(np.array_equal(state["applied_command"][0], [1, 1]) and np.array_equal(state["applied_command"][1:], state["next_command"][:-1])),
        "spike_accounting": bool(np.array_equal(state["counts"].sum(axis=0), state["spike_counts"])),
        "valid_spike_indices": bool(np.all((state["spike_indices"] >= 0) & (state["spike_indices"] < len(state["neuron_ids"])))),
        "spike_times_within_episode": bool(np.all((state["spike_times_s"] >= 0) & (state["spike_times_s"] < duration))),
        "no_mujoco_warnings": not any(warnings),
    }
    minimum = 1 - config["simulation"]["coupling"]["maximum_stride_reduction"]
    checks["command_bounds"] = bool(np.all(state["applied_command"] >= minimum - 1e-12) and np.all(state["applied_command"] <= 1))
    source_is_dn = state["input_indices"] >= 2
    source_times = state["input_times_s"][source_is_dn]
    if trial.condition.startswith("stimulate_"):
        selected = 2 if trial.condition.endswith("left") else 3
        checks["targeted_stimulation_window"] = bool(len(source_times) > 0 and np.all(source_times >= config["intervention_start_s"] - 1e-12)
                                                     and np.all(source_times < config["intervention_end_s"])
                                                     and np.all(state["input_indices"][source_is_dn] == selected))
    else:
        checks["no_unrequested_direct_dn_input"] = len(source_times) == 0
    if trial.condition == "disconnected":
        checks["reference_command_when_disconnected"] = bool(np.all(state["applied_command"] == 1))
    if trial.condition == "silenced":
        checks["silenced_outputs_quiet"] = int(state["dn_counts"].sum()) == 0
        checks["silenced_reference_command"] = bool(np.all(state["applied_command"] == 1))
    return checks


def trial_directory(output, trial):
    return output / "trials" / trial.key


def recording_path(output, trial):
    mode = trial.condition if trial.condition in CONDITIONS else "connected"
    return trial_directory(output, trial) / mode / "recording.npz"


def run_trial(config, graph, trial, output):
    settings, parameters = validate_suite(config)
    settings = {**settings, "seed": trial.seed}
    directory = trial_directory(output, trial)
    directory.mkdir(parents=True, exist_ok=True)
    write_json(directory / "trial.json", {"trial": asdict(trial), "configuration": config})
    rig = BehaviorRig(settings, config["obstacle"] if trial.scenario == "obstacle" else None)
    capture = should_capture(trial, config["seeds"][0])
    mode = trial.condition if trial.condition in CONDITIONS else "connected"
    try:
        state, episode_report, frames = episode(rig, graph, parameters, mode, directory, capture=capture,
                                               protocol=TrialProtocol(config, trial), physics_observer=rig.record_physics)
        physics = rig.physics_arrays()
        warnings = np.asarray(rig.sim.mj_data.warning.number).astype(int).tolist()
        pairs = rig.obstacle_pairs
    finally:
        rig.close()
    np.savez_compressed(directory / "physics.npz", **physics)
    checks = technical_checks(state, physics, trial, config, warnings)
    if trial.scenario == "obstacle":
        checks["obstacle_has_collision_pairs"] = pairs > 0
    if capture:
        media = save_video(frames, directory, trial, settings["sensory_dt_s"])
        checks["rendered_views_nonblank_and_changing"] = media["passed"]
    behavior = behavior_metrics(state, physics, config, trial.scenario)
    files = [directory / "trial.json", directory / "physics.npz", recording_path(output, trial)]
    if capture:
        files += [directory / name for name in ("preview.mp4", "preview.png", "media-validation.json")]
    report = {"trial": asdict(trial), "technical_status": "passed" if all(checks.values()) else "failed",
              "checks": checks, "behavior": behavior, "mujoco_warnings": warnings, "obstacle_collision_pairs": pairs,
              "episode": episode_report, "files": {str(p.relative_to(directory).as_posix()): file_sha256(p) for p in files}}
    write_json(directory / "result.json", report)
    if not all(checks.values()):
        raise RuntimeError(f"Technical checks failed in {trial.key}: {[k for k, v in checks.items() if not v]}")
    return report


def load_verified_result(directory, expected_hash):
    path = directory / "result.json"
    if file_sha256(path) != expected_hash:
        raise ValueError("Checkpoint result checksum mismatch")
    report = json.loads(path.read_text(encoding="utf-8"))
    if report["technical_status"] != "passed" or not all(report["checks"].values()):
        raise ValueError("Cannot reuse a failed technical trial")
    for relative, checksum in report["files"].items():
        target = (directory / relative).resolve()
        if not target.is_relative_to(directory.resolve()) or file_sha256(target) != checksum:
            raise ValueError("Checkpoint artifact checksum mismatch")
    return report


def load_recording(output, trial):
    with np.load(recording_path(output, trial), allow_pickle=False) as data:
        return {key: data[key] for key in data.files}


def comparison_checks(reports, config, output):
    checks, repeat_metrics = {}, {}
    for scenario in SCENARIOS:
        for seed in config["seeds"]:
            disconnected = load_recording(output, Trial(scenario, "disconnected", seed))
            silenced = load_recording(output, Trial(scenario, "silenced", seed))
            checks[f"{scenario}_{seed}_disconnected_equals_silenced_physics"] = bool(np.array_equal(disconnected["qpos_native"], silenced["qpos_native"]))
    for scenario in ("left", "obstacle"):
        first = Trial(scenario, "connected", config["seeds"][0])
        repeated = Trial(scenario, "connected", config["seeds"][0], True)
        a, b = load_recording(output, first), load_recording(output, repeated)
        fields = ("qpos_native", "applied_command", "spike_indices", "spike_times_s", "input_rates_hz")
        checks[f"{scenario}_repeat_neural_and_body_exact"] = all(np.array_equal(a[key], b[key]) for key in fields)
        retina_delta = float(np.max(np.abs(a["ommatidia"] - b["ommatidia"])))
        checks[f"{scenario}_repeat_retina_tolerance"] = retina_delta <= 1 / 255
        repeat_metrics[scenario] = {"retina_max_delta": retina_delta, "qpos_max_delta": float(np.max(np.abs(a["qpos_native"] - b["qpos_native"])))}
    checks["obstacle_physically_contacted_in_suite"] = any(report["behavior"]["obstacle_contact_s"] > 0 for report in reports.values())
    checks["all_planned_trials_present"] = set(reports) == {trial.key for trial in trial_plan(config)}
    return checks, repeat_metrics


def plot_summary(summary, config, output):
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle

    colors = {"connected": "#098568", "disconnected": "#bc5848", "silenced": "#686a75"}
    fig, axes = plt.subplots(2, 2, figsize=(12, 9), layout="constrained")
    for ax, scenario in zip(axes.flat, SCENARIOS):
        for condition in CONDITIONS:
            for i, seed in enumerate(config["seeds"]):
                state = load_recording(output, Trial(scenario, condition, seed))
                xy = state["thorax_m"][:, :2] * 1000
                ax.plot(xy[:, 0], xy[:, 1], color=colors[condition], alpha=0.7,
                        linestyle="--" if condition == "silenced" else "-", label=condition if i == 0 else None)
        if scenario == "obstacle":
            center, half = config["obstacle"]["center_mm"], config["obstacle"]["half_size_mm"]
            ax.add_patch(Rectangle((center[0] - half[0], center[1] - half[1]), 2 * half[0], 2 * half[1], color="#289ca0", alpha=0.4))
        ax.axvline(config["outcome"]["gate_x_mm"], linestyle=":", color="#777777", linewidth=1)
        ax.set(title=scenario, xlabel="x [mm]", ylabel="y [mm]", aspect="equal")
        ax.grid(alpha=0.15)
        ax.legend(fontsize=8)
    fig.suptitle("Stage 6 | All seeds, no exclusions | 4-cell steering model", fontsize=14)
    fig.savefig(output / "trajectories.png", dpi=150)
    plt.close(fig)

    fig, axes = plt.subplots(1, 2, figsize=(12, 5), layout="constrained")
    for index, scenario in enumerate(SCENARIOS):
        rows = [p for p in summary["paired"] if p["scenario"] == scenario]
        values = [p["connected_minus_disconnected_heading_deg"] for p in rows]
        axes[0].scatter(np.full(len(values), index), values, color="#098568", s=45)
        axes[0].plot([index - 0.16, index + 0.16], [np.mean(values)] * 2, color="#222222", linewidth=2)
    axes[0].axhline(0, color="#888888", linewidth=1)
    axes[0].set(xticks=range(4), xticklabels=SCENARIOS, ylabel="paired heading difference [deg]", title="Connected minus disconnected")
    for index, condition in enumerate(("connected", "stimulate_left", "stimulate_right")):
        group = next(g for g in summary["groups"] if g["scenario"] == "neutral" and g["condition"] == condition)
        axes[1].scatter(np.full(group["n"], index), group["heading_deg_per_seed"], s=45,
                        color=("#098568", "#ba6b28", "#4161a1")[index])
    axes[1].axhline(0, color="#888888", linewidth=1)
    axes[1].set(xticks=range(3), xticklabels=("no intervention", "stimulate L", "stimulate R"), ylabel="final heading change [deg]", title="Unilateral DNa02 input, 0.2-0.6 s")
    for ax in axes:
        ax.grid(alpha=0.15)
    fig.suptitle("Each dot = one seed | descriptive model outcomes, not biological replicates", fontsize=12)
    fig.savefig(output / "outcomes.png", dpi=150)
    plt.close(fig)


def run_suite(config, cache, data_dir, output, resume=False, max_trials=None):
    validate_suite(config)
    plan = trial_plan(config)
    if max_trials is not None and (type(max_trials) is not int or max_trials < 1):
        raise ValueError("max_trials must be positive")
    graph, mapping = load_steering_circuit(cache, data_dir)
    identity = {"config": config, "mapping_sha256": mapping["mapping_sha256"],
                "code_sha256": {name: file_sha256(ROOT / name) for name in CODE_FILES},
                "neural_parameters_si": asdict(ModelParameters()),
                "versions": {name: importlib.metadata.version(name) for name in ("flygym", "mujoco", "brian2", "numpy", "scipy", "imageio")}}
    fingerprint = digest(identity)
    manifest_path = output / "manifest.json"
    if manifest_path.exists():
        if not resume:
            raise ValueError("Existing suite; use --resume or a fresh output directory")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest["fingerprint"] != fingerprint or digest(manifest["identity"]) != fingerprint:
            raise ValueError("Resume refused: code, settings, packages or data changed; choose a fresh output")
        if manifest["plan"] != [asdict(t) for t in plan] or not set(manifest["completed"]) <= {t.key for t in plan}:
            raise ValueError("Resume plan mismatch")
        for name, checksum in manifest["source_artifacts"].items():
            if name not in ("mapping.json", "settings.json", "circuit.npz") or file_sha256(output / name) != checksum:
                raise ValueError("Resume source artifact checksum mismatch")
        if set(manifest["source_artifacts"]) != {"mapping.json", "settings.json", "circuit.npz"}:
            raise ValueError("Resume source artifact set mismatch")
    else:
        if output.exists() and any(output.iterdir()):
            raise ValueError("Output is not empty and has no valid suite manifest")
        output.mkdir(parents=True, exist_ok=True)
        manifest = {"stage": 6, "status": "running", "fingerprint": fingerprint, "identity": identity,
                    "plan": [asdict(t) for t in plan], "completed": {}, "planned_trials": len(plan)}
        write_json(output / "mapping.json", mapping)
        write_json(output / "settings.json", config)
        np.savez_compressed(output / "circuit.npz", ids=graph.ids, nt=graph.nt, pre=graph.pre, post=graph.post, counts=graph.counts)
        manifest["source_artifacts"] = {name: file_sha256(output / name) for name in ("mapping.json", "settings.json", "circuit.npz")}
        write_checkpoint(manifest_path, manifest)
    reports = {key: load_verified_result(output / "trials" / key, checksum) for key, checksum in manifest["completed"].items()}
    for trial in plan:
        if trial.key in reports and reports[trial.key]["trial"] != asdict(trial):
            raise ValueError("Resume trial identity mismatch")
    manifest.pop("error", None)
    executed = 0
    started = time.perf_counter()
    try:
        for trial in plan:
            if trial.key in reports:
                continue
            if max_trials is not None and executed >= max_trials:
                break
            manifest.update(status="running", current_trial=trial.key)
            write_checkpoint(manifest_path, manifest)
            print(f"[{len(reports) + 1}/{len(plan)}] {trial.key}", flush=True)
            result = run_trial(config, graph, trial, output)
            reports[trial.key] = result
            manifest["completed"][trial.key] = file_sha256(trial_directory(output, trial) / "result.json")
            executed += 1
            write_checkpoint(manifest_path, manifest)
        if len(reports) < len(plan):
            manifest.update(status="partial", current_trial=None)
            write_checkpoint(manifest_path, manifest)
            print(json.dumps({"status": "partial", "completed": len(reports), "planned": len(plan)}), flush=True)
            return
        summary = summarize_trials(reports, config)
        checks, repeat = comparison_checks(reports, config, output)
        plot_summary(summary, config, output)
        report = {"stage": 6, "status": "passed" if all(checks.values()) else "failed", "biologically_validated": False,
                  "fingerprint": fingerprint, "trials": len(reports), "seeds": config["seeds"], "checks": checks,
                  "per_trial_checks": sum(len(r["checks"]) for r in reports.values()), "repeat_measurements": repeat,
                  "outcomes": summary, "session_wall_s": time.perf_counter() - started,
                  "limitations": ["Four-neuron fragment, surrogate sensory and CPG interfaces remain unchanged",
                                  "Three seeds by default; descriptive statistics only, no population inference",
                                  "Gate records thorax crossing, not whole-body clearance or intelligent obstacle avoidance",
                                  "All falls and failures retained; success of test harness does not imply task success",
                                  "Brain and CPG seeds co-vary; they are not independently estimated sources of variance"],
                  "artifact_sha256": {name: file_sha256(output / name) for name in ("mapping.json", "settings.json", "circuit.npz", "trajectories.png", "outcomes.png")}}
        write_json(output / "report.json", report)
        manifest.update(status=report["status"], current_trial=None, report_sha256=file_sha256(output / "report.json"))
        write_checkpoint(manifest_path, manifest)
        print(json.dumps({"status": report["status"], "trials": len(reports), "checks": len(checks), "failed": [k for k, v in checks.items() if not v]}), flush=True)
        if not all(checks.values()):
            raise RuntimeError("Suite integration checks failed")
    except Exception as error:
        manifest.update(status="failed", error=f"{type(error).__name__}: {error}")
        write_checkpoint(manifest_path, manifest)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "experiments/behavior-suite.json")
    parser.add_argument("--cache", type=Path, default=ROOT / "outputs/flylab/connectome-cache")
    parser.add_argument("--data-dir", type=Path, default=Path.home() / "Downloads/mind")
    parser.add_argument("--output", type=Path, default=ROOT / "outputs/flylab/behavior-stage6")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--max-trials", type=int, help="Bound this invocation; resume the remaining declared trials later")
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    run_suite(config, args.cache, args.data_dir, args.output, args.resume, args.max_trials)


if __name__ == "__main__":
    main()
