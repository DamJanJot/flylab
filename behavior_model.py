"""Stage-6 protocols and descriptive, paired outcome metrics (not validation of biology)."""

from dataclasses import dataclass
from decimal import Decimal
import math

import numpy as np

from closed_loop_experiment import validate_configuration
from closed_loop_model import INPUT_IDS, OUTPUT_IDS


SCENARIOS = ("neutral", "left", "right", "obstacle")
CONDITIONS = ("connected", "disconnected", "silenced")
STIMULATIONS = ("stimulate_left", "stimulate_right")


def validate_suite(config):
    required = {"schema_version", "simulation", "seeds", "scenarios", "conditions", "stimulations",
                "cue_start_s", "intervention_start_s", "intervention_end_s", "stimulation_hz", "obstacle", "outcome"}
    if not isinstance(config, dict) or set(config) != required or type(config["schema_version"]) is not int or config["schema_version"] != 1:
        raise ValueError("Invalid behavior suite fields/schema")
    settings, parameters = validate_configuration(config["simulation"])
    seeds = config["seeds"]
    if not isinstance(seeds, list) or not 2 <= len(seeds) <= 8 or any(type(s) is not int or not 0 <= s < 2**32 for s in seeds) or len(set(seeds)) != len(seeds):
        raise ValueError("Use 2..8 distinct uint32 seeds")
    for key, expected in (("scenarios", SCENARIOS), ("conditions", CONDITIONS), ("stimulations", STIMULATIONS)):
        if config[key] != list(expected):
            raise ValueError(f"Stage-6 suite requires complete ordered {key}")
    for key in ("cue_start_s", "intervention_start_s", "intervention_end_s", "stimulation_hz"):
        if type(config[key]) not in (float, int) or not math.isfinite(config[key]) or config[key] <= 0:
            raise ValueError(f"Invalid {key}")
    for key in ("cue_start_s", "intervention_start_s", "intervention_end_s"):
        if Decimal(str(config[key])) % Decimal(str(settings["sensory_dt_s"])):
            raise ValueError("Protocol times must align with control bins")
    if not 0 < config["cue_start_s"] < settings["duration_s"]:
        raise ValueError("Cue window outside episode")
    if not 0 < config["intervention_start_s"] < config["intervention_end_s"] < settings["duration_s"]:
        raise ValueError("Intervention needs pre/post windows")
    if config["stimulation_hz"] * settings["brain_dt_s"] > 0.1:
        raise ValueError("Intervention exceeds Poisson limit")
    obstacle = config["obstacle"]
    if not isinstance(obstacle, dict) or set(obstacle) != {"center_mm", "half_size_mm"}:
        raise ValueError("Invalid obstacle fields")
    for key in obstacle:
        values = obstacle[key]
        if not isinstance(values, list) or len(values) != 3 or any(type(x) not in (float, int) or not math.isfinite(x) for x in values):
            raise ValueError("Obstacle vectors must contain three finite numbers")
    center, half = np.array(obstacle["center_mm"]), np.array(obstacle["half_size_mm"])
    if np.any(half <= 0) or np.any(half > 10) or center[0] - half[0] < 3 or not np.isclose(center[2], half[2]):
        raise ValueError("Obstacle must rest on floor, beyond spawn, with bounded positive size")
    outcome = config["outcome"]
    if not isinstance(outcome, dict) or set(outcome) != {"gate_x_mm", "corridor_half_width_mm", "upright_min_z", "direction_threshold_deg"}:
        raise ValueError("Invalid outcome fields")
    if any(type(x) not in (float, int) or not math.isfinite(x) or x <= 0 for x in outcome.values()):
        raise ValueError("Outcome thresholds must be positive finite numbers")
    if not center[0] + half[0] < outcome["gate_x_mm"] <= 25 or outcome["upright_min_z"] > 1 or outcome["corridor_half_width_mm"] > 20:
        raise ValueError("Invalid outcome bounds")
    return settings, parameters


@dataclass(frozen=True)
class Trial:
    scenario: str
    condition: str
    seed: int
    repeat: bool = False

    @property
    def key(self):
        return f"{self.scenario}-{self.condition}-seed{self.seed}" + ("-repeat" if self.repeat else "")


def trial_plan(config):
    validate_suite(config)
    trials = [Trial(scenario, condition, seed) for scenario in config["scenarios"]
              for seed in config["seeds"] for condition in config["conditions"]]
    trials.extend(Trial("neutral", condition, seed) for seed in config["seeds"] for condition in config["stimulations"])
    trials.extend(Trial(scenario, "connected", config["seeds"][0], True) for scenario in ("left", "obstacle"))
    return trials


class TrialProtocol:
    """Same four Poisson sources in every stage-6 condition, including zero-rate ones."""

    input_ids = INPUT_IDS + OUTPUT_IDS

    def __init__(self, config, trial):
        settings, _ = validate_suite(config)
        if trial not in trial_plan(config):
            raise ValueError("Trial not in declared suite")
        self.trial = trial
        dt = settings["sensory_dt_s"]
        self.cue_start = round(config["cue_start_s"] / dt)
        self.start = round(config["intervention_start_s"] / dt)
        self.end = round(config["intervention_end_s"] / dt)
        self.stimulation_hz = config["stimulation_hz"]

    def panels(self, index):
        active = index >= self.cue_start
        return (active and self.trial.scenario == "left", active and self.trial.scenario == "right")

    def neural_input(self, index, visual_rates, default_silence=()):
        rates = np.zeros(4)
        rates[:2] = visual_rates
        active = self.start <= index < self.end
        condition = self.trial.condition
        if condition in STIMULATIONS and active:
            rates[2 if condition == "stimulate_left" else 3] = self.stimulation_hz
        # Whole-episode silencing is the paired motor-output control, as in stage 5.
        silent = OUTPUT_IDS if condition == "silenced" else tuple(default_silence)
        return rates, silent


