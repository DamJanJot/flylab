"""Stage 4: native FlyGym sensors, controlled interventions and offline neural replay."""

import argparse
from dataclasses import asdict
from decimal import Decimal
import importlib.metadata
import json
import math
import os
from pathlib import Path
import time

from flylab import ROOT, digest, write_json
from flywire_data import file_sha256
from sensory_model import EncoderParameters, SensoryEncoder, replay_relays

os.environ.setdefault("MPLCONFIGDIR", str(ROOT / "work/sensory-cache/matplotlib"))
os.environ.setdefault("NUMBA_CACHE_DIR", str(ROOT / "work/sensory-cache/numba"))
os.environ.setdefault("NUMBA_NUM_THREADS", "2")
os.environ.setdefault("MPLBACKEND", "Agg")

import numpy as np


def validate_settings(settings):
    required = {"schema_version", "experiment_id", "seed", "duration_s", "physics_dt_s", "sensory_dt_s", "brain_dt_s", "warmup_s", "encoder"}
    if not isinstance(settings, dict) or set(settings) != required:
        raise ValueError("Invalid sensory configuration fields")
    if type(settings["schema_version"]) is not int or settings["schema_version"] != 1:
        raise ValueError("Unsupported sensory schema")
    if not isinstance(settings["experiment_id"], str) or not settings["experiment_id"]:
        raise ValueError("Missing experiment ID")
    if type(settings["seed"]) is not int or not 0 <= settings["seed"] < 2**32:
        raise ValueError("Invalid seed")
    for key in ("duration_s", "physics_dt_s", "sensory_dt_s", "brain_dt_s", "warmup_s"):
        if type(settings[key]) not in (float, int) or not math.isfinite(settings[key]) or settings[key] <= 0:
            raise ValueError(f"Invalid clock: {key}")
    d = {key: Decimal(str(settings[key])) for key in ("duration_s", "physics_dt_s", "sensory_dt_s", "brain_dt_s", "warmup_s")}
    for numerator, denominator in (("duration_s", "sensory_dt_s"), ("sensory_dt_s", "physics_dt_s"),
                                    ("sensory_dt_s", "brain_dt_s"), ("warmup_s", "physics_dt_s")):
        if d[numerator] % d[denominator]:
            raise ValueError("Clocks must have integer ratios")
    if not 0.1 <= settings["duration_s"] <= 2 or settings["physics_dt_s"] != 0.0001:
        raise ValueError("Diagnostic duration is 0.1..2s; physics dt must be 0.0001s")
    if settings["duration_s"] / settings["sensory_dt_s"] > 200 or settings["sensory_dt_s"] > 0.05 or settings["warmup_s"] > 0.2:
        raise ValueError("Sensory diagnostic exceeds sampling/warmup budget")
    if not isinstance(settings["encoder"], dict) or set(settings["encoder"]) != set(asdict(EncoderParameters())):
        raise ValueError("Invalid encoder parameter fields")
    EncoderParameters(**settings["encoder"]).validate()
    if settings["encoder"]["maximum_rate_hz"] * settings["brain_dt_s"] > 0.1:
        raise ValueError("Encoder rate exceeds Poisson timestep limit")
    from brain_model import ModelParameters
    ModelParameters().validate(settings["brain_dt_s"])
    if settings["duration_s"] / settings["brain_dt_s"] > 1_000_000:
        raise ValueError("Replay exceeds diagnostic step budget")
    return settings


