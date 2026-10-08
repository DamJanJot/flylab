"""Numerical/causal tests; synthetic graphs here are not anatomical evidence."""

from dataclasses import replace
import copy
import gc
import json
import unittest

import numpy as np

from brain_model import NeuralRuntime, b2, simulate
from closed_loop_model import CouplingParameters, SteeringAdapter, without_edges
from closed_loop_experiment import validate_configuration, panel_state, neural_arrays
from flylab import ROOT
from test_brain import circuit, config


class RuntimeTests(unittest.TestCase):
    def test_snapshots_own_monitor_data_after_network_destruction(self):
        graph = circuit()
        runtime = NeuralRuntime(graph, 0.0001, 42, [str(graph.ids[0])])
        runtime.advance(0.05, [500])
        data = neural_arrays(runtime, graph)
        expected = {key: value.copy() for key, value in data.items()}
        for value in data.values():
            self.assertTrue(value.flags.owndata)
        del runtime
        gc.collect()
        replacement = NeuralRuntime(graph, 0.0001, 42, [str(graph.ids[0])])
        replacement.advance(0.1, [20])
        for key in data:
            np.testing.assert_array_equal(data[key], expected[key])

    def test_segmented_matches_uninterrupted_network_with_delays(self):
        graph = circuit()
        setup = config(graph, 0.05)
        setup["interventions"] = [{"kind": "stimulate", "neuron_ids": [str(graph.ids[0])],
                                   "rate_hz": 500, "start_s": 0, "end_s": 0.05}]
        batch, _ = simulate(graph, setup, record_ids=graph.ids.tolist())
        runtime = NeuralRuntime(graph, 0.0001, setup["seed"], [str(graph.ids[0])], record_ids=graph.ids.tolist())
        counts = np.zeros(2, dtype=int)
        for _ in range(50):
            counts += runtime.advance(0.001, [500])
        np.testing.assert_array_equal(runtime.spikes.i[:], batch["spike_indices"])
        np.testing.assert_array_equal(runtime.spikes.t[:] / b2.second, batch["spike_times_s"])
        np.testing.assert_array_equal(runtime.traces.v[:] / b2.volt, batch["voltage_v"])
        np.testing.assert_array_equal(runtime.traces.g[:] / b2.volt, batch["synaptic_drive_v"])
        np.testing.assert_array_equal(counts, batch["spike_counts"])
        self.assertEqual(runtime.steps, 500)

    def test_silencing_output_and_removing_edges_do_not_stop_input(self):
        graph = circuit()
        for mode in ("silence", "no_edges"):
            runtime = NeuralRuntime(without_edges(graph) if mode == "no_edges" else graph,
                                    0.0001, 42, [str(graph.ids[0])])
            counts = runtime.advance(0.05, [700], [str(graph.ids[1])] if mode == "silence" else [])
            self.assertGreater(counts[0], 0)
            self.assertEqual(counts[1], 0)

    def test_invalid_step_does_not_advance_state(self):
        runtime = NeuralRuntime(circuit(), 0.0001, 42, ["720575940600000001"])
        for dt, rates, silent in ((0, [0], []), (0.00015, [0], []), (0.01, [float("nan")], []),
                                  (0.01, [1001], []), (0.01, [-1], []), (0.01, [], []), (0.01, [0], ["999"])):
            with self.subTest(dt=dt, rates=rates, silent=silent), self.assertRaises(ValueError):
                runtime.advance(dt, rates, silent)
        self.assertEqual(runtime.steps, 0)
        self.assertEqual(float(runtime.network.t / b2.second), 0)
        runtime.advance(0.001, [0])

    def test_inflight_event_is_blocked_during_output_silencing(self):
        graph = circuit()
        runtime = NeuralRuntime(graph, 0.0001, 42, initial_v=[-0.04, -0.052], record_ids=graph.ids.tolist())
        runtime.advance(0.001, [])
        runtime.advance(0.005, [], [str(graph.ids[1])])
        self.assertEqual(int(runtime.spikes.count[1]), 0)
        np.testing.assert_array_equal(runtime.traces.g[1] / b2.volt, 0)
        runtime.advance(0.004, [])
        self.assertEqual(int(runtime.spikes.count[1]), 0)


