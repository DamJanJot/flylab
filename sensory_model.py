"""Explicit surrogate sensory encoding; no anatomical FlyWire mapping is implied."""

from dataclasses import asdict, dataclass
import math

import numpy as np


@dataclass(frozen=True)
class EncoderParameters:
    maximum_rate_hz: float = 200.0
    angle_scale_rad: float = 0.5
    velocity_scale_rad_s: float = 50.0
    contrast_scale_per_s: float = 10.0

    def validate(self):
        for key, value in asdict(self).items():
            if type(value) not in (float, int) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"{key} must be finite and positive")


class SensoryEncoder:
    """Stateful causal encoder. Call reset before starting another episode."""

    def __init__(self, joint_names, leg_names, reference_rad, parameters=None):
        self.parameters = parameters or EncoderParameters()
        self.parameters.validate()
        self.joint_names = list(joint_names)
        self.leg_names = list(leg_names)
        for names in (self.joint_names, self.leg_names):
            if not names or any(not isinstance(i, str) or not i for i in names) or len(set(names)) != len(names):
                raise ValueError("Channel names must be nonempty unique strings")
        if len(self.leg_names) != 6:
            raise ValueError("Expected six legs")
        self.reference_rad = np.asarray(reference_rad, dtype=float).copy()
        if self.reference_rad.shape != (len(self.joint_names),) or not np.isfinite(self.reference_rad).all():
            raise ValueError("Invalid reference pose")
        self.channel_names = [f"vision.{eye}.{kind}" for kind in ("bright", "dark", "on", "off") for eye in ("left", "right")]
        self.channel_names += [f"contact.{leg}" for leg in self.leg_names]
        self.channel_names += [f"joint.{joint}.{kind}" for kind in ("angle_positive", "angle_negative", "velocity_positive", "velocity_negative") for joint in self.joint_names]
        self.reset()

    def reset(self):
        self.previous_time = None
        self.previous_intensity = None

    def encode(self, time_s, ommatidia, contacts, joint_rad, velocity_rad_s):
        if type(time_s) not in (int, float) or not math.isfinite(time_s) or time_s < 0:
            raise ValueError("Invalid sample time")
        if self.previous_time is not None and time_s <= self.previous_time:
            raise ValueError("Samples must have strictly increasing timestamps")
        eyes = np.asarray(ommatidia, dtype=float)
        if eyes.ndim != 3 or eyes.shape[0] != 2 or eyes.shape[1] < 1 or eyes.shape[2] != 2:
            raise ValueError("Expected left/right ommatidia array (2, N, 2)")
        if not np.isfinite(eyes).all() or np.any(eyes < 0) or np.any(eyes > 1):
            raise ValueError("Eye values must be finite in [0,1]")
        # Exactly one yellow/pale channel is populated per ommatidium by FlyGym.
        if np.any(np.count_nonzero(eyes, axis=2) > 1):
            raise ValueError("Expected at most one active color channel per ommatidium")
        contact = np.asarray(contacts)
        angles, velocity = np.asarray(joint_rad, dtype=float), np.asarray(velocity_rad_s, dtype=float)
        if contact.shape != (6,) or not np.all((contact == 0) | (contact == 1)):
            raise ValueError("Expected six binary contact flags")
        if any(a.shape != self.reference_rad.shape or not np.isfinite(a).all() for a in (angles, velocity)):
            raise ValueError("Invalid joint state")
        intensity = eyes.sum(axis=2).mean(axis=1)
        contrast = np.zeros(2) if self.previous_time is None else (intensity - self.previous_intensity) / (time_s - self.previous_time)
        angle = (angles - self.reference_rad) / self.parameters.angle_scale_rad
        speed = velocity / self.parameters.velocity_scale_rad_s
        raw = np.concatenate([intensity, 1 - intensity,
                              np.maximum(contrast, 0) / self.parameters.contrast_scale_per_s,
                              np.maximum(-contrast, 0) / self.parameters.contrast_scale_per_s,
                              contact.astype(float), np.maximum(angle, 0), np.maximum(-angle, 0),
                              np.maximum(speed, 0), np.maximum(-speed, 0)])
        rates = np.clip(raw, 0, 1) * self.parameters.maximum_rate_hz
        self.previous_time, self.previous_intensity = time_s, intensity.copy()
        return rates

    def manifest(self):
        return {
            "schema_version": 1, "status": "surrogate_encoding_not_anatomical_mapping",
            "flywire_root_ids": [], "anatomical_mapping": None, "olfaction": "not_implemented",
            "channel_names": self.channel_names, "joint_names": self.joint_names,
            "leg_names": self.leg_names, "reference_joint_rad": self.reference_rad.tolist(),
            "parameters": asdict(self.parameters),
            "rules": {
                "vision": "Mean across ommatidia of sum of sparse yellow/pale channels; not calibrated luminance",
                "bright_dark": "intensity and 1-intensity",
                "on_off": "Positive/negative causal intensity derivative; zero for first sample after reset",
                "contact": "Binary ground-contact sensor per leg, not contact force or a receptor model",
                "proprioception": "Signed angle displacement from reference and signed angular velocity, split into positive/negative channels",
                "scaling": "Divide by explicit scales, clip to [0,1], multiply by maximum_rate_hz",
                "timing": "Sample-and-hold from current timestamp until next observation; no future samples",
            },
        }