class SensorRig:
    def __init__(self, settings, world=None):
        import mujoco
        from flygym import Simulation
        from flygym.anatomy import BodySegment
        from flygym.compose import FlatGroundWorld
        from flygym.utils.math import Rotation3D
        from flygym_demo.complex_terrain import make_locomotion_fly, PreprogrammedSteps

        self.settings = validate_settings(settings)
        self.fly = make_locomotion_fly(name="sensory_fly", colorize=True, add_adhesion=True)
        self.fly.add_vision()
        self.camera = self.fly.add_tracking_camera(name="sensory_view", pos_offset=(-0.5, -3.5, 0),
                                                   rotation=Rotation3D("euler", (1.57, 0, 0)), fovy=70)
        self.world = FlatGroundWorld() if world is None else world
        self.panels = []
        for side, y in (("left", 5), ("right", -5)):
            self.panels.append(self.world.mjcf_root.worldbody.add_geom(
                name=f"stimulus_{side}", type=mujoco.mjtGeom.mjGEOM_BOX,
                pos=(1, y, 2), size=(3, 0.05, 3), rgba=(0.02, 0.02, 0.02, 0),
                contype=0, conaffinity=0, mass=0, group=0))
        self.world.add_fly(self.fly, [0, 0, 0.5], Rotation3D("quat", [1, 0, 0, 0]))
        self.sim = Simulation(self.world, timestep=settings["physics_dt_s"])
        self.overview = None
        try:
            self.panel_ids = [self.sim.mj_model.geom(p.name).id for p in self.panels]
            self.overview = mujoco.Renderer(self.sim.mj_model, height=480, width=640)
            self.camera_id = self.sim.mj_model.camera(self.camera.name).id
            self.dofs = self.fly.get_actuated_jointdofs_order("position")
            all_dofs = self.fly.get_jointdofs_order()
            self.active_indices = np.array([all_dofs.index(dof) for dof in self.dofs])
            self.all_joint_names = [dof.name for dof in all_dofs]
            self.joint_names = [dof.name for dof in self.dofs]
            self.legs = list(self.fly.get_legs_order())
            self.thorax = self.fly.get_bodysegs_order().index(BodySegment("c_thorax"))
            self.steps = PreprogrammedSteps()
        except Exception:
            self.close()
            raise

    def close(self):
        if self.overview is not None:
            self.overview.close()
        self.sim.close()

    def set_panels(self, left=False, right=False):
        self.sim.mj_model.geom_rgba[self.panel_ids, 3] = [float(left), float(right)]

    def reset(self):
        import mujoco
        from flygym_demo.complex_terrain import LocomotionAction, apply_locomotion_action

        self.sim.reset()
        self.set_panels()
        initial = LocomotionAction(self.steps.default_pose_by_dof_order(self.dofs), np.ones(6, dtype=bool))
        apply_locomotion_action(self.sim, self.fly.name, initial)
        self.sim.warmup(self.settings["warmup_s"])
        mujoco.mj_forward(self.sim.mj_model, self.sim.mj_data)

    def observe(self, eyes=True):
        import mujoco

        mujoco.mj_forward(self.sim.mj_model, self.sim.mj_data)
        contacts = self.contact_flags()
        angles = self.sim.get_joint_angles(self.fly.name)
        speeds = self.sim.get_joint_velocities(self.fly.name)
        result = {"contacts": contacts.copy(), "all_joint_rad": angles.copy(),
                  "all_joint_velocity_rad_s": speeds.copy(), "joint_rad": angles[self.active_indices].copy(),
                  "joint_velocity_rad_s": speeds[self.active_indices].copy(),
                  "thorax_m": (self.sim.get_body_positions(self.fly.name)[self.thorax] / 1000).copy(),
                  "qpos_native": self.sim.mj_data.qpos.copy()}
        if eyes:
            rgb = self.sim.get_raw_vision(self.fly.name)
            retina = self.sim.retina
            values = np.array([retina.raw_image_to_hex_pxls(frame) for frame in rgb], dtype=np.float32)
            result.update(ommatidia=values, eye_rgb=rgb)
        return result

    def contact_flags(self):
        return self.sim.get_ground_contact_info(self.fly.name)[0] > 0

    def frame(self):
        self.overview.update_scene(self.sim.mj_data, camera=self.camera_id)
        return self.overview.render().copy()


