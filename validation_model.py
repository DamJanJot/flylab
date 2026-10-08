"""Predeclared engineering calibration and held-out ablations, not a biological fit."""

from dataclasses import dataclass
import math

import numpy as np

from closed_loop_model import INPUT_IDS, OUTPUT_IDS
from flywire_data import Connectome


SCENARIOS = ("neutral", "left", "right")
ABLATIONS = ("disconnected", "silenced", "no_edges", "no_vision", "no_feedback")


def validate_config(config):
    fields = {"schema_version", "training_seeds", "validation_seeds", "stride_reductions",
              "target_turn_deg", "neutral_penalty", "fall_penalty_deg", "full_brain_duration_s"}
    if not isinstance(config, dict) or set(config) != fields or type(config["schema_version"]) is not int or config["schema_version"] != 1:
        raise ValueError("Invalid validation configuration fields/schema")
    for name in ("training_seeds", "validation_seeds"):
        values = config[name]
        if not isinstance(values, list) or not 2 <= len(values) <= 8 or any(type(s) is not int or not 0 <= s < 2**32 for s in values) or len(set(values)) != len(values):
            raise ValueError("Use 2..8 distinct uint32 seeds per partition")
    if set(config["training_seeds"]) & set(config["validation_seeds"]):
        raise ValueError("Training and validation seeds must be disjoint")
    values = config["stride_reductions"]
    if not isinstance(values, list) or not 2 <= len(values) <= 5 or any(type(x) not in (int, float) or not math.isfinite(x) or not 0 < x < 1 for x in values) or len(set(values)) != len(values):
        raise ValueError("Use 2..5 distinct stride reductions in (0,1)")
    for key in ("target_turn_deg", "neutral_penalty", "fall_penalty_deg", "full_brain_duration_s"):
        if type(config[key]) not in (int, float) or not math.isfinite(config[key]) or config[key] <= 0:
            raise ValueError(f"Invalid {key}")
    if config["target_turn_deg"] > 90 or not 0.02 <= config["full_brain_duration_s"] <= 1:
        raise ValueError("Target or full brain duration outside bounded protocol")
    return config


@dataclass(frozen=True)
class Assay:
    phase: str
    profile: str
    gain: float
    scenario: str
    condition: str
    seed: int
    repeat: bool = False

    @property
    def key(self):
        return f"{self.phase}-{self.profile}-{self.scenario}-{self.condition}-{self.seed}" + ("-repeat" if self.repeat else "")


def training_plan(config):
    validate_config(config)
    return [Assay("training", f"candidate_{i}", gain, scenario, "connected", seed)
            for i, gain in enumerate(config["stride_reductions"])
            for seed in config["training_seeds"] for scenario in SCENARIOS]


def evaluation_plan(config, selected_gain, default_gain):
    validate_config(config)
    if selected_gain not in config["stride_reductions"] or not 0 < default_gain < 1:
        raise ValueError("Unknown selected/default gain")
    plan = [Assay("validation", profile, gain, scenario, "connected", seed)
            for profile, gain in (("default", default_gain), ("selected", selected_gain))
            for seed in config["validation_seeds"] for scenario in SCENARIOS]
    plan += [Assay("validation", "selected", selected_gain, scenario, condition, seed)
             for seed in config["validation_seeds"] for scenario in ("left", "right") for condition in ABLATIONS]
    plan += [Assay("validation", "selected", selected_gain, scenario, "connected", config["validation_seeds"][i], True)
             for i, scenario in enumerate(("left", "right"))]
    return plan


def objective(rows, config):
    """One paired seed, including its neutral drift and every fall; degrees-equivalent cost."""
    if set(rows) != set(SCENARIOS):
        raise ValueError("Objective requires a complete neutral/left/right pair")
    headings = {key: value["heading_change_deg"] for key, value in rows.items()}
    if not all(math.isfinite(x) for x in headings.values()):
        raise ValueError("Nonfinite heading")
    neutral = headings["neutral"]
    errors = [abs(headings[side] - neutral - sign * config["target_turn_deg"])
              for side, sign in (("left", 1), ("right", -1))]
    return float(np.mean(errors) + config["neutral_penalty"] * abs(neutral)
                 + config["fall_penalty_deg"] * sum(bool(r["fell"]) for r in rows.values()) / 3)


def select_candidate(reports, config):
    plan = training_plan(config)
    if set(reports) != {a.key for a in plan}:
        raise ValueError("Selection requires all and only training trials")
    scores = []
    for i, gain in enumerate(config["stride_reductions"]):
        values = []
        for seed in config["training_seeds"]:
            rows = {scenario: reports[Assay("training", f"candidate_{i}", gain, scenario, "connected", seed).key]["behavior"] for scenario in SCENARIOS}
            values.append(objective(rows, config))
        scores.append({"profile": f"candidate_{i}", "gain": gain, "cost_per_seed": values, "mean_cost": float(np.mean(values))})
    # Declared candidate order breaks ties, never the held-out outcomes.
    winner = min(range(len(scores)), key=lambda i: scores[i]["mean_cost"])
    return {"gain": scores[winner]["gain"], "profile": scores[winner]["profile"], "scores": scores,
            "target_turn_deg": config["target_turn_deg"],
            "training_seeds": config["training_seeds"], "validation_seeds": config["validation_seeds"],
            "criterion": "mean paired absolute turn error + neutral penalty + fall penalty; first candidate breaks ties",
            "biologically_calibrated": False}


def without_feedback(graph):
    """Remove only existing DNa02 -> DNa03 edges; preserve all other weights and IDs."""
    mask = np.isin(graph.ids[graph.pre], OUTPUT_IDS) & np.isin(graph.ids[graph.post], INPUT_IDS)
    if not np.any(mask):
        raise ValueError("No feedback edges in selected graph")
    keep = ~mask
    return Connectome(graph.ids.copy(), graph.nt.copy(), graph.pre[keep].copy(), graph.post[keep].copy(),
                      graph.counts[keep].copy(), {**graph.provenance, "control": "DNa02_to_DNa03_removed"}).validate()
