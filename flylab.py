"""Experiment preparation and environment audit. Does not simulate a fly yet."""

import argparse
import csv
import gzip
import hashlib
import importlib.metadata
import itertools
import json
import math
import os
from pathlib import Path
import platform
import re
import shutil
import sys
from datetime import datetime, timezone
from decimal import Decimal

from outputs.flywire_demo.build_real_connectome_view import EmbeddedDataParser


ROOT = Path(__file__).resolve().parent
VIEW = ROOT / "outputs/flywire_demo/connectome-simulation.html"
REQUIRED_TABLES = {
    "connections_princeton.csv.gz": {"pre_root_id", "post_root_id", "syn_count"},
    "neurons.csv.gz": {"root_id", "nt_type"},
    "names.csv.gz": {"root_id", "name"},
    "visual_neuron_types.csv.gz": {"root_id", "type"},
}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def positive_number(value, label):
    if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
        raise ValueError(f"{label}: expected a finite positive number")
    return Decimal(str(value))


def valid_id(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9]{1,20}", value) is not None


def validate_config(config):
    if not isinstance(config, dict):
        raise ValueError("Experiment must be a JSON object")
    required = {"schema_version", "experiment_id", "seed", "duration_s", "physics_dt_s", "brain_dt_s", "control_dt_s", "body_engine", "brain_engine", "graph_center_id", "interventions"}
    if set(config) != required:
        raise ValueError(f"Missing or unknown fields: {sorted(set(config) ^ required)}")
    if type(config["schema_version"]) is not int or config["schema_version"] != 1:
        raise ValueError("Unsupported schema_version")
    if not isinstance(config["experiment_id"], str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", config["experiment_id"]):
        raise ValueError("Invalid experiment_id")
    if type(config["seed"]) is not int or not 0 <= config["seed"] < 2**32:
        raise ValueError("seed must be an unsigned 32-bit integer")
    if config["body_engine"] != "flygym" or config["brain_engine"] != "brian2":
        raise ValueError("Planned engines are flygym and brian2")
    if not valid_id(config["graph_center_id"]):
        raise ValueError("FlyWire IDs must remain decimal strings")
    clocks = {key: positive_number(config[key], key) for key in ("duration_s", "physics_dt_s", "brain_dt_s", "control_dt_s")}
    for step in ("physics_dt_s", "brain_dt_s", "control_dt_s"):
        if clocks["duration_s"] % clocks[step]:
            raise ValueError(f"duration_s must be an integer multiple of {step}")
    for step in ("physics_dt_s", "brain_dt_s"):
        if clocks["control_dt_s"] % clocks[step]:
            raise ValueError(f"control_dt_s must be an integer multiple of {step}")
    if not isinstance(config["interventions"], list):
        raise ValueError("interventions must be a list")
    for item in config["interventions"]:
        if not isinstance(item, dict) or item.get("kind") not in ("stimulate", "silence"):
            raise ValueError("Intervention kind must be stimulate or silence")
        fields = {"kind", "neuron_ids", "start_s", "end_s"}
        if item["kind"] == "stimulate":
            fields.add("rate_hz")
            positive_number(item.get("rate_hz"), "rate_hz")
        if set(item) != fields:
            raise ValueError("Invalid intervention fields")
        ids = item["neuron_ids"]
        if not isinstance(ids, list) or not ids or not all(valid_id(i) for i in ids) or len(set(ids)) != len(ids):
            raise ValueError("Intervention requires unique string neuron_ids")
        start, end = item["start_s"], item["end_s"]
        if any(type(t) not in (int, float) or not math.isfinite(t) for t in (start, end)) or not 0 <= start < end <= config["duration_s"]:
            raise ValueError("Intervention is outside experiment duration")
        if any(Decimal(str(t)) % clocks["brain_dt_s"] for t in (start, end)):
            raise ValueError("Intervention times must align to brain_dt_s")
    return config


def load_view(path):
    parser = EmbeddedDataParser()
    parser.feed(path.read_text(encoding="utf-8"))
    return json.loads("".join(parser.parts))


def prepare(config, payload):
    validate_config(config)
    center = next((c for c in payload["centers"] if c["id"] == config["graph_center_id"]), None)
    if center is None:
        raise ValueError("Requested center is not present in the cached graph")
    ids = center["simNodes"]
    if not ids or not all(valid_id(i) for i in ids) or len(set(ids)) != len(ids):
        raise ValueError("Graph requires unique string neuron IDs")
    if not all(i in payload["nodes"] and payload["nodes"][i]["id"] == i for i in ids):
        raise ValueError("Missing or inconsistent neuron metadata")
    edges = []
    pairs = set()
    for source, target, count in center["simEdges"]:
        if source not in ids or target not in ids or type(count) is not int or count <= 0:
            raise ValueError("Invalid graph edge")
        if (source, target) in pairs:
            raise ValueError("Duplicate pair: aggregate synapses before preparing")
        pairs.add((source, target))
        edges.append({"source": source, "target": target, "synapse_count": count})
    for intervention in config["interventions"]:
        if not set(intervention["neuron_ids"]) <= set(ids):
            raise ValueError("Intervention references a neuron outside the prepared graph")
    graph = {
        "scope": "induced_demonstration_subgraph",
        "complete_brain": False,
        "source": payload["source"],
        "center_id": center["id"],
        "nodes": [payload["nodes"][i] for i in sorted(ids)],
        "edges": sorted(edges, key=lambda e: (e["source"], e["target"])),
        "weight_semantics": "synapse_count_not_calibrated_conductance",
        "sensory_mapping": None,
        "motor_mapping": None,
    }
    config_hash, graph_hash = digest(config), digest(graph)
    return {
        "schema_version": 1,
        "status": "prepared_not_simulated",
        "experiment_key": digest({"config": config_hash, "graph": graph_hash})[:16],
        "config_sha256": config_hash,
        "graph_sha256": graph_hash,
        "config": config,
        "graph": graph,
        "results": None,
    }


def inspect_table(path, required):
    result = {"path": str(path), "check_scope": "header_and_first_16_rows", "full_integrity_verified": False}
    try:
        result["compressed_bytes"] = path.stat().st_size
        with gzip.open(path, "rt", encoding="utf-8", newline="") as stream:
            reader = csv.DictReader(stream)
            result["columns"] = reader.fieldnames or []
            missing = required - set(result["columns"])
            if missing:
                raise ValueError(f"Missing columns: {sorted(missing)}")
            rows = list(itertools.islice(reader, 16))
            if not rows:
                raise ValueError("Table has no data rows")
            for row in rows:
                for key in required:
                    if row.get(key) is None:
                        raise ValueError(f"Malformed row: {key}")
                    if key.endswith("root_id") and not valid_id(row[key]):
                        raise ValueError(f"Malformed ID: {key}")
                if "syn_count" in required and int(row["syn_count"]) <= 0:
                    raise ValueError("Invalid synapse count")
            result["sample_rows"] = len(rows)
        result["status"] = "ok"
    except (OSError, ValueError, EOFError, csv.Error) as error:
        result.update(status="error", error=str(error))
    return result


def doctor(data_dir):
    dependencies = {}
    for name in ("flygym", "mujoco", "brian2", "numpy"):
        try:
            dependencies[name] = {"version": importlib.metadata.version(name), "runtime_import_tested": False}
        except importlib.metadata.PackageNotFoundError:
            dependencies[name] = {"version": None, "runtime_import_tested": False}
    tables = {name: inspect_table(data_dir / name, columns) for name, columns in REQUIRED_TABLES.items()}
    return {
        "schema_version": 1,
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "python": {"version": platform.python_version(), "executable": sys.executable, "isolated_environment": sys.prefix != sys.base_prefix},
        "system": {"platform": platform.platform(), "logical_cpus": os.cpu_count(), "workspace_disk_free_bytes": shutil.disk_usage(ROOT).free, "ram_bytes": None, "gpu": None},
        "dependencies": dependencies,
        "data_tables": tables,
        "sampled_data_ready": all(t["status"] == "ok" for t in tables.values()),
        "simulation_runtime_verified": False,
        "next_stage": "Isolated FlyGym/MuJoCo environment, body and rendering smoke test",
    }


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=True, allow_nan=False) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    audit = commands.add_parser("doctor", help="Read-only input audit; writes a JSON report")
    audit.add_argument("--data-dir", type=Path, default=Path.home() / "Downloads/mind")
    audit.add_argument("--output", type=Path, default=ROOT / "outputs/flylab/audit.json")
    packet = commands.add_parser("prepare", help="Validate and package an experiment, without simulation")
    packet.add_argument("config", type=Path)
    packet.add_argument("--view", type=Path, default=VIEW)
    packet.add_argument("--output", type=Path, default=ROOT / "outputs/flylab/prepared-baseline.json")
    args = parser.parse_args()
    try:
        if args.command == "doctor":
            result = doctor(args.data_dir)
            print(f"Sampled tables ready: {result['sampled_data_ready']}")
            print("Simulation runtime: not yet verified")
        else:
            result = prepare(json.loads(args.config.read_text(encoding="utf-8")), load_view(args.view))
            print(f"Prepared {len(result['graph']['nodes'])} neurons and {len(result['graph']['edges'])} edges; no simulation executed")
            print(f"Experiment key: {result['experiment_key']}")
        write_json(args.output, result)
        print(args.output.resolve())
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.exit(2, f"Error: {error}\n")


if __name__ == "__main__":
    main()
