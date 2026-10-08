"""Validated local FlyWire import. Root IDs never pass through floating point."""

import argparse
from array import array
from collections import Counter
import csv
from dataclasses import dataclass
import gzip
import hashlib
import json
from pathlib import Path
import time

import numpy as np
from scipy.sparse import coo_matrix

from flylab import ROOT, digest, valid_id, write_json


def file_sha256(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def root_id(value):
    if not valid_id(value) or not 0 < int(value) < 2**64 or str(int(value)) != value:
        raise ValueError(f"Invalid uint64 decimal root ID: {value!r}")
    return value


@dataclass
class Connectome:
    ids: np.ndarray
    nt: np.ndarray
    pre: np.ndarray
    post: np.ndarray
    counts: np.ndarray
    provenance: dict

    def validate(self):
        if any(a.ndim != 1 for a in (self.ids, self.nt, self.pre, self.post, self.counts)):
            raise ValueError("Graph arrays must be one-dimensional")
        if not len(self.ids) or len(set(self.ids.tolist())) != len(self.ids):
            raise ValueError("Neuron IDs must be nonempty and unique")
        for value in self.ids.tolist():
            root_id(value)
        if len(self.nt) != len(self.ids) or self.nt.dtype.kind != "U":
            raise ValueError("Invalid neurotransmitter metadata")
        if not len(self.pre) == len(self.post) == len(self.counts):
            raise ValueError("Inconsistent edge arrays")
        for indices in (self.pre, self.post):
            if indices.dtype.kind not in "iu" or np.any(indices < 0) or np.any(indices >= len(self.ids)):
                raise ValueError("Invalid endpoint index")
        if self.counts.dtype.kind not in "iu" or np.any(self.counts <= 0):
            raise ValueError("Counts must be positive integers")
        keys = self.pre.astype(np.int64) * len(self.ids) + self.post
        if len(keys) > 1 and np.any(keys[1:] <= keys[:-1]):
            raise ValueError("Edges must be sorted and aggregated by ordered pair")
        return self

    def subset(self, ids):
        requested = set(ids)
        if not requested or not requested <= set(self.ids.tolist()):
            raise ValueError("Unknown or empty subset")
        selected = np.flatnonzero(np.isin(self.ids, list(requested)))
        remap = np.full(len(self.ids), -1, dtype=np.int32)
        remap[selected] = np.arange(len(selected), dtype=np.int32)
        mask = (remap[self.pre] >= 0) & (remap[self.post] >= 0)
        return Connectome(self.ids[selected], self.nt[selected], remap[self.pre[mask]],
                          remap[self.post[mask]], self.counts[mask],
                          {**self.provenance, "scope": "induced_subgraph", "complete_brain": False}).validate()


def from_packet(packet):
    graph = packet["graph"]
    if digest(graph) != packet["graph_sha256"] or digest(packet["config"]) != packet["config_sha256"]:
        raise ValueError("Prepared packet hash mismatch")
    nodes = sorted(graph["nodes"], key=lambda n: n["id"])
    lookup = {node["id"]: i for i, node in enumerate(nodes)}
    edges = sorted(graph["edges"], key=lambda e: (e["source"], e["target"]))
    if any(type(e["synapse_count"]) is not int for e in edges):
        raise ValueError("Non-integer synapse count")
    return Connectome(np.array([n["id"] for n in nodes]), np.array([n["nt"] for n in nodes]),
                      np.array([lookup[e["source"]] for e in edges], dtype=np.int32),
                      np.array([lookup[e["target"]] for e in edges], dtype=np.int32),
                      np.array([e["synapse_count"] for e in edges], dtype=np.int64),
                      {"scope": graph["scope"], "complete_brain": False,
                       "source": graph["source"], "graph_sha256": packet["graph_sha256"]}).validate()


def read_rows(path, required, audit):
    before = path.stat()
    checksum = file_sha256(path)
    rows = 0
    with gzip.open(path, "rt", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if not required <= set(reader.fieldnames or []):
            raise ValueError(f"Missing columns in {path.name}")
        for row in reader:
            rows += 1
            if None in row or any(value is None for value in row.values()):
                raise ValueError(f"Malformed CSV: {path.name}, row {rows}")
            yield row
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError(f"Source changed during import: {path}")
    if not rows:
        raise ValueError(f"Empty table: {path}")
    audit[path.name] = {"path": str(path.resolve()), "sha256": checksum,
                        "compressed_bytes": before.st_size, "rows": rows,
                        "gzip_read_to_eof": True, "all_rows_validated": True}


def import_tables(data_dir, output):
    started = time.perf_counter()
    audit, neurons = {}, {}
    for row in read_rows(data_dir / "neurons.csv.gz", {"root_id", "nt_type"}, audit):
        neuron = root_id(row["root_id"])
        if neuron in neurons or len(row["nt_type"]) > 32:
            raise ValueError("Duplicate neuron or invalid NT label")
        neurons[neuron] = row["nt_type"].strip().upper() or "UNKNOWN"
    ids = np.array(sorted(neurons), dtype="U20")
    nt = np.array([neurons[i] for i in ids], dtype="U32")
    lookup = {neuron: i for i, neuron in enumerate(ids)}
    for filename, label in (("names.csv.gz", "name"), ("visual_neuron_types.csv.gz", "type")):
        for row in read_rows(data_dir / filename, {"root_id", label}, audit):
            root_id(row["root_id"])
    pre, post, counts = array("i"), array("i"), array("q")
    required = {"pre_root_id", "post_root_id", "syn_count"}
    for row in read_rows(data_dir / "connections_princeton.csv.gz", required, audit):
        source, target = root_id(row["pre_root_id"]), root_id(row["post_root_id"])
        if source not in lookup or target not in lookup:
            raise ValueError(f"Connection endpoint missing from neurons table: {source}, {target}")
        value = int(row["syn_count"])
        if not 0 < value < 2**31:
            raise ValueError("Invalid synapse count")
        pre.append(lookup[source])
        post.append(lookup[target])
        counts.append(value)
    matrix = coo_matrix((np.asarray(counts), (np.asarray(pre), np.asarray(post))),
                        shape=(len(ids), len(ids)), dtype=np.int64).tocsr().tocoo()
    graph = Connectome(ids, nt, matrix.row.astype(np.int32), matrix.col.astype(np.int32),
                        matrix.data, {}).validate()
    output.mkdir(parents=True, exist_ok=True)
    archive = output / "connectome.npz"
    temp = output / "connectome.pending.npz"
    np.savez_compressed(temp, ids=ids, nt=nt, pre=graph.pre, post=graph.post, counts=graph.counts)
    temp.replace(archive)
    report = {"schema_version": 1, "status": "imported", "source_files": audit,
              "source": "Local FlyWire FAFB v783 tables; release not independently matched to remote checksums",
              "scope": "entire_local_thresholded_table", "complete_brain": False,
              "neurons": len(ids), "connection_rows": len(counts), "ordered_pairs": len(graph.pre),
              "synapse_count_sum": int(graph.counts.sum()), "autapse_pairs": int(np.sum(graph.pre == graph.post)),
              "neurotransmitters": dict(Counter(nt.tolist())),
              "policy": "Sum neuropil rows per directed pair; no extra threshold; preserve self edges and all NT labels",
              "archive_sha256": file_sha256(archive), "importer_sha256": file_sha256(__file__),
              "elapsed_s": time.perf_counter() - started}
    write_json(output / "manifest.json", report)
    return report


def load_cache(directory):
    report = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    path = directory / "connectome.npz"
    if report["status"] != "imported" or file_sha256(path) != report["archive_sha256"]:
        raise ValueError("Connectome cache checksum mismatch")
    with np.load(path, allow_pickle=False) as data:
        graph = Connectome(*(data[key] for key in ("ids", "nt", "pre", "post", "counts")), report).validate()
    if len(graph.ids) != report["neurons"] or len(graph.pre) != report["ordered_pairs"]:
        raise ValueError("Cache manifest counts do not match")
    return graph


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path.home() / "Downloads/mind")
    parser.add_argument("--output", type=Path, default=ROOT / "outputs/flylab/connectome-cache")
    args = parser.parse_args()
    print(json.dumps(import_tables(args.data_dir, args.output), indent=2))