def behavior_metrics(state, physics, config, scenario):
    """All behavioral failures are retained. A gate measures thorax crossing only."""
    outcome = config["outcome"]
    times = np.asarray(physics["time_s"])
    xy = np.asarray(physics["thorax_m"])[:, :2] * 1000
    minimum_up = float(np.min(physics["up_axis_z"]))
    crossed = (xy[:, 0] >= outcome["gate_x_mm"]) & (np.abs(xy[:, 1]) <= outcome["corridor_half_width_mm"])
    in_gate = np.flatnonzero(crossed)
    obstacle_contact = np.asarray(physics["obstacle_contact"], dtype=bool)
    contact_indices = np.flatnonzero(obstacle_contact)
    dt = config["simulation"]["sensors"]["physics_dt_s"]
    heading = np.rad2deg(state["heading_rad"] - state["heading_rad"][0])
    mean_command = np.mean(state["applied_command"], axis=0)
    return {
        "heading_change_deg": float(heading[-1]),
        "path_length_mm": float(np.linalg.norm(np.diff(xy, axis=0), axis=1).sum()),
        "forward_progress_mm": float(xy[-1, 0] - xy[0, 0]),
        "final_y_mm": float(xy[-1, 1]),
        "mean_command_left_right": mean_command.tolist(),
        "dn_spikes_left_right": state["dn_counts"].sum(axis=0).tolist(),
        "minimum_up_axis_z": minimum_up,
        "fell": minimum_up < outcome["upright_min_z"],
        "thorax_reached_gate": bool(len(in_gate)),
        "gate_time_s": float(times[in_gate[0]]) if len(in_gate) else None,
        "upright_gate_success": bool(len(in_gate) and minimum_up >= outcome["upright_min_z"]),
        "obstacle_contact_s": float(obstacle_contact.sum() * dt),
        "obstacle_first_contact_s": float(times[contact_indices[0]]) if len(contact_indices) else None,
        "body_obstacle_contact_s": float(np.count_nonzero(physics["body_obstacle_contact"]) * dt),
        "floor_or_obstacle_support_fraction": float(np.any(physics["leg_contacts"], axis=1).mean()),
        "scenario": scenario,
    }


def summarize_trials(reports, config):
    """Pair by seed and environment, never average unpaired recordings."""
    expected = trial_plan(config)
    if set(reports) != {trial.key for trial in expected}:
        raise ValueError("Cannot summarize an incomplete or extra trial set")
    groups, paired, interventions = [], [], []
    for scenario in SCENARIOS:
        for condition in CONDITIONS + (STIMULATIONS if scenario == "neutral" else ()):
            rows = [reports[Trial(scenario, condition, seed).key]["behavior"] for seed in config["seeds"]]
            headings = np.array([row["heading_change_deg"] for row in rows])
            groups.append({"scenario": scenario, "condition": condition, "n": len(rows),
                           "seeds": list(config["seeds"]), "heading_deg_per_seed": headings.tolist(),
                           "heading_mean_deg": float(headings.mean()), "heading_min_deg": float(headings.min()),
                           "heading_max_deg": float(headings.max()), "heading_sd_deg": float(headings.std(ddof=1)),
                           "upright_gate_successes": sum(row["upright_gate_success"] for row in rows),
                           "falls": sum(row["fell"] for row in rows),
                           "obstacle_contact_runs": sum(row["obstacle_contact_s"] > 0 for row in rows)})
        for seed in config["seeds"]:
            current = reports[Trial(scenario, "connected", seed).key]["behavior"]
            control = reports[Trial(scenario, "disconnected", seed).key]["behavior"]
            neutral = reports[Trial("neutral", "connected", seed).key]["behavior"]
            direction = 1 if scenario == "left" else -1 if scenario == "right" else 0
            cue_delta = current["heading_change_deg"] - neutral["heading_change_deg"]
            paired.append({"scenario": scenario, "seed": seed,
                           "connected_minus_disconnected_heading_deg": current["heading_change_deg"] - control["heading_change_deg"],
                           "connected_minus_neutral_heading_deg": cue_delta,
                           "directional_response": bool(direction * cue_delta >= config["outcome"]["direction_threshold_deg"]) if direction else None,
                           "connected_minus_disconnected_progress_mm": current["forward_progress_mm"] - control["forward_progress_mm"]})
    for seed in config["seeds"]:
        baseline = reports[Trial("neutral", "connected", seed).key]["behavior"]
        for condition in STIMULATIONS:
            current = reports[Trial("neutral", condition, seed).key]["behavior"]
            side = 0 if condition == "stimulate_left" else 1
            interventions.append({"seed": seed, "condition": condition,
                                  "heading_delta_deg": current["heading_change_deg"] - baseline["heading_change_deg"],
                                  "target_dn_spike_delta": current["dn_spikes_left_right"][side] - baseline["dn_spikes_left_right"][side]})
    return {"groups": groups, "paired": paired, "interventions": interventions,
            "statistics": "descriptive only; seeded model trials are not biological replicates",
            "outcome_definition": config["outcome"], "no_exclusions": True}