def replay_relays(time_s, rates_hz, channel_names, seed=42, brain_dt_s=0.0001):
    """Offline Brian2 relay test. These cells are NOT FlyWire neurons."""
    from brain_model import b2, ModelParameters

    times, rates = np.asarray(time_s, dtype=float), np.asarray(rates_hz, dtype=float)
    names = list(channel_names)
    if times.ndim != 1 or len(times) < 2 or not np.isfinite(times).all() or times[0] != 0:
        raise ValueError("Replay requires at least two timestamps starting at zero")
    spacing = np.diff(times)
    if np.any(spacing <= 0) or not np.allclose(spacing, spacing[0], atol=1e-12, rtol=0):
        raise ValueError("Replay samples must use a regular increasing clock")
    if not names or len(set(names)) != len(names) or any(not isinstance(n, str) or not n for n in names):
        raise ValueError("Invalid relay channel names")
    if rates.shape != (len(times), len(names)) or not np.isfinite(rates).all() or np.any(rates < 0):
        raise ValueError("Invalid replay rates")
    if type(seed) is not int or not 0 <= seed < 2**32:
        raise ValueError("Invalid replay seed")
    if type(brain_dt_s) not in (int, float) or not math.isfinite(brain_dt_s) or brain_dt_s <= 0:
        raise ValueError("Invalid brain timestep")
    ticks = np.rint(times / brain_dt_s)
    if not np.allclose(ticks * brain_dt_s, times, atol=1e-12, rtol=0):
        raise ValueError("Sensory clock must align with the brain clock")
    if np.max(rates) * brain_dt_s > 0.1:
        raise ValueError("Poisson rate*dt must be <= 0.1")
    if len(names) > 2048 or times[-1] > 10 or rates.nbytes > 128 * 1024**2 or times[-1] / brain_dt_s > 1_000_000:
        raise ValueError("Replay exceeds diagnostic size limit")
    params = ModelParameters()
    params.validate(brain_dt_s)
    b2.start_scope()
    b2.prefs.codegen.target = "numpy"
    b2.seed(seed)
    clock = b2.Clock(dt=brain_dt_s * b2.second)
    # The endpoint is a recorded observation, not an extra interval of input.
    drive = b2.TimedArray(rates * b2.Hz, dt=spacing[0] * b2.second)
    source = b2.PoissonGroup(len(names), rates="drive(t, i)", namespace={"drive": drive}, clock=clock)
    constants = {"rest": params.rest_v * b2.volt, "reset_v": params.reset_v * b2.volt, "threshold_v": params.threshold_v * b2.volt,
                 "tau": params.membrane_tau_s * b2.second, "kick": params.input_kick_v * b2.volt}
    relays = b2.NeuronGroup(len(names), "dv/dt = (rest-v)/tau : volt (unless refractory)",
                           threshold="v > threshold_v", reset="v=reset_v", method="exact",
                           refractory=params.refractory_s * b2.second, namespace=constants, clock=clock)
    relays.v = params.rest_v * b2.volt
    synapses = b2.Synapses(source, relays, on_pre="v_post += kick", namespace=constants, clock=clock)
    synapses.connect(j="i")
    spikes, inputs = b2.SpikeMonitor(relays), b2.SpikeMonitor(source)
    network = b2.Network(source, relays, synapses, spikes, inputs)
    network.run(times[-1] * b2.second, namespace={})
    return {"channel_names": np.array(names), "input_indices": np.asarray(inputs.i, dtype=np.int32),
            "input_times_s": np.asarray(inputs.t / b2.second),
            "spike_indices": np.asarray(spikes.i, dtype=np.int32), "spike_times_s": np.asarray(spikes.t / b2.second),
            "spike_counts": np.asarray(spikes.count), "final_voltage_v": np.asarray(relays.v / b2.volt)}
