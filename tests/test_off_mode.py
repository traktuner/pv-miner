import tempfile
import sys
import types
import unittest
from pathlib import Path

try:
    import flask  # noqa: F401
except ModuleNotFoundError:
    flask_stub = types.ModuleType("flask")
    flask_stub.Flask = object
    flask_stub.Response = object
    flask_stub.jsonify = lambda *args, **kwargs: None
    flask_stub.request = object()
    sys.modules["flask"] = flask_stub

try:
    import requests  # noqa: F401
except ModuleNotFoundError:
    sys.modules["requests"] = types.ModuleType("requests")

from pv_miner import ConfigManager, PowerController, StateStore


class FakeFronius:
    def __init__(self, powerflow):
        self.powerflow = powerflow

    def get_powerflow(self):
        return self.powerflow


class ForbiddenBraiins:
    def __getattr__(self, name):
        raise AssertionError(f"Braiins API method must not be accessed in Off mode: {name}")


class OffModeTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.config = ConfigManager(str(Path(self.tempdir.name) / "config.json"))
        self.config.update({
            "fronius": {"host": "fronius.test"},
            "miner": {"host": "miner.test"},
            "modes": {"manual_override": "off"},
        })

    def tearDown(self):
        self.tempdir.cleanup()

    def test_off_mode_skips_braiins_and_keeps_fronius_live_data(self):
        state = StateStore()
        controller = PowerController(
            self.config,
            FakeFronius({
                "soc": 63.5,
                "p_grid": -120.0,
                "p_pv": 5400.0,
                "p_akku": -1800.0,
                "p_load": -3480.0,
            }),
            ForbiddenBraiins(),
            state,
        )

        controller.run_cycle()

        snapshot = state.snapshot()
        self.assertEqual(snapshot["manual_override"], "off")
        self.assertEqual(snapshot["display_state"], "off")
        self.assertEqual(snapshot["soc"], 63.5)
        self.assertEqual(snapshot["p_pv"], 5400.0)
        self.assertIsNone(snapshot["miner_power_w"])
        self.assertIsNone(snapshot["house_without_miner_w"])
        self.assertIsNone(snapshot["available_w"])

    def test_off_mode_skips_braiins_when_fronius_is_unavailable(self):
        state = StateStore()
        controller = PowerController(
            self.config,
            FakeFronius(None),
            ForbiddenBraiins(),
            state,
        )

        controller.run_cycle()

        snapshot = state.snapshot()
        self.assertEqual(snapshot["manual_override"], "off")
        self.assertEqual(snapshot["display_state"], "off")
        self.assertIsNone(snapshot["p_pv"])
        self.assertIsNone(snapshot["miner_power_w"])

    def test_off_mode_can_be_queued_and_persisted(self):
        self.config.update({"modes": {"manual_override": "auto"}})

        current, apply_at = self.config.queue_run_mode("off", delay_s=0)

        self.assertEqual(current, "auto")
        self.assertIsNotNone(apply_at)
        self.assertTrue(self.config.apply_pending_run_mode())
        self.assertEqual(self.config.get()["modes"]["manual_override"], "off")


if __name__ == "__main__":
    unittest.main()
