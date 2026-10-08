"""Numerical tests use synthetic circuits, never asserted to be anatomy."""

from dataclasses import replace
import gzip
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

from brain_model import ModelParameters, polarity, simulate
from flylab import ROOT
from flywire_data import Connectome, from_packet, import_tables, load_cache


def circuit(nt=("ACH", "ACH"), counts=(400,)):
    return Connectome(np.array([str(720575940600000001 + i) for i in range(len(nt))]),
                      np.array(nt), np.zeros(len(counts), dtype=np.int32),
                      np.ones(len(counts), dtype=np.int32), np.array(counts, dtype=np.int64),
                      {"scope": "synthetic_unit_test", "complete_brain": False})


def config(graph, duration=0.05):
    base = json.loads((ROOT / "experiments/baseline.json").read_text())
    return {**base, "duration_s": duration, "graph_center_id": str(graph.ids[0])}


class NeuralTests(unittest.TestCase):
    def test_passive_leak_matches_analytic_solution(self):
        graph = circuit(nt=("ACH",), counts=())
        data, report = simulate(graph, config(graph), record_ids=graph.ids.tolist(), initial_v=[-0.05])
        expected = -0.052 + 0.002 * np.exp(-0.05 / 0.02)
        self.assertAlmostEqual(data["final_voltage_v"][0], expected, places=12)
        self.assertEqual(report["spikes"], 0)
        self.assertTrue(report["finite_state"])

    def test_synapse_sign_and_delay(self):
        params = ModelParameters()
        for nt, sign in (("ACH", 1), ("GABA", -1), ("GLUT", -1), ("DA", 0)):
            with self.subTest(nt=nt):
                graph = circuit(nt=(nt, "ACH"))
                data, _ = simulate(graph, config(graph), record_ids=graph.ids.tolist(), initial_v=[-0.04, -0.052])
                drive = data["synaptic_drive_v"][1]
                before = data["trace_time_s"] < params.delay_s - 1e-10
                np.testing.assert_array_equal(drive[before], 0)
                if sign:
                    first = np.flatnonzero(drive)[0]
                    self.assertAlmostEqual(data["trace_time_s"][first], params.delay_s, places=10)
                    self.assertEqual(np.sign(drive[first]), sign)
                    self.assertAlmostEqual(drive[first], sign * 400 * params.weight_per_contact_v)
                if sign == 1:
                    self.assertGreater(data["spike_counts"][1], 0)
                elif sign == -1:
                    self.assertEqual(data["spike_counts"][1], 0)
                    self.assertLess(data["voltage_v"][1].min(), params.rest_v)
                else:
                    np.testing.assert_array_equal(drive, 0)
        np.testing.assert_array_equal(polarity(["ACH", "GABA", "GLUT", "SER", "UNKNOWN"]), [1, -1, -1, 0, 0])

    def test_reproducible_input_window_refractory_and_silencing(self):
        graph = circuit()
        setup = config(graph, 0.1)
        stimulus = {"kind": "stimulate", "neuron_ids": [str(graph.ids[0])], "rate_hz": 500,
                    "start_s": 0.01, "end_s": 0.08}
        setup["interventions"] = [stimulus]
        data, _ = simulate(graph, setup, record_ids=graph.ids.tolist())
        repeat, _ = simulate(graph, setup, record_ids=graph.ids.tolist())
        for key in data:
            np.testing.assert_array_equal(data[key], repeat[key])
        self.assertTrue(np.all(data["input_times_s"] >= 0.01))
        self.assertTrue(np.all(data["input_times_s"] < 0.08))
        self.assertGreater(data["spike_counts"].min(), 0)
        for i in range(2):
            spikes = data["spike_times_s"][data["spike_indices"] == i]
            self.assertTrue(np.all(np.diff(spikes) >= 0.0022 - 1e-10))
        setup["interventions"].append({"kind": "silence", "neuron_ids": [str(graph.ids[0])], "start_s": 0, "end_s": 0.1})
        quiet, report = simulate(graph, setup, record_ids=graph.ids.tolist())
        np.testing.assert_array_equal(quiet["input_times_s"], data["input_times_s"])
        self.assertEqual(report["spikes"], 0)
        np.testing.assert_allclose(quiet["voltage_v"], -0.052, atol=1e-12)

    def test_silence_boundaries_and_release(self):
        graph = circuit()
        setup = config(graph, 0.1)
        setup["interventions"] = [
            {"kind": "stimulate", "neuron_ids": graph.ids.tolist(), "rate_hz": 700, "start_s": 0, "end_s": 0.1},
            {"kind": "silence", "neuron_ids": graph.ids.tolist(), "start_s": 0.02, "end_s": 0.06},
            {"kind": "silence", "neuron_ids": graph.ids.tolist(), "start_s": 0.04, "end_s": 0.07},
        ]
        data, _ = simulate(graph, setup, record_ids=graph.ids.tolist())
        spikes = data["spike_times_s"]
        self.assertTrue(np.any(spikes < 0.02))
        self.assertFalse(np.any((spikes >= 0.02) & (spikes < 0.07)))
        self.assertTrue(np.any(spikes >= 0.07))
        mask = (data["trace_time_s"] >= 0.02) & (data["trace_time_s"] < 0.07)
        np.testing.assert_allclose(data["voltage_v"][:, mask], -0.052, atol=1e-12)

    def test_inhibition_reduces_driven_target_firing(self):
        graph = circuit(nt=("GABA", "ACH"), counts=(1000,))
        setup = config(graph, 0.15)
        setup["interventions"] = [{"kind": "stimulate", "neuron_ids": graph.ids.tolist(),
                                    "rate_hz": 500, "start_s": 0, "end_s": 0.15}]
        inhibited, _ = simulate(graph, setup)
        disconnected = circuit(nt=("UNKNOWN", "ACH"), counts=(1000,))
        control, _ = simulate(disconnected, setup)
        np.testing.assert_array_equal(inhibited["input_times_s"], control["input_times_s"])
        self.assertLess(inhibited["spike_counts"][1], control["spike_counts"][1])

    def test_overlapping_stimulation_rate_limit(self):
        graph = circuit()
        setup = config(graph)
        item = {"kind": "stimulate", "neuron_ids": [str(graph.ids[0])], "rate_hz": 600, "start_s": 0, "end_s": 0.05}
        setup["interventions"] = [item, dict(item)]
        with self.assertRaisesRegex(ValueError, "rate\\*dt"):
            simulate(graph, setup)

    def test_reject_bad_inputs_and_packet_tampering(self):
        graph = circuit()
        setup = config(graph)
        with self.assertRaises(ValueError):
            simulate(graph, setup, parameters=replace(ModelParameters(), delay_s=0.00185))
        with self.assertRaises(ValueError):
            simulate(graph, setup, record_ids=["1"])
        with self.assertRaises(ValueError):
            simulate(graph, setup, initial_v=[float("nan"), 0])
        setup["interventions"] = [{"kind": "stimulate", "neuron_ids": ["999"], "rate_hz": 100, "start_s": 0, "end_s": 0.05}]
        with self.assertRaises(ValueError):
            simulate(graph, setup)
        setup["interventions"][0].update(neuron_ids=[str(graph.ids[0])], rate_hz=2000)
        with self.assertRaisesRegex(ValueError, "rate\\*dt"):
            simulate(graph, setup)
        packet = json.loads((ROOT / "outputs/flylab/prepared-baseline.json").read_text())
        from_packet(packet)
        packet["graph"]["edges"][0]["synapse_count"] += 1
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            from_packet(packet)


