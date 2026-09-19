"""Diagnostic report tests; these run without Krita or PyQt."""

import os
from pathlib import Path
import sys
import unittest

from krita_scene_poser import diagnostics
from krita_scene_poser.integration.krita_document import DocumentSnapshot


class DiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = DocumentSnapshot(
            257, 193, "RGBA", "U8", "sRGB-elle-V2-srgbtrc.icc", "{root-uuid}")
        self.data = diagnostics.collect(
            renderer_details={"renderer": "Test GPU", "core_profile": False},
            probe={"width": 257, "samples": {"top_red": {"rgba": [220, 20, 15, 191]}}},
            error="Probe failed",
            document=self.snapshot,
        )

    def test_report_is_deterministic_and_sectioned(self):
        report = diagnostics.format_report(self.data)
        self.assertEqual(report, diagnostics.format_report(self.data))
        headers = [line for line in report.splitlines() if line.startswith("[")]
        self.assertEqual(headers, ["[{}]".format(name) for name in diagnostics.SECTIONS])
        self.assertIn("renderer: Test GPU", report)
        self.assertIn('samples: {"top_red": {"rgba": [220, 20, 15, 191]}}', report)
        self.assertIn("message: Probe failed", report)

    def test_document_section_omits_identity(self):
        self.assertEqual(self.data["document"]["width"], 257)
        self.assertNotIn("root_id", self.data["document"])
        self.assertNotIn("{root-uuid}", diagnostics.format_report(self.data))

    def test_empty_collection_marks_missing_state(self):
        data = diagnostics.collect()
        self.assertEqual(data["opengl"], {"status": "not initialized"})
        self.assertEqual(data["probe"], {"status": "not run"})
        self.assertEqual(data["last_error"], {"message": "none"})
        if "krita" not in sys.modules:
            self.assertEqual(data["environment"]["krita"], "unavailable")

    def test_report_contains_no_local_paths(self):
        report = diagnostics.format_report(diagnostics.collect())
        for path in {str(Path.home()), os.path.dirname(sys.executable), os.getcwd()}:
            self.assertNotIn(path, report)


if __name__ == "__main__":
    unittest.main()