def controlled_probes(rig, output):
    import mujoco

    rig.reset()
    state = rig.sim.mj_data.qpos.copy()
    rig.sim.mj_data.qvel[:] = 0
    cases, frames = {}, {}
    for name, left, right in (("baseline", False, False), ("left", True, False), ("right", False, True), ("repeat", False, False)):
        rig.set_panels(left, right)
        cases[name] = rig.observe()
        frames[name] = rig.frame()
    rig.set_panels()
    free = np.flatnonzero(rig.sim.mj_model.jnt_type == mujoco.mjtJoint.mjJNT_FREE)
    if len(free) != 1:
        raise ValueError("Contact probe requires exactly one free body joint")
    rig.sim.mj_data.qpos[rig.sim.mj_model.jnt_qposadr[free[0]] + 2] += 5
    cases["airborne"] = rig.observe(eyes=False)
    rig.sim.mj_data.qpos[:] = state
    joint = rig.sim.mj_model.joint(rig.fly.jointdof_to_mjcfjoint[rig.dofs[0]].name)
    rig.sim.mj_data.qpos[joint.qposadr[0]] += 0.1
    cases["joint_offset"] = rig.observe(eyes=False)
    rig.sim.mj_data.qpos[:] = state
    rig.sim.mj_data.qvel[joint.dofadr[0]] = 2.0
    cases["joint_velocity"] = rig.observe(eyes=False)

    baseline = cases["baseline"]
    left_delta = np.mean(np.abs(cases["left"]["ommatidia"] - baseline["ommatidia"]), axis=(1, 2))
    right_delta = np.mean(np.abs(cases["right"]["ommatidia"] - baseline["ommatidia"]), axis=(1, 2))
    offset = cases["joint_offset"]["joint_rad"] - baseline["joint_rad"]
    speed = cases["joint_velocity"]["joint_velocity_rad_s"]
    encoder = SensoryEncoder(rig.joint_names, rig.legs, baseline["joint_rad"], EncoderParameters(**rig.settings["encoder"]))
    for observation in cases.values():
        encoder.reset()
        observation["rates_hz"] = encoder.encode(0, observation.get("ommatidia", baseline["ommatidia"]),
                                                observation["contacts"], observation["joint_rad"], observation["joint_velocity_rad_s"])
    left_dark = encoder.channel_names.index("vision.left.dark")
    right_dark = encoder.channel_names.index("vision.right.dark")
    joint_positive = encoder.channel_names.index(f"joint.{rig.joint_names[0]}.angle_positive")
    velocity_positive = encoder.channel_names.index(f"joint.{rig.joint_names[0]}.velocity_positive")
    parameters = encoder.parameters
    checks = {
        "left_stimulus_changes_left_eye": bool(left_delta[0] > 0.005 and left_delta[0] > left_delta[1] * 2),
        "right_stimulus_changes_right_eye": bool(right_delta[1] > 0.005 and right_delta[1] > right_delta[0] * 2),
        "visual_probe_does_not_move_body": all(np.array_equal(cases[k]["qpos_native"], baseline["qpos_native"]) for k in ("left", "right", "repeat")),
        "visual_reset_restores_retina": bool(np.array_equal(cases["repeat"]["ommatidia"], baseline["ommatidia"])),
        "ground_to_air_removes_contacts": bool(baseline["contacts"].any() and not cases["airborne"]["contacts"].any()),
        "translation_keeps_joint_angles": bool(np.array_equal(cases["airborne"]["joint_rad"], baseline["joint_rad"])),
        "known_joint_offset_observed": bool(np.isclose(offset[0], 0.1) and np.allclose(offset[1:], 0)),
        "known_joint_velocity_observed": bool(np.isclose(speed[0], 2.0) and np.allclose(speed[1:], 0)),
        "left_darkening_increases_left_input": bool(cases["left"]["rates_hz"][left_dark] > baseline["rates_hz"][left_dark] and cases["left"]["rates_hz"][right_dark] == baseline["rates_hz"][right_dark]),
        "right_darkening_increases_right_input": bool(cases["right"]["rates_hz"][right_dark] > baseline["rates_hz"][right_dark] and cases["right"]["rates_hz"][left_dark] == baseline["rates_hz"][left_dark]),
        "airborne_contact_inputs_zero": bool(np.all(cases["airborne"]["rates_hz"][8:14] == 0)),
        "offset_encodes_expected_rate": bool(np.isclose(cases["joint_offset"]["rates_hz"][joint_positive], min(0.1 / parameters.angle_scale_rad, 1) * parameters.maximum_rate_hz)),
        "velocity_encodes_expected_rate": bool(np.isclose(cases["joint_velocity"]["rates_hz"][velocity_positive], min(2 / parameters.velocity_scale_rad_s, 1) * parameters.maximum_rate_hz)),
    }
    np.savez_compressed(output / "probes.npz", **{f"{name}_{key}": value for name, data in cases.items() for key, value in data.items()})
    metrics = {"left_stimulus_mean_abs_delta_by_eye": left_delta.tolist(), "right_stimulus_mean_abs_delta_by_eye": right_delta.tolist(),
               "ground_contact_flags": baseline["contacts"].tolist(), "airborne_contact_flags": cases["airborne"]["contacts"].tolist(),
               "joint_offset_rad": offset.tolist(), "joint_velocity_rad_s": speed.tolist()}
    write_json(output / "probes.json", {"checks": checks, "measurements": metrics, "encoding": encoder.manifest(),
                                         "probe_type": "Controlled kinematic snapshots, not biological behavior"})
    return cases, frames, checks


