"""Run and record a NeuroMechFly body baseline with the upstream CPG controller."""

import argparse
import csv
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import platform
import time


ROOT = Path(__file__).resolve().parent


def save_json(path, data):
    path.write_text(json.dumps(data, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def run(duration, seed, output):
    output.mkdir(parents=True, exist_ok=True)
    save_json(output / "report.json", {"status": "running", "stage": 2, "brain_connected": False})
    cache = ROOT / "work/body-cache"
    cache.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("MPLCONFIGDIR", str(cache / "matplotlib"))
    os.environ.setdefault("NUMBA_CACHE_DIR", str(cache / "numba"))

    import imageio.v2 as imageio
    import mujoco
    import numpy as np
    from flygym import Simulation
    from flygym.anatomy import BodySegment
    from flygym.compose import FlatGroundWorld
    from flygym.utils.math import Rotation3D
    from flygym_demo.complex_terrain import (
        CPGController, LocomotionAction, PreprogrammedSteps,
        apply_locomotion_action, make_locomotion_fly, make_tripod_cpg_network,
    )

    output.mkdir(parents=True, exist_ok=True)
    dt = 0.0001
    warmup_s = 0.05
    steps_count = round(duration / dt)
    if not math.isclose(steps_count * dt, duration, abs_tol=1e-10):
        raise ValueError("Duration must be a multiple of 0.0001 seconds")
    settings = {"duration_s": duration, "warmup_s": warmup_s, "timestep_s": dt,
                "seed": seed, "controller": "flygym_demo CPG / tripod", "brain_connected": False,
                "native_length_unit": "mm", "exported_length_unit": "m",
                "video_fps": 25, "video_playback_speed": 0.25}
    save_json(output / "settings.json", settings)
    print("Building the fly and flat arena...", flush=True)
    fly = make_locomotion_fly(name="flylab", colorize=True, add_adhesion=True)
    camera = fly.add_tracking_camera(name="body", pos_offset=(-0.5, -7.5, 0),
                                     rotation=Rotation3D("euler", (1.57, 0, 0)), fovy=30)
    world = FlatGroundWorld()
    world.add_fly(fly, [0, 0, 0.5], Rotation3D("quat", [1, 0, 0, 0]))
    sim = Simulation(world, timestep=dt)
    try:
        renderer = sim.set_renderer([camera], camera_res=(480, 640),
                                    output_fps=25, playback_speed=0.25)
        leg_steps = PreprogrammedSteps()
        dofs = fly.get_actuated_jointdofs_order("position")
        thorax = fly.get_bodysegs_order().index(BodySegment("c_thorax"))

        def episode(render):
            sim.reset()
            initial = LocomotionAction(leg_steps.default_pose_by_dof_order(dofs), np.ones(6, dtype=bool))
            apply_locomotion_action(sim, fly.name, initial)
            sim.warmup(warmup_s)
            network = make_tripod_cpg_network(timestep=dt, seed=seed)
            controller = CPGController(network, leg_steps, dofs)
            state = np.empty((steps_count + 1, sim.mj_model.nq), dtype=np.float64)
            position = np.empty((steps_count + 1, 3), dtype=np.float64)
            contact = np.empty((steps_count + 1, 6), dtype=bool)
            upright = np.empty(steps_count + 1, dtype=float)
            controls = np.empty((steps_count, len(dofs)), dtype=float)
            adhesion = np.empty((steps_count, 6), dtype=bool)

            def observe(index):
                state[index] = sim.mj_data.qpos
                position[index] = sim.get_body_positions(fly.name)[thorax] / 1000
                contact[index] = sim.get_ground_contact_info(fly.name)[0] > 0
                quat = sim.get_body_rotations(fly.name)[thorax]
                upright[index] = 1 - 2 * (quat[1] ** 2 + quat[2] ** 2)

            mujoco.mj_forward(sim.mj_model, sim.mj_data)
            observe(0)
            started = time.perf_counter()
            for index in range(steps_count):
                action = controller.step()
                controls[index] = action.joint_angles
                adhesion[index] = action.adhesion_onoff
                apply_locomotion_action(sim, fly.name, action)
                sim.step()
                # Refresh derived positions and sensors at the recorded state time.
                mujoco.mj_forward(sim.mj_model, sim.mj_data)
                observe(index + 1)
                if render:
                    sim.render_as_needed()
                if not np.isfinite(state[index + 1]).all():
                    raise RuntimeError(f"Non-finite state at step {index + 1}")
            return state, position, contact, upright, controls, adhesion, time.perf_counter() - started

        print(f"Recording {duration:g} simulation seconds ({steps_count} steps)...", flush=True)
        state, position, contact, upright, controls, adhesion, elapsed = episode(True)
        frames = next(iter(renderer.frames.values()))
        if len(frames) < 2:
            raise RuntimeError("Renderer produced fewer than two frames")
        imageio.imwrite(output / "fly.png", frames[len(frames) // 2])
        with imageio.get_writer(output / "walking.mp4", fps=25, codec="libx264", quality=8) as video:
            for frame in frames:
                video.append_data(frame)
        pixel_std = float(np.std(frames[len(frames) // 2].astype(float)))
        pixel_motion = float(np.mean(np.abs(frames[-1].astype(float) - frames[0].astype(float))))
        frame_count = len(frames)
        np.savez_compressed(output / "state.npz", qpos=state, thorax_m=position,
                            contacts=contact, up_axis_z=upright, joint_targets_rad=controls,
                            adhesion=adhesion, time_s=np.arange(steps_count + 1) * dt)
        with (output / "trajectory.csv").open("w", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(["time_s", "thorax_x_m", "thorax_y_m", "thorax_z_m", "legs_in_contact", "up_axis_z"])
            for index in range(steps_count + 1):
                writer.writerow([index * dt, *position[index], int(contact[index].sum()), upright[index]])

        print("Resetting and repeating the same seeded experiment...", flush=True)
        repeated_state, repeated_pos, repeated_contact, _, _, _, repeat_elapsed = episode(False)
        reset_delta = float(np.max(np.abs(repeated_state - state)))
        displacement = float(np.linalg.norm(position[-1, :2] - position[0, :2]))
        checks = {
            "finite_states": bool(np.isfinite(state).all()),
            "ground_contact": bool(contact.any()),
            "body_moved": displacement > 0.00005,
            "upright": bool(np.min(upright) > 0),
            "reset_reproduces_states": bool(np.allclose(state, repeated_state, atol=1e-9, rtol=0)),
            "reset_reproduces_contacts": bool(np.array_equal(contact, repeated_contact)),
            "render_nonblank": pixel_std > 5,
            "render_changes": pixel_motion > 0.1,
        }
        report = {
            "status": "passed" if all(checks.values()) else "failed",
            "stage": 2, "settings": settings, "checks": checks,
            "body_dofs": len(dofs), "physics_steps_per_episode": steps_count,
            "planar_displacement_m": displacement,
            "ground_contact_fraction": float(contact.any(axis=1).mean()),
            "minimum_up_axis_z": float(upright.min()),
            "reset_max_qpos_difference": reset_delta,
            "reset_max_position_difference_m": float(np.max(np.abs(position - repeated_pos))),
            "rendered_frames": frame_count, "image_pixel_std": pixel_std,
            "first_last_frame_mean_difference": pixel_motion,
            "recorded_episode_wall_s": elapsed, "repeat_without_render_wall_s": repeat_elapsed,
            "python": platform.python_version(),
            "package_versions": {name: importlib.metadata.version(name) for name in ("flygym", "mujoco", "numpy", "scipy", "numba", "imageio", "imageio-ffmpeg")},
            "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "source": "https://neuromechfly.org/tutorials/4a_cpg_controller/",
        }
        save_json(output / "report.json", report)
        print(json.dumps(report, indent=2), flush=True)
        if not all(checks.values()):
            raise RuntimeError("Body checks failed; see report.json")
    finally:
        sim.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--duration", type=float, default=1.0)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output", type=Path, default=ROOT / "outputs/flylab/body-baseline")
    args = parser.parse_args()
    if not math.isfinite(args.duration) or not 0.05 <= args.duration <= 10:
        parser.error("Duration must be between 0.05 and 10 seconds")
    if not 0 <= args.seed < 2**32:
        parser.error("Seed must be an unsigned 32-bit integer")
    output = args.output.resolve()
    try:
        run(args.duration, args.seed, output)
    except Exception as error:
        report_path = output / "report.json"
        report = json.loads(report_path.read_text()) if report_path.exists() else {}
        report.update(status="failed", error=str(error))
        output.mkdir(parents=True, exist_ok=True)
        save_json(report_path, report)
        raise


if __name__ == "__main__":
    main()