class AdapterTests(unittest.TestCase):
    def test_eye_difference_mapping_and_common_mode_rejection(self):
        adapter = SteeringAdapter()
        names = ["vision.left.dark", "vision.right.dark"]
        baseline = [80, 80]
        rates, contrast = adapter.sensory_drive([110, 80], baseline, names, 200)
        np.testing.assert_array_equal(rates, [200, 20])
        self.assertEqual(contrast, 1)
        rates, contrast = adapter.sensory_drive([80, 110], baseline, names, 200)
        np.testing.assert_array_equal(rates, [20, 200])
        self.assertEqual(contrast, -1)
        rates, _ = adapter.sensory_drive([110, 110], baseline, names, 200)
        np.testing.assert_array_equal(rates, [20, 20])

    def test_filter_units_bounds_and_disconnection(self):
        adapter = SteeringAdapter()
        command = adapter.motor_command([1, 0], 0.01)
        expected_hz = (1 - np.exp(-0.01 / 0.04)) * 100
        self.assertAlmostEqual(adapter.filtered_hz[0], expected_hz)
        self.assertAlmostEqual(command[0], 1 - 0.65 * expected_hz / 100)
        self.assertEqual(command[1], 1)
        for _ in range(100):
            command = adapter.motor_command([100, 0], 0.01)
        np.testing.assert_allclose(command, [0.35, 1])
        np.testing.assert_array_equal(adapter.motor_command([0, 10], 0.01, connected=False), [1, 1])

    def test_reset_and_no_spikes_give_reference_gait(self):
        for _ in range(2):
            adapter = SteeringAdapter()
            np.testing.assert_array_equal(adapter.motor_command([0, 0], 0.01), [1, 1])
            np.testing.assert_array_equal(adapter.motor_command([1, 1], 0.01), [1, 1])

    def test_reject_invalid_adapter_parameters_and_observations(self):
        for key in CouplingParameters.__dataclass_fields__:
            with self.assertRaises(ValueError):
                SteeringAdapter(replace(CouplingParameters(), **{key: float("nan")}))
        with self.assertRaises(ValueError):
            SteeringAdapter(replace(CouplingParameters(), maximum_stride_reduction=1))
        adapter = SteeringAdapter()
        for counts in ([1], [1, -1], [0.5, 0], [float("inf"), 0]):
            with self.assertRaises(ValueError):
                adapter.motor_command(counts, 0.01)
        with self.assertRaises(ValueError):
            adapter.motor_command([0, 1], 0)
        with self.assertRaises(ValueError):
            adapter.sensory_drive([201, 0], [0, 0], ["vision.left.dark", "vision.right.dark"], 200)


class ConfigurationTests(unittest.TestCase):
    def test_valid_defaults_and_invalid_fields_clocks_rates(self):
        config = json.loads((ROOT / "experiments/closed-loop.json").read_text())
        settings, _ = validate_configuration(config)
        self.assertEqual(settings["duration_s"], 0.6)
        for group, key, value in (("sensors", "sensory_dt_s", 0.01005),
                                  ("sensors", "seed", True), ("coupling", "visual_gain_hz", 2000),
                                  ("coupling", "unknown", 1)):
            candidate = copy.deepcopy(config)
            candidate[group][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_configuration(candidate)

    def test_panel_schedule_is_integer_aligned(self):
        self.assertEqual(panel_state(0, 60), (False, False))
        self.assertEqual(panel_state(9, 60), (False, False))
        self.assertEqual(panel_state(10, 60), (True, False))
        self.assertEqual(panel_state(34, 60), (True, False))
        self.assertEqual(panel_state(35, 60), (False, True))
        self.assertEqual(panel_state(59, 60), (False, True))


if __name__ == "__main__":
    unittest.main()
