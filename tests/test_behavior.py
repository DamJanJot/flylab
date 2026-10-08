"""Protocol, scoring, pairing and checkpoint tests; no biological claims."""

import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from flylab import ROOT, write_json
from flywire_data import file_sha256
from behavior_model import Trial, TrialProtocol, trial_plan, validate_suite, behavior_metrics, summarize_trials
from behavior_experiment import load_verified_result, write_checkpoint, should_capture, run_suite, trial_directory
from closed_loop_model import OUTPUT_IDS
from test_brain import circuit


def configuration():
    return json.loads((ROOT / "experiments/behavior-suite.json").read_text())


class ProtocolTests(unittest.TestCase):
    def test_plan_complete_unique_and_repeats_not_extra_seeds(self):
        plan = trial_plan(configuration())
        self.assertEqual(len(plan), 44)
        self.assertEqual(len(set(t.key for t in plan)), 44)
        self.assertEqual(sum(t.repeat for t in plan), 2)
        self.assertEqual(sum(t.condition.startswith("stimulate") for t in plan), 6)
        self.assertEqual(sum(should_capture(t, 42) for t in plan), 4)

    def test_reject_bad_seeds_clocks_and_missing_controls(self):
        for key, value in (("seeds", [42, 42]), ("seeds", [True, 43]), ("seeds", [42]),
                           ("cue_start_s", 0.015), ("intervention_start_s", 0.605),
                           ("intervention_end_s", 0.8), ("stimulation_hz", 1001),
                           ("scenarios", ["neutral"]), ("conditions", ["connected"])):
            c = configuration()
            c[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                validate_suite(c)

    def test_obstacle_bounds_units_and_goal_validation(self):
        for key, value in (("center_mm", [0, 0, 0.15]), ("half_size_mm", [0.4, 2, -0.15]),
                           ("center_mm", [5, 0, 1]), ("center_mm", [5, float("nan"), 0.15])):
            c = configuration()
            c["obstacle"][key] = value
            with self.assertRaises(ValueError):
                validate_suite(c)
        c = configuration()
        c["outcome"]["gate_x_mm"] = 5
        with self.assertRaises(ValueError):
            validate_suite(c)

    def test_cue_sides_and_onset_without_altering_input(self):
        c = configuration()
        for scenario, expected in (("left", (True, False)), ("right", (False, True)), ("neutral", (False, False))):
            p = TrialProtocol(c, Trial(scenario, "connected", 42))
            self.assertEqual(p.panels(9), (False, False))
            self.assertEqual(p.panels(10), expected)
            rates = np.array([90., 20.])
            result, silence = p.neural_input(20, rates)
            np.testing.assert_array_equal(result, [90, 20, 0, 0])
            np.testing.assert_array_equal(rates, [90, 20])
            self.assertEqual(silence, ())

    def test_stimulation_half_open_window_and_exact_target(self):
        for condition, target in (("stimulate_left", 2), ("stimulate_right", 3)):
            p = TrialProtocol(configuration(), Trial("neutral", condition, 42))
            for index, expected in ((19, 0), (20, 150), (59, 150), (60, 0)):
                rates, silent = p.neural_input(index, [30, 40])
                self.assertEqual(rates[target], expected)
                self.assertEqual(rates[5 - target], 0)
                self.assertEqual(silent, ())
        p = TrialProtocol(configuration(), Trial("left", "silenced", 42))
        self.assertEqual(p.neural_input(0, [20, 20])[1], OUTPUT_IDS)
        with self.assertRaises(ValueError):
            TrialProtocol(configuration(), Trial("other", "connected", 42))


class MetricTests(unittest.TestCase):
    def fixture(self):
        state = {"heading_rad": np.deg2rad([0, 10, 20]), "applied_command": np.array([[1, 1], [0.5, 1]]),
                 "dn_counts": np.array([[1, 2], [3, 4]])}
        physics = {"time_s": np.array([0.01, 0.02, 0.03]), "thorax_m": np.array([[0.001, 0, 0], [0.007, 0, 0], [0.008, 0, 0]]),
                   "up_axis_z": np.array([1, 0.8, 0.9]), "obstacle_contact": [False, True, False],
                   "body_obstacle_contact": [False, False, False], "leg_contacts": np.ones((3, 6), dtype=bool)}
        return state, physics

    def test_gate_path_units_contacts_and_heading(self):
        state, physics = self.fixture()
        r = behavior_metrics(state, physics, configuration(), "obstacle")
        self.assertEqual(r["heading_change_deg"], 20)
        self.assertAlmostEqual(r["forward_progress_mm"], 7)
        self.assertAlmostEqual(r["path_length_mm"], 7)
        self.assertEqual(r["gate_time_s"], 0.02)
        self.assertTrue(r["upright_gate_success"])
        self.assertEqual(r["obstacle_contact_s"], 0.0001)
        self.assertEqual(r["obstacle_first_contact_s"], 0.02)

    def test_fall_and_off_corridor_are_retained_failures(self):
        state, physics = self.fixture()
        physics["up_axis_z"][2] = 0.1
        r = behavior_metrics(state, physics, configuration(), "obstacle")
        self.assertTrue(r["fell"])
        self.assertTrue(r["thorax_reached_gate"])
        self.assertFalse(r["upright_gate_success"])
        physics["thorax_m"][:, 1] = 0.004
        r = behavior_metrics(state, physics, configuration(), "obstacle")
        self.assertIsNone(r["gate_time_s"])
        self.assertFalse(r["thorax_reached_gate"])

    def test_paired_seed_statistics_exclude_only_declared_repeats(self):
        c = configuration()
        reports = {}
        for trial in trial_plan(c):
            h = trial.seed - 42 + (10 if trial.scenario == "left" else -10 if trial.scenario == "right" else 0)
            if trial.condition == "connected":
                h += 2
            reports[trial.key] = {"behavior": {"heading_change_deg": h, "upright_gate_success": False, "fell": True,
                                               "obstacle_contact_s": 0, "forward_progress_mm": h, "dn_spikes_left_right": [1, 2]}}
        result = summarize_trials(reports, c)
        self.assertTrue(all(g["n"] == 3 and g["falls"] == 3 for g in result["groups"]))
        self.assertEqual(len(result["paired"]), 12)
        self.assertTrue(all(row["connected_minus_disconnected_heading_deg"] == 2 for row in result["paired"]))
        self.assertTrue(all(row["directional_response"] for row in result["paired"] if row["scenario"] in ("left", "right")))
        reports.pop(next(iter(reports)))
        with self.assertRaises(ValueError):
            summarize_trials(reports, c)


class CheckpointTests(unittest.TestCase):
    def test_partial_resume_skips_completed_trial_and_rejects_changed_config(self):
        c = configuration()
        graph = circuit()
        visited = []

        def fake_trial(config, graph, trial, output):
            visited.append(trial.key)
            directory = trial_directory(output, trial)
            directory.mkdir(parents=True)
            report = {"trial": {"scenario": trial.scenario, "condition": trial.condition, "seed": trial.seed, "repeat": trial.repeat},
                      "technical_status": "passed", "checks": {"synthetic_test": True}, "files": {}}
            write_json(directory / "result.json", report)
            return report

        with tempfile.TemporaryDirectory() as name, patch("behavior_experiment.load_steering_circuit", return_value=(graph, {"mapping_sha256": "fixture"})), patch("behavior_experiment.run_trial", side_effect=fake_trial):
            output = Path(name) / "suite"
            run_suite(c, None, None, output, max_trials=1)
            first_hash = file_sha256(trial_directory(output, trial_plan(c)[0]) / "result.json")
            run_suite(c, None, None, output, resume=True, max_trials=1)
            self.assertEqual(visited, [t.key for t in trial_plan(c)[:2]])
            self.assertEqual(first_hash, file_sha256(trial_directory(output, trial_plan(c)[0]) / "result.json"))
            with self.assertRaisesRegex(ValueError, "Existing suite"):
                run_suite(c, None, None, output)
            changed = copy.deepcopy(c)
            changed["stimulation_hz"] = 151
            with self.assertRaisesRegex(ValueError, "Resume refused"):
                run_suite(changed, None, None, output, resume=True)
            write_json(output / "settings.json", {"tampered": True})
            with self.assertRaisesRegex(ValueError, "source artifact"):
                run_suite(c, None, None, output, resume=True)

    def test_interrupted_atomic_write_keeps_previous_checkpoint(self):
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / "manifest.json"
            write_checkpoint(path, {"completed": 1})
            with patch("behavior_experiment.write_json", side_effect=OSError("disk interrupted")), self.assertRaises(OSError):
                write_checkpoint(path, {"completed": 2})
            self.assertEqual(json.loads(path.read_text())["completed"], 1)

    def test_checksum_tamper_rejected_and_data_not_deleted(self):
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            data = directory / "data.json"
            write_json(data, {"value": 1})
            report = {"technical_status": "passed", "checks": {"finite": True}, "files": {"data.json": file_sha256(data)}}
            write_json(directory / "result.json", report)
            checksum = file_sha256(directory / "result.json")
            self.assertEqual(load_verified_result(directory, checksum), report)
            write_json(data, {"value": 2})
            with self.assertRaisesRegex(ValueError, "artifact checksum"):
                load_verified_result(directory, checksum)
            self.assertTrue(data.exists())
            with self.assertRaisesRegex(ValueError, "result checksum"):
                load_verified_result(directory, "0" * 64)

    def test_checkpoint_refuses_artifacts_outside_trial(self):
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            write_json(directory / "result.json", {"technical_status": "passed", "checks": {"ok": True},
                                                   "files": {"../outside.json": "untrusted"}})
            with self.assertRaisesRegex(ValueError, "artifact checksum"):
                load_verified_result(directory, file_sha256(directory / "result.json"))


if __name__ == "__main__":
    unittest.main()
