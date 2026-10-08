import copy
import gzip
import json
from pathlib import Path
import tempfile
import unittest

import flylab


class ExperimentTests(unittest.TestCase):
    def setUp(self):
        self.config = json.loads((flylab.ROOT / "experiments/baseline.json").read_text())
        self.payload = flylab.load_view(flylab.VIEW)

    def test_reproducible_preparation_keeps_large_ids_and_raw_weights(self):
        first = flylab.prepare(self.config, self.payload)
        self.assertEqual(first, flylab.prepare(copy.deepcopy(self.config), self.payload))
        self.assertEqual(first["status"], "prepared_not_simulated")
        self.assertIsNone(first["results"])
        self.assertFalse(first["graph"]["complete_brain"])
        self.assertIsNone(first["graph"]["motor_mapping"])
        self.assertTrue(all(isinstance(n["id"], str) for n in first["graph"]["nodes"]))
        self.assertEqual(len(first["graph"]["nodes"]), 13)
        center = next(c for c in self.payload["centers"] if c["id"] == self.config["graph_center_id"])
        self.assertEqual(sum(e["synapse_count"] for e in first["graph"]["edges"]), sum(e[2] for e in center["simEdges"]))
        self.config["seed"] += 1
        self.assertNotEqual(first["experiment_key"], flylab.prepare(self.config, self.payload)["experiment_key"])

    def test_invalid_configuration(self):
        for field, value in (("seed", True), ("seed", -1), ("graph_center_id", 720575940626979621), ("duration_s", float("nan")), ("brain_dt_s", 0.0003), ("control_dt_s", 0), ("experiment_id", "../escape")):
            with self.subTest(field=field, value=value):
                config = {**self.config, field: value}
                with self.assertRaises(ValueError):
                    flylab.validate_config(config)

    def test_intervention_validation(self):
        item = {"kind": "stimulate", "neuron_ids": [self.config["graph_center_id"]], "start_s": 0.01, "end_s": 0.1, "rate_hz": 20}
        self.config["interventions"] = [item]
        flylab.prepare(self.config, self.payload)
        for key, value in (("end_s", 1), ("start_s", 0.00015), ("rate_hz", -1), ("neuron_ids", ["999"])):
            with self.subTest(key=key):
                changed = {**self.config, "interventions": [{**item, key: value}]}
                with self.assertRaises(ValueError):
                    flylab.prepare(changed, self.payload)

    def test_bad_edges_and_unknown_center_fail(self):
        config = {**self.config, "graph_center_id": "999"}
        with self.assertRaises(ValueError):
            flylab.prepare(config, self.payload)
        center = next(c for c in self.payload["centers"] if c["id"] == self.config["graph_center_id"])
        center["simEdges"].append(center["simEdges"][0])
        with self.assertRaisesRegex(ValueError, "Duplicate pair"):
            flylab.prepare(self.config, self.payload)

    def test_table_audit_reports_missing_headers_and_corruption(self):
        with tempfile.TemporaryDirectory() as directory:
            table = Path(directory) / "connections.csv.gz"
            with gzip.open(table, "wt") as stream:
                stream.write("pre_root_id,post_root_id,syn_count\n720575940626979621,720575940628908548,7\n")
            required = flylab.REQUIRED_TABLES["connections_princeton.csv.gz"]
            report = flylab.inspect_table(table, required)
            self.assertEqual(report["status"], "ok")
            self.assertFalse(report["full_integrity_verified"])
            self.assertEqual(flylab.inspect_table(table, {"absent"})["status"], "error")
            table.write_bytes(b"invalid gzip")
            self.assertEqual(flylab.inspect_table(table, required)["status"], "error")
            table.unlink()
            self.assertEqual(flylab.inspect_table(table, required)["status"], "error")


if __name__ == "__main__":
    unittest.main()
