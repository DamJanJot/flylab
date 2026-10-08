"""Brian2 LIF model, with explicit surrogate parameters and timed interventions."""

from dataclasses import asdict, dataclass
from decimal import Decimal
import importlib.metadata
import math
import os
import time

from flylab import ROOT, digest, validate_config

os.environ.setdefault("MPLCONFIGDIR", str(ROOT / "work/brain-cache/matplotlib"))
os.environ.setdefault("MPLBACKEND", "Agg")

# Brian2's Windows CPU cache is hard-coded under expanduser('~') at import.
# Redirect only that import to a project-local home; restore the real home after it.
_home_key = "USERPROFILE" if os.name == "nt" else "HOME"
_original_home = os.environ.get(_home_key)
os.environ[_home_key] = str(ROOT / "work/brain-cache/home")
try:
    import brian2 as b2
finally:
    if _original_home is None:
        os.environ.pop(_home_key, None)
    else:
        os.environ[_home_key] = _original_home
import numpy as np
import psutil


@dataclass(frozen=True)
class ModelParameters:
    rest_v: float = -0.052
    reset_v: float = -0.052
    threshold_v: float = -0.045
    membrane_tau_s: float = 0.020
    synapse_tau_s: float = 0.005
    refractory_s: float = 0.0022
    delay_s: float = 0.0018
    weight_per_contact_v: float = 0.000275
    input_kick_v: float = 0.06875

    def validate(self, dt):
        if any(type(v) not in (int, float) or not math.isfinite(v) for v in asdict(self).values()):
            raise ValueError("Parameters must be finite numbers")
        if not self.rest_v < self.threshold_v or not self.reset_v < self.threshold_v:
            raise ValueError("Rest and reset must be below threshold")
        for key, value in asdict(self).items():
            if key not in ("rest_v", "reset_v", "threshold_v") and value <= 0:
                raise ValueError(f"Parameter must be positive: {key}")
        for value in (self.delay_s, self.refractory_s):
            if Decimal(str(value)) % Decimal(str(dt)):
                raise ValueError("Delay and refractory time must align to brain_dt_s")
        if self.membrane_tau_s == self.synapse_tau_s:
            raise ValueError("Use distinct time constants for this exact state updater")


# These are model assumptions, not measured receptor-specific effects.
NT_SIGN = {"ACH": 1, "GABA": -1, "GLUT": -1}


def polarity(nt):
    return np.array([NT_SIGN.get(value, 0) for value in nt], dtype=np.int8)