def walking_episode(rig, capture):
    from flygym_demo.complex_terrain import CPGController, make_tripod_cpg_network, apply_locomotion_action

    rig.reset()
    settings = rig.settings
    dt, sample_dt = settings["physics_dt_s"], settings["sensory_dt_s"]
    step_count, stride = round(settings["duration_s"] / dt), round(sample_dt / dt)
    controller = CPGController(make_tripod_cpg_network(timestep=dt, seed=settings["seed"]), rig.steps, rig.dofs)
    reference = rig.sim.get_joint_angles(rig.fly.name)[rig.active_indices].copy()
    encoder = SensoryEncoder(rig.joint_names, rig.legs, reference, EncoderParameters(**settings["encoder"]))
    rows, raw_frames, body_frames = [], [], []
    for step in range(step_count + 1):
        if step % stride == 0:
            t = float(Decimal(step) * Decimal(str(dt)))
            phase = "left" if settings["duration_s"] / 3 <= t < 2 * settings["duration_s"] / 3 else "right" if t >= 2 * settings["duration_s"] / 3 else "baseline"
            rig.set_panels(phase == "left", phase == "right")
            observation = rig.observe()
            rgb = observation.pop("eye_rgb")
            rates = encoder.encode(t, observation["ommatidia"], observation["contacts"], observation["joint_rad"], observation["joint_velocity_rad_s"])
            rows.append({**observation, "time_s": t, "rates_hz": rates, "stimulus": phase})
            if capture:
                raw_frames.append(rgb)
                body_frames.append(rig.frame())
        if step < step_count:
            apply_locomotion_action(rig.sim, rig.fly.name, controller.step())
            rig.sim.step()
    data = {key: np.array([row[key] for row in rows]) for key in rows[0]}
    data.update(channel_names=np.array(encoder.channel_names), joint_names=np.array(rig.joint_names),
                all_joint_names=np.array(rig.all_joint_names), leg_names=np.array(rig.legs),
                reference_joint_rad=reference)
    return data, encoder.manifest(), raw_frames, body_frames


