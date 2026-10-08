"""Audited steering fragment and explicitly surrogate sensor/motor adapters."""

import ast
from dataclasses import asdict, dataclass
import math

import numpy as np

from flylab import digest
from flywire_data import Connectome, load_cache, read_rows


# Local v783 labels and hemisphere annotations, checked again at every run.
CELLS = (
    ("720575940620918789", "DNa03", "left"),
    ("720575940630085583", "DNa03", "right"),
    ("720575940629327659", "DNa02", "left"),
    ("720575940604737708", "DNa02", "right"),
)
INPUT_IDS = tuple(row[0] for row in CELLS[:2])
OUTPUT_IDS = tuple(row[0] for row in CELLS[2:])
SOURCES = {
    "steering": "https://elifesciences.org/articles/102230v1",
    "stride_modulation": "https://doi.org/10.1016/j.cell.2024.08.033",
    "data_release": "https://zenodo.org/records/10676866",
    "cpg": "https://neuromechfly.org/tutorials/4a_cpg_controller/",
}


def load_steering_circuit(cache, data_dir):
    full = load_cache(cache)
    graph = full.subset([row[0] for row in CELLS])
    audit, labels, classification = {}, {}, {}
    for row in read_rows(data_dir / "processed_labels.csv.gz", {"root_id", "processed_labels"}, audit):
        if row["root_id"] in graph.ids:
            if row["root_id"] in labels:
                raise ValueError("Duplicate selected label record")
            value = ast.literal_eval(row["processed_labels"])
            if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
                raise ValueError("Invalid selected cell labels")
            labels[row["root_id"]] = value
    for row in read_rows(data_dir / "classification.csv.gz", {"root_id", "side", "super_class"}, audit):
        if row["root_id"] in graph.ids:
            if row["root_id"] in classification:
                raise ValueError("Duplicate selected classification")
            classification[row["root_id"]] = row
    cells = []
    for root, cell_type, side in CELLS:
        if not any(label == cell_type or label.startswith(cell_type + " (") for label in labels.get(root, [])):
            raise ValueError(f"Missing expected label for {root}")
        row = classification.get(root, {})
        if row.get("side") != side or row.get("super_class") != "descending":
            raise ValueError(f"Unexpected hemisphere/class for {root}")
        cells.append({"root_id": root, "cell_type": cell_type, "side": side,
                      "local_labels": labels[root], "classification": row})
    lookup = {root: i for i, root in enumerate(graph.ids)}
    edges = {(int(a), int(b)): int(c) for a, b, c in zip(graph.pre, graph.post, graph.counts)}
    for source, target in zip(INPUT_IDS, OUTPUT_IDS):
        if (lookup[source], lookup[target]) not in edges:
            raise ValueError("Missing DNa03 -> DNa02 connection")
    metadata = {
        "status": "real_induced_connectome_fragment_with_surrogate_interfaces",
        "complete_brain": False, "biologically_validated": False,
        "nodes": cells, "source": full.provenance, "annotation_files": audit,
        "edges": [{"pre_root_id": str(graph.ids[a]), "post_root_id": str(graph.ids[b]),
                   "synapse_count": int(c)} for a, b, c in zip(graph.pre, graph.post, graph.counts)],
        "neurotransmitters": dict(zip(graph.ids.tolist(), graph.nt.tolist())),
        "input_root_ids_left_right": list(INPUT_IDS), "output_root_ids_left_right": list(OUTPUT_IDS),
        "selection": "Bilateral DNa03/DNa02; published steering roles, not selection by synapse count",
        "sources": SOURCES,
        "sensor_mapping_status": "surrogate high-order drive, NOT anatomical retina-to-DNa03 wiring",
        "motor_mapping_status": "surrogate CPG stride amplitude, NOT reconstructed VNC or motor neurons",
        "excluded": "All other neurons/edges, central-complex computation, VNC, muscles' neural drive",
    }
    metadata["mapping_sha256"] = digest(metadata)
    graph.provenance = {**graph.provenance, "mapping_sha256": metadata["mapping_sha256"]}
    return graph, metadata