class ImportTests(unittest.TestCase):
    def fixture(self, root, bad=False):
        tables = {
            "neurons.csv.gz": "root_id,nt_type\n720575940600000001,ACH\n720575940600000002,GABA\n",
            "names.csv.gz": "root_id,name\n720575940600000001,one\n720575940600000002,two\n",
            "visual_neuron_types.csv.gz": "root_id,type\n720575940600000001,T5\n",
            "connections_princeton.csv.gz": "pre_root_id,post_root_id,syn_count,neuropil\n720575940600000001,720575940600000002,3,A\n720575940600000001,720575940600000002,4,B\n720575940600000002,720575940600000001,2,A\n",
        }
        if bad:
            tables["connections_princeton.csv.gz"] += "720575940600000001,999,1,A\n"
        for name, content in tables.items():
            with gzip.open(root / name, "wt", newline="") as stream:
                stream.write(content)

    def test_full_stream_aggregation_exact_ids_and_checksum(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.fixture(root)
            report = import_tables(root, root / "cache")
            graph = load_cache(root / "cache")
            self.assertEqual(report["connection_rows"], 3)
            self.assertEqual(report["ordered_pairs"], 2)
            self.assertEqual(graph.ids.tolist(), ["720575940600000001", "720575940600000002"])
            np.testing.assert_array_equal(graph.counts, [7, 2])
            self.assertEqual(len(graph.subset([str(graph.ids[0])]).counts), 0)
            with (root / "cache/connectome.npz").open("ab") as stream:
                stream.write(b"changed")
            with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                load_cache(root / "cache")

    def test_unknown_endpoint_and_late_gzip_corruption_fail(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.fixture(root, bad=True)
            with self.assertRaisesRegex(ValueError, "endpoint missing"):
                import_tables(root, root / "cache")
            self.assertFalse((root / "cache/manifest.json").exists())
            self.fixture(root)
            path = root / "connections_princeton.csv.gz"
            path.write_bytes(path.read_bytes()[:-5])
            with self.assertRaises((EOFError, OSError)):
                import_tables(root, root / "cache")

    def test_bad_graph_arrays_fail(self):
        for change in ("duplicate", "endpoint", "weight"):
            graph = circuit()
            if change == "duplicate":
                graph.ids[1] = graph.ids[0]
            elif change == "endpoint":
                graph.post[0] = 2
            else:
                graph.counts[0] = 0
            with self.assertRaises(ValueError):
                graph.validate()


if __name__ == "__main__":
    unittest.main()