def plot_outputs(rig, output, cases, frames, walk, raw_frames, body_frames, relay):
    import imageio.v2 as imageio
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(3, 3, figsize=(12, 10), layout="constrained")
    for row, name in enumerate(("baseline", "left", "right")):
        axes[row, 0].imshow(frames[name])
        axes[row, 0].set_title(f"{name}: MuJoCo arena")
        for eye in range(2):
            image = rig.sim.retina.hex_pxls_to_human_readable(cases[name]["ommatidia"][eye].sum(axis=1))
            axes[row, eye + 1].imshow(image, cmap="gray", vmin=0, vmax=1)
            axes[row, eye + 1].set_title("Left compound eye" if eye == 0 else "Right compound eye")
        for axis in axes[row]:
            axis.set_axis_off()
    fig.suptitle("FlyLab | Controlled visual probes at one fixed body pose\nRendered retina, not an image recorded from a living fly", fontsize=14)
    fig.savefig(output / "visual-probes.png", dpi=130)
    plt.close(fig)

    fig, axes = plt.subplots(4, 1, figsize=(12, 10), sharex=True, layout="constrained")
    times = walk["time_s"]
    for eye, name in enumerate(("left", "right")):
        axes[0].plot(times, walk["ommatidia"].sum(axis=3).mean(axis=2)[:, eye], label=name)
    axes[0].set_ylabel("Retinal intensity [0..1]")
    axes[0].legend(loc="lower left")
    axes[1].pcolormesh(times, np.arange(7) - 0.5, walk["contacts"][:-1].T,
                       cmap="Greys", vmin=0, vmax=1, shading="flat")
    axes[1].set_ylim(5.5, -0.5)
    axes[1].set_yticks(range(6), walk["leg_names"])
    axes[1].set_ylabel("Ground contact")
    for i in (0, 7, 14):
        axes[2].plot(times, walk["joint_rad"][:, i], label=walk["joint_names"][i])
    axes[2].set_ylabel("Joint angle [rad]")
    axes[2].legend(fontsize=8, loc="upper right")
    axes[3].scatter(relay["spike_times_s"], relay["spike_indices"], s=2, color="#287e78")
    axes[3].set_ylabel("Surrogate relay index")
    axes[3].set_xlabel("Time after warmup [s]")
    for axis in axes:
        axis.axvspan(times[-1] / 3, 2 * times[-1] / 3, alpha=0.08, color="#ba4278")
        axis.axvspan(2 * times[-1] / 3, times[-1], alpha=0.08, color="#318588")
        axis.grid(alpha=0.15)
    fig.suptitle("FlyLab | Native body sensors -> explicit rate encoder -> offline Brian2 relays\nCPG drives walking. Relays have no anatomical FlyWire mapping and do not control the body.", fontsize=13)
    fig.savefig(output / "sensory-traces.png", dpi=130)
    plt.close(fig)

    fig, axes = plt.subplots(1, 3, figsize=(12, 4.5), layout="constrained", gridspec_kw={"width_ratios": [1.5, 1, 1]})
    artists = [axes[0].imshow(body_frames[0]), axes[1].imshow(raw_frames[0][0]), axes[2].imshow(raw_frames[0][1])]
    for axis, title in zip(axes, ("CPG-driven body", "Left eye / camera", "Right eye / camera")):
        axis.set_axis_off()
        axis.set_title(title)
    heading = fig.suptitle("FlyLab | Native sensory recording", fontsize=13)
    with imageio.get_writer(output / "sensory-preview.mp4", fps=10, codec="libx264", quality=8, macro_block_size=2) as video:
        for i, (body, eyes) in enumerate(zip(body_frames[:-1], raw_frames[:-1])):
            for artist, frame in zip(artists, (body, *eyes)):
                artist.set_data(frame)
            heading.set_text(f"FlyLab | t={times[i]:.2f}s | stimulus: {walk['stimulus'][i]}\nRecorded sensors. Brain does not drive this body.")
            fig.canvas.draw()
            frame = np.asarray(fig.canvas.buffer_rgba())[:, :, :3].copy()
            video.append_data(frame)
            if i == len(body_frames) // 2:
                imageio.imwrite(output / "sensory-preview.png", frame)
    plt.close(fig)


