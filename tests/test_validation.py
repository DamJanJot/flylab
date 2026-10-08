"""Stage-8 data partition, scoring, ablation and resumability regression tests."""

from dataclasses import asdict
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from flylab import ROOT, write_json
from flywire_data import Connectome, file_sha256
from closed_loop_model import INPUT_IDS, OUTPUT_IDS
from validation_model import Assay, training_plan, evaluation_plan, validate_config, objective, select_candidate, without_feedback
from validation_experiment import measured_process, verified_files, run_suite, compare_results


def configuration():
    return json.loads((ROOT / "experiments/validation-suite.json").read_text())


def fixture_graph():
    ids = np.array(sorted(INPUT_IDS + OUTPUT_IDS))
    lookup = {root: i for i, root in enumerate(ids)}
    edges = sorted((lookup[a], lookup[b], count) for a, b, count in
                   ((INPUT_IDS[0], OUTPUT_IDS[0], 309), (INPUT_IDS[1], OUTPUT_IDS[1], 301),
                    (OUTPUT_IDS[0], INPUT_IDS[0], 16), (OUTPUT_IDS[1], INPUT_IDS[1], 19)))
    pre, post, counts = np.array(edges).T
    return Connectome(ids, np.array(["ACH"] * 4), pre, post, counts, {}).validate()


def behavior_rows(left=30, right=-30, neutral=0, fell=False):
    return {scenario: {"heading_change_deg": value, "fell": fell}
            for scenario, value in (("left", left), ("right", right), ("neutral", neutral))}


