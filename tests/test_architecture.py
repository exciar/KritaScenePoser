"""Architecture rules from the plan section 6, checked statically."""

import ast
from pathlib import Path
import unittest

PACKAGE = Path(__file__).resolve().parents[1] / "krita_scene_poser"
HOST_MODULES = {"krita", "PyKrita", "PyQt5", "PyQt6", "sip"}
KSP_HOST_PACKAGES = {"ui", "render", "integration"}
# Calls that change a Krita document; only integration/ may make them.
DOCUMENT_MUTATIONS = {
    "createNode", "setPixelData", "addChildNode", "removeChildNode", "setChildNodes",
    "setColorSpace", "setColorProfile", "resizeImage", "scaleImage", "crop",
    "rotateImage", "shearImage", "flatten", "mergeDown", "setOpacity", "setName",
}


def modules(*packages):
    for package in packages:
        yield from sorted((PACKAGE / package).rglob("*.py"))


def imported_names(path):
    relative_parts = path.relative_to(PACKAGE.parent).with_suffix("").parts
    tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = list(relative_parts[:-node.level])
                yield ".".join(base + ([node.module] if node.module else []))
            else:
                yield node.module


class ArchitectureTests(unittest.TestCase):
    def test_core_and_storage_never_import_krita_qt_or_host_packages(self):
        for path in modules("core", "storage"):
            for name in imported_names(path):
                with self.subTest(module=path.name, imports=name):
                    parts = name.split(".")
                    self.assertNotIn(parts[0], HOST_MODULES)
                    if parts[0] == "krita_scene_poser" and len(parts) > 1:
                        self.assertNotIn(parts[1], KSP_HOST_PACKAGES)

    def test_only_integration_mutates_documents(self):
        for path in sorted(PACKAGE.rglob("*.py")):
            if path.relative_to(PACKAGE).parts[0] == "integration":
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
            calls = {node.func.attr for node in ast.walk(tree)
                     if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)}
            with self.subTest(module=str(path.relative_to(PACKAGE))):
                self.assertEqual(calls & DOCUMENT_MUTATIONS, set())

    def test_rules_see_the_packages_they_guard(self):
        # Guard against the rules passing vacuously after a restructure.
        for package in ("core", "storage", "integration", "ui", "render"):
            self.assertTrue((PACKAGE / package / "__init__.py").is_file(), package)
        integration_calls = set()
        for path in modules("integration"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            integration_calls |= {node.func.attr for node in ast.walk(tree)
                                  if isinstance(node, ast.Call)
                                  and isinstance(node.func, ast.Attribute)}
        self.assertIn("setPixelData", integration_calls)


if __name__ == "__main__":
    unittest.main()