class NeuralRuntime:
    """One persistent Brian2 network. Sequential runs preserve delays and state.

    Brian2's NumPy RNG is process-global: use one active runtime per process.
    Both batch and closed-loop experiments use this same construction.
    """

    def __init__(self, graph, dt, seed, input_ids=(), parameters=None,
                 record_ids=(), initial_v=None):
        graph.validate()
        if type(dt) not in (int, float) or not math.isfinite(dt) or dt <= 0:
            raise ValueError("Invalid neural timestep")
        if type(seed) is not int or not 0 <= seed < 2**32:
            raise ValueError("Invalid neural seed")
        self.parameters = parameters or ModelParameters()
        p = self.parameters
        p.validate(dt)
        input_ids, record_ids = tuple(input_ids), tuple(record_ids)
        self.lookup = {value: i for i, value in enumerate(graph.ids)}
        for ids in (input_ids, record_ids):
            if len(set(ids)) != len(ids) or not set(ids) <= self.lookup.keys():
                raise ValueError("Invalid runtime neuron IDs")
        if len(record_ids) > 16:
            raise ValueError("Record at most 16 neurons")
        if initial_v is not None:
            initial_v = np.asarray(initial_v, dtype=float)
            if initial_v.shape != (len(graph.ids),) or not np.all(np.isfinite(initial_v)):
                raise ValueError("Invalid initial membrane state")
        self.dt, self.input_ids = dt, tuple(input_ids)
        self.steps = 0
        b2.start_scope()
        b2.prefs.codegen.target = "numpy"
        b2.seed(seed)
        clock = b2.Clock(dt=dt * b2.second)
        constants = {
            "v_rest": p.rest_v * b2.volt, "v_reset": p.reset_v * b2.volt,
            "v_threshold": p.threshold_v * b2.volt,
            "tau_m": p.membrane_tau_s * b2.second,
            "tau_g": p.synapse_tau_s * b2.second,
            "t_ref": p.refractory_s * b2.second, "kick": p.input_kick_v * b2.volt,
        }
        self.neurons = neurons = b2.NeuronGroup(len(graph.ids),
            """dv/dt = (v_rest - v + g) / tau_m : volt (unless refractory)
               dg/dt = -g / tau_g : volt (unless refractory)
               disabled : boolean""",
            threshold="v > v_threshold and not disabled", reset="v = v_reset; g = 0*volt",
            refractory="t_ref", method="exact", clock=clock, namespace=constants)
        neurons.v = (initial_v if initial_v is not None else p.rest_v) * b2.volt
        self.weights = graph.counts * polarity(graph.nt)[graph.pre] * p.weight_per_contact_v
        self.synapses = synapses = b2.Synapses(neurons, neurons, model="w : volt",
            on_pre="g_post += w * int(not disabled_pre) * int(not disabled_post)",
            delay=p.delay_s * b2.second, clock=clock)
        if len(graph.pre):
            synapses.connect(i=graph.pre, j=graph.post)
            synapses.w = self.weights * b2.volt
        else:
            synapses.active = False
        self.spikes = b2.SpikeMonitor(neurons)
        objects = [neurons, synapses, self.spikes]
        self.traces = None
        if record_ids:
            self.traces = b2.StateMonitor(neurons, ["v", "g"], record=[self.lookup[i] for i in record_ids],
                                          clock=clock, when="end")
            objects.append(self.traces)
        self.sources = self.input_spikes = None
        if input_ids:
            self.sources = b2.PoissonGroup(len(input_ids), rates=np.zeros(len(input_ids)) * b2.Hz, clock=clock)
            inputs = b2.Synapses(self.sources, neurons, on_pre="v_post += kick * int(not disabled_post)",
                                clock=clock, namespace=constants)
            inputs.connect(i=np.arange(len(input_ids)), j=[self.lookup[i] for i in input_ids])
            self.input_spikes = b2.SpikeMonitor(self.sources)
            objects.extend([self.sources, inputs, self.input_spikes])
        self.network = b2.Network(*objects)

    def advance(self, duration_s, rates_hz, silenced=()):
        if type(duration_s) not in (int, float) or not math.isfinite(duration_s) or duration_s <= 0:
            raise ValueError("Invalid neural segment duration")
        if Decimal(str(duration_s)) % Decimal(str(self.dt)):
            raise ValueError("Neural segment must align to timestep")
        rates = np.asarray(rates_hz, dtype=float)
        if rates.shape != (len(self.input_ids),) or not np.all(np.isfinite(rates)) or np.any(rates < 0):
            raise ValueError("Invalid neural rates")
        if np.any(rates * self.dt > 0.1):
            raise ValueError("PoissonGroup rate*dt must be <= 0.1")
        if not set(silenced) <= self.lookup.keys():
            raise ValueError("Unknown silenced neuron")
        before = np.asarray(self.spikes.count).copy()
        self.neurons.disabled = False
        if silenced:
            indices = [self.lookup[i] for i in silenced]
            self.neurons.disabled[indices] = True
            self.neurons.v[indices] = self.parameters.rest_v * b2.volt
            self.neurons.g[indices] = 0 * b2.volt
        if self.sources is not None:
            self.sources.rates = rates * b2.Hz
        self.network.run(duration_s * b2.second, namespace={})
        self.steps += round(duration_s / self.dt)
        if not (np.all(np.isfinite(self.neurons.v[:])) and np.all(np.isfinite(self.neurons.g[:]))):
            raise RuntimeError("Nonfinite neural state")
        return np.asarray(self.spikes.count).copy() - before