def verify_video(output, expected_frames):
    import imageio.v2 as imageio

    with imageio.get_reader(output / "sensory-preview.mp4") as video:
        metadata = video.get_meta_data()
        count = video.count_frames()
        first, last = video.get_data(0), video.get_data(count - 1)
    # Check all three panels in the decoded video, not just the unencoded frames.
    regions = [(0, 500), (500, 850), (850, first.shape[1])]
    std = [float(np.std(first[80:, left:right])) for left, right in regions]
    delta = [float(np.mean(np.abs(first[80:, left:right].astype(float) - last[80:, left:right].astype(float)))) for left, right in regions]
    result = {"frames": count, "fps": metadata["fps"], "size": list(metadata["size"]),
              "panel_pixel_std": std, "panel_first_last_mean_abs_change": delta,
              "passed": count == expected_frames and metadata["fps"] == 10 and all(x > 5 for x in std) and all(x > 0.1 for x in delta)}
    write_json(output / "media-validation.json", result)
    return result


def run(settings, output):
    validate_settings(settings)
    output.mkdir(parents=True, exist_ok=True)
    write_json(output / "report.json", {"status": "running", "stage": 4})
    write_json(output / "settings.json", settings)
    started = time.perf_counter()
    rig = None
    try:
        print("Building vision-enabled body and controlled arena...", flush=True)
        rig = SensorRig(settings)
        cases, frames, checks = controlled_probes(rig, output)
        print(f"Controlled probes: {checks}", flush=True)
        if not all(checks.values()):
            raise RuntimeError("Controlled sensor probe failed; see probes.json")
        print("Recording walking sensors, then resetting and repeating...", flush=True)
        walk, mapping, eye_frames, body_frames = walking_episode(rig, True)
        repeated, _, _, _ = walking_episode(rig, False)
        retinal_delta = float(np.max(np.abs(walk["ommatidia"] - repeated["ommatidia"])))
        rate_delta = float(np.max(np.abs(walk["rates_hz"] - repeated["rates_hz"])))
        checks["walking_reset_reproduces_physics"] = all(np.array_equal(walk[key], repeated[key]) for key in walk if key not in ("ommatidia", "rates_hz"))
        # Renderer values originate from 8-bit RGB. Do not claim bitwise image reproducibility.
        checks["retinal_repeat_within_one_rgb_level"] = retinal_delta <= 1 / 255
        checks["encoded_repeat_within_0_01_hz"] = rate_delta <= 0.01
        checks["walking_joint_angles_change"] = bool(np.max(np.ptp(walk["joint_rad"], axis=0)) > 0.1)
        checks["walking_contact_states_change"] = bool(np.any(np.diff(walk["contacts"].astype(int), axis=0)))
        checks["finite_sensor_values"] = all(np.isfinite(value).all() for value in walk.values() if value.dtype.kind in "fiu")
        checks["bounded_encoded_rates"] = bool(np.all((walk["rates_hz"] >= 0) & (walk["rates_hz"] <= settings["encoder"]["maximum_rate_hz"])))
        np.savez_compressed(output / "sensors.npz", **walk)
        write_json(output / "encoding.json", mapping)
        print("Replaying encoded observations into surrogate Brian2 sensory relays...", flush=True)
        relay = replay_relays(walk["time_s"], walk["rates_hz"], walk["channel_names"].tolist(), settings["seed"], settings["brain_dt_s"])
        repeated_relay = replay_relays(walk["time_s"], walk["rates_hz"], walk["channel_names"].tolist(), settings["seed"], settings["brain_dt_s"])
        disconnected = replay_relays(walk["time_s"], np.zeros_like(walk["rates_hz"]), walk["channel_names"].tolist(), settings["seed"], settings["brain_dt_s"])
        checks["encoded_inputs_produce_relay_spikes"] = len(relay["spike_times_s"]) > 0
        checks["zero_input_control_has_no_spikes"] = len(disconnected["spike_times_s"]) == 0
        checks["relay_reset_reproduces_spikes"] = all(np.array_equal(relay[key], repeated_relay[key]) for key in relay)
        np.savez_compressed(output / "sensory-relays.npz", **relay)
        np.savez_compressed(output / "relays-disconnected.npz", **disconnected)
        plot_outputs(rig, output, cases, frames, walk, eye_frames, body_frames, relay)
        media = verify_video(output, len(walk["time_s"]) - 1)
        checks["encoded_video_panels_nonblank_and_changing"] = media["passed"]
        checks["overview_render_nonblank_and_changes"] = bool(np.std(body_frames[len(body_frames) // 2]) > 5 and
                       np.mean(np.abs(body_frames[0].astype(float) - body_frames[-1].astype(float))) > 0.1)
        from brain_model import ModelParameters
        from flygym import assets_dir
        report = {
            "status": "passed" if all(checks.values()) else "failed", "stage": 4, "checks": checks,
            "brain_controls_body": False, "flywire_sensory_mapping": None, "biologically_validated": False,
            "settings_sha256": digest(settings), "elapsed_s": time.perf_counter() - started,
            "samples_including_endpoint": len(walk["time_s"]), "ommatidia_per_eye": walk["ommatidia"].shape[2],
            "retinal_repeat_max_abs_difference": retinal_delta, "retinal_repeat_tolerance": 1 / 255,
            "rate_repeat_max_abs_difference_hz": rate_delta, "rate_repeat_tolerance_hz": 0.01,
            "video": {"fps": 10, "frames": len(walk["time_s"]) - 1, "playback_speed": settings["sensory_dt_s"] * 10},
            "relay_model": {"status": "independent_surrogate_cells_not_FlyWire",
                            "parameters_si": {k: v for k, v in asdict(ModelParameters()).items() if k in ("rest_v", "reset_v", "threshold_v", "membrane_tau_s", "refractory_s", "input_kick_v")},
                            "input": "zero-delay voltage kicks, same membrane/refractory values as stage 3; no recurrent synapses"},
            "vision_asset_sha256": {name: file_sha256(assets_dir / "model/neuromechfly" / name) for name in ("vision.yaml", "compound_eye.npz")},
            "all_joint_dofs": len(walk["all_joint_names"]), "encoded_joint_dofs": len(walk["joint_names"]),
            "sensory_channels": len(walk["channel_names"]), "relay_spikes": len(relay["spike_times_s"]),
            "active_relays": int(np.count_nonzero(relay["spike_counts"])), "olfaction": "deferred",
            "retina_source": "FlyGym 2.1.0 native fisheye and compound-eye model, default calibration",
            "body_controller": "upstream CPG, not Brian2",
            "units": {"joint_angle": "rad", "angular_velocity": "rad/s", "time": "s", "thorax": "m", "rate": "Hz",
                      "contact": "binary", "retinal_intensity": "normalized renderer proxy", "qpos_native": "MuJoCo mm/rad/quaternion"},
            "versions": {name: importlib.metadata.version(name) for name in ("flygym", "mujoco", "brian2", "numpy", "numba", "imageio")},
            "code_sha256": {name: file_sha256(ROOT / name) for name in ("sensory_experiment.py", "sensory_model.py", "brain_model.py")},
            "artifact_sha256": {name: file_sha256(output / name) for name in ("sensors.npz", "encoding.json", "probes.npz", "sensory-relays.npz", "relays-disconnected.npz", "sensory-preview.mp4", "media-validation.json")},
        }
        write_json(output / "report.json", report)
        print(json.dumps(report, indent=2), flush=True)
        if not all(checks.values()):
            raise RuntimeError("Stage 4 verification failed")
    except Exception as error:
        path = output / "report.json"
        report = json.loads(path.read_text())
        report.update(status="failed", error=str(error))
        write_json(path, report)
        raise
    finally:
        if rig is not None:
            rig.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "experiments/sensory-baseline.json")
    parser.add_argument("--output", type=Path, default=ROOT / "outputs/flylab/sensory-stage4")
    args = parser.parse_args()
    run(json.loads(args.config.read_text(encoding="utf-8")), args.output)