def without_edges(graph):
    return Connectome(graph.ids.copy(), graph.nt.copy(), np.array([], dtype=np.int32),
                      np.array([], dtype=np.int32), np.array([], dtype=np.int64),
                      {**graph.provenance, "control": "all_fragment_edges_removed"}).validate()


@dataclass(frozen=True)
class CouplingParameters:
    baseline_input_hz: float = 20.0
    visual_gain_hz: float = 180.0
    contrast_scale: float = 0.15
    input_quantum_hz: float = 1.0
    filter_tau_s: float = 0.04
    steering_scale_hz: float = 100.0
    maximum_stride_reduction: float = 0.65

    def validate(self):
        if any(type(x) not in (float, int) or not math.isfinite(x) or x <= 0 for x in asdict(self).values()):
            raise ValueError("Coupling parameters must be positive finite numbers")
        if self.maximum_stride_reduction >= 1 or self.input_quantum_hz > self.baseline_input_hz:
            raise ValueError("Invalid stride reduction or rate quantization")
        if (self.baseline_input_hz + self.visual_gain_hz) / self.input_quantum_hz > 10000:
            raise ValueError("Rate quantization exceeds diagnostic budget")
        return self


class SteeringAdapter:
    def __init__(self, parameters=None):
        self.parameters = (parameters or CouplingParameters()).validate()
        self.filtered_hz = np.zeros(2)

    def sensory_drive(self, encoded_hz, reference_hz, channel_names, maximum_rate_hz):
        encoded, reference = np.asarray(encoded_hz), np.asarray(reference_hz)
        if encoded.shape != (len(channel_names),) or reference.shape != encoded.shape:
            raise ValueError("Invalid encoded sensor shape")
        if not math.isfinite(maximum_rate_hz) or maximum_rate_hz <= 0:
            raise ValueError("Invalid encoder maximum")
        if not np.all(np.isfinite(encoded)) or not np.all(np.isfinite(reference)):
            raise ValueError("Nonfinite sensory drive")
        if np.any(encoded < 0) or np.any(encoded > maximum_rate_hz) or np.any(reference < 0) or np.any(reference > maximum_rate_hz):
            raise ValueError("Sensory rates out of bounds")
        indices = [channel_names.index(f"vision.{side}.dark") for side in ("left", "right")]
        darkening = (encoded[indices] - reference[indices]) / maximum_rate_hz
        p = self.parameters
        contrast = np.clip((darkening[0] - darkening[1]) / p.contrast_scale, -1, 1)
        rates = p.baseline_input_hz + p.visual_gain_hz * np.maximum([contrast, -contrast], 0)
        # Explicit quantization limits insignificant renderer jitter; does not guarantee cross-platform identity.
        rates = np.floor(rates / p.input_quantum_hz + 0.5) * p.input_quantum_hz
        return rates, float(contrast)

    def motor_command(self, counts_left_right, dt, connected=True):
        counts = np.asarray(counts_left_right)
        if counts.shape != (2,) or not np.all(np.isfinite(counts)) or np.any(counts < 0) or np.any(counts != np.floor(counts)):
            raise ValueError("Expected two nonnegative spike counts")
        if type(dt) not in (float, int) or not math.isfinite(dt) or dt <= 0:
            raise ValueError("Invalid adapter timestep")
        p = self.parameters
        decay = np.exp(-dt / p.filter_tau_s)
        self.filtered_hz = decay * self.filtered_hz + (1 - decay) * counts / dt
        delta = np.clip((self.filtered_hz[0] - self.filtered_hz[1]) / p.steering_scale_hz, -1, 1)
        if not connected:
            delta = 0.0
        # DNa02-associated ipsilateral stride shortening, with uncalibrated gain.
        return 1.0 - p.maximum_stride_reduction * np.maximum([delta, -delta], 0)
