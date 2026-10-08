import copy
import json
import unittest

import numpy as np

from flylab import ROOT
from sensory_model import EncoderParameters, SensoryEncoder, replay_relays
from sensory_experiment import validate_settings


def eyes(left=0.8, right=0.6):
    values = np.zeros((2, 4, 2))
    values[0, :2, 0], values[0, 2:, 1] = left, left
    values[1, :2, 0], values[1, 2:, 1] = right, right
    return values


class EncoderTests(unittest.TestCase):
    def setUp(self):
        self.encoder = SensoryEncoder(["joint_a", "joint_b"], ["lf", "lm", "lh", "rf", "rm", "rh"], [0.1, -0.2])

    def encode(self, t=0, retina=None, contacts=None, angles=None, speed=None):
        return self.encoder.encode(t, eyes() if retina is None else retina,
                                   [0] * 6 if contacts is None else contacts,
                                   [0.1, -0.2] if angles is None else angles,
                                   [0, 0] if speed is None else speed)

    def test_sparse_retinal_channels_and_lateral_separation(self):
        rate = self.encode()
        np.testing.assert_allclose(rate[:8], [160, 120, 40, 80, 0, 0, 0, 0])
        self.assertEqual(len(rate), 22)
        self.assertEqual(len(rate), len(self.encoder.channel_names))
        changed = self.encode(0.01, eyes(left=0.3))
        self.assertGreater(changed[2], rate[2])
        self.assertEqual(changed[3], rate[3])
        self.assertGreater(changed[6], 0)
        self.assertEqual(changed[7], 0)
        self.assertIsNone(self.encoder.manifest()["anatomical_mapping"])
        self.assertEqual(self.encoder.manifest()["flywire_root_ids"], [])

    def test_causal_temporal_channels_and_reset(self):
        self.encode()
        rate = self.encode(0.1, eyes(0.9, 0.5))
        np.testing.assert_allclose(rate[4:8], [20, 0, 0, 20], atol=1e-10)
        self.encoder.reset()
        restarted = self.encode(0, eyes(0.9, 0.5))
        np.testing.assert_array_equal(restarted[4:8], 0)
        with self.assertRaisesRegex(ValueError, "increasing"):
            self.encode(0)

    def test_contact_proprioception_sign_scale_and_saturation(self):
        rate = self.encode(contacts=[1, 0, 1, 0, 1, 0], angles=[0.35, -1.2], speed=[25, -100])
        np.testing.assert_array_equal(rate[8:14], [200, 0, 200, 0, 200, 0])
        np.testing.assert_allclose(rate[14:], [100, 0, 0, 200, 100, 0, 0, 200])
        self.assertTrue(np.all((rate >= 0) & (rate <= 200)))

    def test_reject_invalid_observations_without_advancing_encoder(self):
        bad_eyes = eyes()
        bad_eyes[0, 0, 1] = 0.2
        for kwargs in ({"retina": bad_eyes}, {"retina": np.zeros((2, 3))},
                       {"retina": eyes(-0.1)}, {"retina": eyes(float("nan"))},
                       {"contacts": [2] * 6}, {"contacts": [False]},
                       {"angles": [1]}, {"speed": [float("inf"), 0]}, {"t": -1}):
            with self.subTest(kwargs=list(kwargs)):
                with self.assertRaises(ValueError):
                    self.encode(**kwargs)
                self.assertIsNone(self.encoder.previous_time)
        self.encode()

    def test_reference_pose_is_copied_and_parameters_validated(self):
        reference = np.array([0.1])
        encoder = SensoryEncoder(["joint"], list("abcdef"), reference)
        reference[:] = 9
        self.assertEqual(encoder.reference_rad[0], 0.1)
        for parameters in (EncoderParameters(maximum_rate_hz=-1), EncoderParameters(angle_scale_rad=float("nan")), EncoderParameters(velocity_scale_rad_s=True)):
            with self.assertRaises(ValueError):
                SensoryEncoder(["joint"], list("abcdef"), [0], parameters)
        with self.assertRaises(ValueError):
            SensoryEncoder(["joint", "joint"], list("abcdef"), [0, 0])


class RelayTests(unittest.TestCase):
    def test_sample_hold_windows_and_repeat(self):
        times = np.arange(11) * 0.01
        rates = np.zeros((11, 2))
        rates[2:4, 0] = 700
        rates[6:8, 1] = 700
        first = replay_relays(times, rates, ["left", "right"])
        second = replay_relays(times, rates, ["left", "right"])
        for key in first:
            np.testing.assert_array_equal(first[key], second[key])
        for channel, start, stop in ((0, 0.02, 0.04), (1, 0.06, 0.08)):
            events = first["input_times_s"][first["input_indices"] == channel]
            self.assertGreater(len(events), 0)
            self.assertTrue(np.all((events >= start) & (events < stop)))
            spikes = first["spike_times_s"][first["spike_indices"] == channel]
            self.assertGreater(len(spikes), 0)
            self.assertTrue(np.all(np.diff(spikes) >= 0.0022 - 1e-10))

    def test_endpoint_is_not_an_extra_input_interval(self):
        rates = np.zeros((11, 1))
        rates[-1] = 1000
        result = replay_relays(np.arange(11) * 0.01, rates, ["end"])
        self.assertEqual(len(result["input_times_s"]), 0)
        self.assertEqual(len(result["spike_times_s"]), 0)

    def test_reject_bad_replay_clocks_rates_and_metadata(self):
        cases = [([0, 0.01, 0.025], np.zeros((3, 1)), ["a"], {}),
                 ([0, 0.01005], np.zeros((2, 1)), ["a"], {}),
                 ([0, 0.01], np.full((2, 1), 2000), ["a"], {}),
                 ([0, 0.01], np.full((2, 1), -1), ["a"], {}),
                 ([0, 0.01], np.zeros((2, 2)), ["a", "a"], {}),
                 ([0, 0.01], np.zeros((2, 1)), ["a"], {"seed": True}),
                 ([0, 0.01], np.zeros((2, 1)), ["a"], {"brain_dt_s": 0})]
        for times, rates, names, kwargs in cases:
            with self.subTest(times=times, kwargs=kwargs):
                with self.assertRaises(ValueError):
                    replay_relays(times, rates, names, **kwargs)


class SensoryConfigurationTests(unittest.TestCase):
    def test_default_and_invalid_configurations(self):
        settings = json.loads((ROOT / "experiments/sensory-baseline.json").read_text())
        validate_settings(settings)
        for key, value in (("seed", True), ("sensory_dt_s", 0.007), ("duration_s", 5),
                           ("warmup_s", 10), ("physics_dt_s", 0.001), ("brain_dt_s", 0.1),
                           ("duration_s", float("nan")), ("schema_version", True),
                           ("brain_dt_s", 0.0005), ("encoder", {}), ("encoder", "invalid")):
            with self.subTest(key=key):
                with self.assertRaises(ValueError):
                    validate_settings({**settings, key: value})
        changed = copy.deepcopy(settings)
        changed["encoder"]["maximum_rate_hz"] = 2000
        with self.assertRaises(ValueError):
            validate_settings(changed)


if __name__ == "__main__":
    unittest.main()