class CalibrationTests(unittest.TestCase):
    def test_partitions_and_plan(self):
        c = configuration()
        train = training_plan(c)
        evaluate = evaluation_plan(c, .35, .65)
        self.assertEqual(len(train), 18)
        self.assertEqual(len(evaluate), 50)
        self.assertEqual(len({a.key for a in train + evaluate}), 68)
        self.assertFalse({a.seed for a in train} & {a.seed for a in evaluate})
        self.assertEqual(sum(a.repeat for a in evaluate), 2)
        self.assertEqual(sum(a.condition == "no_feedback" for a in evaluate), 6)

    def test_invalid_partitions_and_candidates(self):
        for key, value in (("training_seeds", [201, 102]), ("validation_seeds", [201, 201]),
                           ("training_seeds", [True, 102]), ("validation_seeds", [201]),
                           ("stride_reductions", [.5, .5]), ("stride_reductions", [0, .5]),
                           ("stride_reductions", [.3, float("nan")]), ("target_turn_deg", float("inf")),
                           ("full_brain_duration_s", 10), ("schema_version", True)):
            c = configuration()
            c[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                validate_config(c)

    def test_objective_pairs_neutral_and_penalizes_falls(self):
        c = configuration()
        self.assertEqual(objective(behavior_rows(), c), 0)
        self.assertEqual(objective(behavior_rows(40, -20, 10), c), 2.5)
        self.assertEqual(objective(behavior_rows(fell=True), c), 180)
        with self.assertRaises(ValueError):
            objective({"left": {"heading_change_deg": 30}}, c)
        with self.assertRaises(ValueError):
            objective(behavior_rows(left=float("nan")), c)

    def test_training_only_selection_and_tie_order(self):
        c = configuration()
        reports = {a.key: {"behavior": behavior_rows()[a.scenario]} for a in training_plan(c)}
        self.assertEqual(select_candidate(reports, c)["gain"], .35)
        contaminated = {**reports, "validation-extra": {"behavior": {}}}
        with self.assertRaises(ValueError):
            select_candidate(contaminated, c)
        reports.pop(next(iter(reports)))
        with self.assertRaises(ValueError):
            select_candidate(reports, c)

    def test_best_training_candidate_not_default(self):
        c = configuration()
        reports = {}
        for a in training_plan(c):
            rows = behavior_rows() if a.profile == "candidate_1" else behavior_rows(60, -60)
            reports[a.key] = {"behavior": rows[a.scenario]}
        self.assertEqual(select_candidate(reports, c)["gain"], .5)

    def test_feedback_ablation_preserves_forward_synapses_and_source(self):
        original = fixture_graph()
        before = original.counts.copy()
        ablated = without_feedback(original)
        np.testing.assert_array_equal(ablated.ids, original.ids)
        self.assertEqual(int(ablated.counts.sum()), 610)
        self.assertEqual(len(ablated.pre), 2)
        self.assertTrue(np.all(np.isin(ablated.ids[ablated.pre], INPUT_IDS)))
        np.testing.assert_array_equal(original.counts, before)
        self.assertEqual(int(original.counts.sum()), 645)
        with self.assertRaises(ValueError):
            without_feedback(ablated)

    def test_comparison_requires_every_assay_including_repeats(self):
        behavior = json.loads((ROOT / "experiments/behavior-suite.json").read_text())
        with self.assertRaisesRegex(ValueError, "incomplete"):
            compare_results(configuration(), {"gain": .35}, behavior, {}, Path("unused"))

    def test_control_comparison_detects_divergent_physics(self):
        c = configuration()
        behavior = json.loads((ROOT / "experiments/behavior-suite.json").read_text())
        reports = {}
        for a in training_plan(c) + evaluation_plan(c, .35, .65):
            row = {**behavior_rows()[a.scenario], "forward_progress_mm": 5, "dn_spikes_left_right": [2, 3]}
            reports[a.key] = {"assay": asdict(a), "behavior": row, "technical_status": "passed", "checks": {"fixture": True},
                              "performance": {"sampled_peak_rss_bytes": 100, "worker_wall_s": 1}}
        def recording(output, result):
            arrays = {key: np.zeros(3) for key in ("qpos_native", "input_rates_hz", "spike_times_s", "spike_indices", "voltage_v", "applied_command", "ommatidia")}
            if result["assay"]["condition"] == "no_edges":
                arrays["qpos_native"] += 1
            return arrays
        with patch("validation_experiment.load_recording", side_effect=recording):
            checks, outcomes = compare_results(c, {"gain": .35}, behavior, reports, Path("unused"))
        self.assertEqual(sum(not value for value in checks.values()), 6)
        self.assertEqual(len(outcomes["held_out"]), 3)
        self.assertEqual(len(outcomes["ablations"]), 30)


class CheckpointTests(unittest.TestCase):
    def setUp(self):
        self.c = configuration()
        self.behavior = json.loads((ROOT / "experiments/behavior-suite.json").read_text())

    def fake_trial(self, assay, behavior, config, output):
        directory = output / "trials" / assay.key
        report = {"assay": asdict(assay), "technical_status": "passed", "checks": {"fixture": True}, "files": {},
                  "behavior": behavior_rows()[assay.scenario]}
        write_json(directory / "result.json", report)
        return report

    def run_mocked(self, output, **kwargs):
        with patch("validation_experiment.load_steering_circuit", return_value=(fixture_graph(), {"mapping_sha256": "fixture"})), \
                patch("validation_experiment.execute_assay", side_effect=self.fake_trial) as runner:
            run_suite(self.c, self.behavior, Path("cache"), Path("data"), output, **kwargs)
            return runner.call_count

    def test_resume_reuses_trials_and_freezes_before_evaluation(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "suite"
            self.assertEqual(self.run_mocked(output, max_trials=1), 1)
            first = json.loads((output / "manifest.json").read_text())["completed"]
            self.assertFalse((output / "selection.json").exists())
            self.assertEqual(self.run_mocked(output, resume=True, max_trials=17), 17)
            selected = json.loads((output / "selection.json").read_text())
            self.assertEqual(len(selected["training_result_sha256"]), 18)
            self.assertEqual(self.run_mocked(output, resume=True, max_trials=1), 1)
            manifest = json.loads((output / "manifest.json").read_text())
            self.assertEqual(len(manifest["completed"]), 19)
            for key, checksum in first.items():
                self.assertEqual(manifest["completed"][key], checksum)

    def test_resume_rejects_configuration_and_artifact_changes(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "suite"
            self.run_mocked(output, max_trials=1)
            self.c["target_turn_deg"] = 31
            with self.assertRaisesRegex(ValueError, "Resume refused"):
                self.run_mocked(output, resume=True, max_trials=1)
            self.c["target_turn_deg"] = 30.0
            write_json(output / "settings.json", {})
            with self.assertRaisesRegex(ValueError, "Artifact mismatch"):
                self.run_mocked(output, resume=True, max_trials=1)

    def test_resume_rejects_unknown_paths_before_reading_results(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "suite"
            self.run_mocked(output, max_trials=1)
            path = output / "manifest.json"
            manifest = json.loads(path.read_text())
            manifest["completed"]["../../outside"] = "invalid"
            write_json(path, manifest)
            with self.assertRaisesRegex(ValueError, "Unknown checkpoint"):
                self.run_mocked(output, resume=True, max_trials=1)

    def test_frozen_selection_checksum_checked(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "suite"
            self.run_mocked(output, max_trials=18)
            write_json(output / "selection.json", {"gain": .65})
            with self.assertRaisesRegex(ValueError, "Artifact mismatch"):
                self.run_mocked(output, resume=True, max_trials=1)

    def test_verify_files_rejects_traversal_and_damage(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            write_json(root / "outside.json", {})
            (root / "inside").mkdir()
            with self.assertRaises(ValueError):
                verified_files(root / "inside", {"../outside.json": file_sha256(root / "outside.json")})
            with self.assertRaises(ValueError):
                verified_files(root, {"outside.json": "bad-checksum"})

    def test_fresh_worker_resource_measurement_and_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            log = Path(temp) / "worker.log"
            result = measured_process([sys.executable, "-c", "import time; time.sleep(0.2)"], log)
            self.assertGreater(result["sampled_peak_rss_bytes"], 0)
            self.assertGreater(result["worker_wall_s"], .2)
            self.assertGreater(result["memory_samples"], 0)
            allocated = measured_process([sys.executable, "-c", "import time; a=bytearray(64*1024*1024); time.sleep(0.3)"], log)
            self.assertGreater(allocated["sampled_peak_rss_bytes"] - result["sampled_peak_rss_bytes"], 48 * 1024 * 1024)
            with self.assertRaisesRegex(RuntimeError, "Worker failed"):
                measured_process([sys.executable, "-c", "raise SystemExit(3)"], log)


if __name__ == "__main__":
    unittest.main()