def simulate(graph, config, parameters=None, record_ids=(), initial_v=None):
    validate_config(config)
    graph.validate()
    parameters = parameters or ModelParameters()
    dt = config["brain_dt_s"]
    parameters.validate(dt)
    lookup = {value: i for i, value in enumerate(graph.ids)}
    if len(record_ids) > 16 or len(set(record_ids)) != len(record_ids) or not set(record_ids) <= lookup.keys():
        raise ValueError("Record at most 16 unique neurons present in graph")
    if config["graph_center_id"] not in lookup:
        raise ValueError("Experiment center is not present in graph")
    for item in config["interventions"]:
        if not set(item["neuron_ids"]) <= lookup.keys():
            raise ValueError("Unknown intervention neuron")
    stimulated = sorted({neuron for item in config["interventions"] if item["kind"] == "stimulate"
                         for neuron in item["neuron_ids"]})
    steps = round(config["duration_s"] / dt)
    intervention_steps = [(item, round(item["start_s"] / dt), round(item["end_s"] / dt))
                          for item in config["interventions"]]
    boundaries = sorted({0, steps, *(s for _, s, _ in intervention_steps), *(e for _, _, e in intervention_steps)})
    segments = []
    for start, end in zip(boundaries, boundaries[1:]):
        silenced = set()
        rates = dict.fromkeys(stimulated, 0.0)
        for item, begin, finish in intervention_steps:
            if begin <= start < finish:
                if item["kind"] == "silence":
                    silenced.update(item["neuron_ids"])
                else:
                    for neuron in item["neuron_ids"]:
                        rates[neuron] += item["rate_hz"]
        if any(rate * dt > 0.1 for rate in rates.values()):
            raise ValueError("PoissonGroup rate*dt must be <= 0.1; reduce dt or rate")
        segments.append((start, end, silenced, [rates[i] for i in stimulated]))
    if initial_v is not None:
        initial_v = np.asarray(initial_v, dtype=float)
        if initial_v.shape != (len(graph.ids),) or not np.all(np.isfinite(initial_v)):
            raise ValueError("Invalid initial membrane state")
    duration = config["duration_s"]
    estimated_bytes = (len(graph.pre) * 120 + len(graph.ids) * 300 +
                       len(record_ids) * steps * 24 +
                       len(graph.ids) * (duration / parameters.refractory_s + 1) * 24)
    memory = psutil.virtual_memory()
    available = memory.available
    if estimated_bytes > min(6 * 1024**3, available * 0.6):
        raise ValueError("Run exceeds conservative memory budget; reduce duration or graph size")

    started = time.perf_counter()
    runtime = NeuralRuntime(graph, dt, config["seed"], stimulated, parameters, record_ids, initial_v)
    neurons, spikes, traces = runtime.neurons, runtime.spikes, runtime.traces
    input_spikes, weights = runtime.input_spikes, runtime.weights
    built_s = time.perf_counter() - started
    process = psutil.Process()
    sampled_peak = process.memory_info().rss
    run_started = time.perf_counter()
    for start, end, silenced, rates in segments:
        runtime.advance(float(Decimal(end - start) * Decimal(str(dt))), rates, silenced)
        sampled_peak = max(sampled_peak, process.memory_info().rss)
    run_s = time.perf_counter() - run_started
    final_v = np.asarray(neurons.v / b2.volt)
    final_g = np.asarray(neurons.g / b2.volt)
    data = {
        "neuron_ids": graph.ids, "spike_indices": np.asarray(spikes.i, dtype=np.int32),
        "spike_times_s": np.asarray(spikes.t / b2.second), "spike_counts": np.asarray(spikes.count),
        "record_ids": np.array(record_ids, dtype="U20"),
        "trace_time_s": np.asarray(traces.t / b2.second) if traces is not None else np.array([]),
        "voltage_v": np.asarray(traces.v / b2.volt) if traces is not None else np.empty((0, 0)),
        "synaptic_drive_v": np.asarray(traces.g / b2.volt) if traces is not None else np.empty((0, 0)),
        "input_ids": np.array(stimulated, dtype="U20"),
        "input_indices": np.asarray(input_spikes.i, dtype=np.int32) if input_spikes is not None else np.array([], dtype=np.int32),
        "input_times_s": np.asarray(input_spikes.t / b2.second) if input_spikes is not None else np.array([]),
        "final_voltage_v": final_v, "final_synaptic_drive_v": final_g,
    }
    # A Brian2 VariableView may not own the monitor's backing memory.
    data = {key: value.copy() for key, value in data.items()}
    finite = all(np.all(np.isfinite(data[k])) for k in ("voltage_v", "synaptic_drive_v", "final_voltage_v", "final_synaptic_drive_v"))
    if not finite:
        raise RuntimeError("Nonfinite neural state")
    report = {
        "status": "simulated", "model": "flylab-lif-v1", "biologically_validated": False,
        "body_connected": False, "parameters_si": asdict(parameters), "nt_sign_assumptions": NT_SIGN,
        "other_nt_policy": "zero effective weight; connections retained; no neuromodulation",
        "silence_policy": "clamp v/g on segment start, block spikes and incoming/outgoing delivery during [start,end)",
        "input_policy": "Brian2 PoissonGroup voltage kicks; at most one event per input per dt; refractory retained",
        "integration": "exact linear subthreshold; v and g frozen during refractory; reset v and g",
        "source": graph.provenance, "config": config, "config_sha256": digest(config),
        "parameters_sha256": digest(asdict(parameters)), "neurons": len(graph.ids),
        "ordered_pairs": len(graph.pre), "positive_pairs": int(np.sum(weights > 0)),
        "negative_pairs": int(np.sum(weights < 0)), "zero_weight_pairs": int(np.sum(weights == 0)),
        "spikes": len(data["spike_times_s"]), "active_neurons": int(np.count_nonzero(data["spike_counts"])),
        "finite_state": bool(finite), "construction_s": built_s, "run_and_compile_s": run_s,
        "voltage_range_recorded_and_final_v": [
            float(min(final_v.min(), data["voltage_v"].min() if data["voltage_v"].size else final_v.min())),
            float(max(final_v.max(), data["voltage_v"].max() if data["voltage_v"].size else final_v.max()))],
        "total_memory_bytes": memory.total, "available_memory_before_run_bytes": available,
        "wall_s_per_simulated_s": run_s / duration, "rss_sampled_peak_bytes": sampled_peak,
        "process_lifetime_peak_wset_bytes": getattr(process.memory_info(), "peak_wset", None),
        "estimated_memory_budget_bytes": int(estimated_bytes), "initial_state": "rest" if initial_v is None else initial_v.tolist(),
        "versions": {name: importlib.metadata.version(name) for name in ("brian2", "numpy", "scipy", "psutil")},
        "backend": "numpy_cpu",
    }
    return data, report
