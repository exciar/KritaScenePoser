"""Diagnostic log tests: off by default, JSON lines, no paths or tracebacks."""

import json
import logging
from pathlib import Path
import tempfile
import unittest

from krita_scene_poser import log


class LogTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        # Cleanups run last-in first-out: close the log before deleting its folder.
        self.addCleanup(log.configure, None)
        self.directory = Path(temporary.name) / "logs"

    def lines(self):
        path = self.directory / log.FILENAME
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    def test_disabled_by_default_and_writes_nothing(self):
        log.configure(None)
        self.assertFalse(log.enabled())
        log.event("ignored", value=1)
        self.assertFalse(self.directory.exists())
        self.assertFalse(log.LOGGER.propagate)

    def test_enabled_log_writes_structured_json_lines(self):
        path = log.configure(str(self.directory))
        self.assertEqual(Path(path), self.directory / log.FILENAME)
        self.assertTrue(log.enabled())
        log.event("export_finished", width=257, height=193, seconds=0.25)
        log.event("export_failed", level=logging.WARNING, error=ValueError("bad size"))
        first, second = self.lines()
        self.assertEqual(first["event"], "export_finished")
        self.assertEqual((first["width"], first["height"], first["level"]), (257, 193, "info"))
        self.assertRegex(first["time"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$")
        self.assertEqual(second["error"], "ValueError: bad size")
        self.assertEqual(second["level"], "warning")
        self.assertNotIn("Traceback", (self.directory / log.FILENAME).read_text(encoding="utf-8"))

    def test_disabling_closes_the_file_and_stops_writing(self):
        log.configure(str(self.directory))
        log.event("before")
        self.assertIsNone(log.configure(None))
        log.event("after")
        self.assertEqual([line["event"] for line in self.lines()], ["before"])
        self.assertEqual(
            [handler for handler in log.LOGGER.handlers if isinstance(handler, logging.FileHandler)], [])

    def test_reconfiguring_does_not_duplicate_lines(self):
        log.configure(str(self.directory))
        log.configure(str(self.directory))
        log.event("once")
        self.assertEqual(len(self.lines()), 1)


if __name__ == "__main__":
    unittest.main()
