"""Settings tests with a dictionary standing in for Krita's settings store."""

import unittest

from krita_scene_poser.integration.settings import Settings, load, save, update


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.store = {}

    def read(self, name, default):
        return self.store.get(name, default)

    def write(self, name, value):
        self.store[name] = value

    def test_defaults_when_nothing_is_stored(self):
        self.assertEqual(load(self.read), Settings())
        self.assertFalse(Settings().diagnostic_log)

    def test_round_trip_through_text(self):
        save(Settings(diagnostic_log=True), self.write)
        self.assertEqual(self.store, {"diagnostic_log": "true"})
        self.assertEqual(load(self.read), Settings(diagnostic_log=True))

    def test_invalid_or_unreadable_values_fall_back_to_defaults(self):
        self.store["diagnostic_log"] = "sometimes"
        self.assertEqual(load(self.read), Settings())

        def broken(name, default):
            raise RuntimeError("settings unavailable")
        self.assertEqual(load(broken), Settings())

    def test_update_returns_and_persists_changes(self):
        changed = update(Settings(), self.write, diagnostic_log=True)
        self.assertTrue(changed.diagnostic_log)
        self.assertEqual(self.store["diagnostic_log"], "true")


if __name__ == "__main__":
    unittest.main()
