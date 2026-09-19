"""Release boundary tests that do not need Krita or PyQt."""

from configparser import ConfigParser
import os
from pathlib import Path
import tempfile
import unittest
import zipfile

from tools.package_plugin import collect_files, package_plugin

REPOSITORY = Path(__file__).resolve().parents[1]


def parent_directories(names):
    """Immediate parents; checking directory entries too covers every level."""
    stripped = (name.rstrip("/") for name in names)
    return {name.rsplit("/", 1)[0] + "/" for name in stripped if "/" in name}


class PackagePluginTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        for name in (
            "krita_scene_poser.desktop", "LICENSE",
            "krita_scene_poser/__init__.py", "krita_scene_poser/plugin.py",
            "krita_scene_poser/manual.html", "krita_scene_poser/render/triangle.py",
            "krita_scene_poser/assets/shaders/triangle.vert",
        ):
            self.write(name, "fixture: " + name)

    def write(self, name, content):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def test_archive_is_byte_identical_despite_source_mtime_changes(self):
        first, second = self.root / "first.zip", self.root / "second.zip"
        package_plugin(self.root, first)
        for path in collect_files(self.root).values():
            os.utime(path, (1700000000, 1700000000))
        package_plugin(self.root, second)
        self.assertEqual(first.read_bytes(), second.read_bytes())

    def test_archive_has_installable_layout_license_and_fixed_metadata(self):
        output = self.root / "release.zip"
        package_plugin(self.root, output)
        with zipfile.ZipFile(output) as archive:
            names = archive.namelist()
            self.assertEqual(names, sorted(names))
            self.assertEqual(
                {name.split("/")[0] for name in names},
                {"krita_scene_poser.desktop", "krita_scene_poser"},
            )
            self.assertEqual(archive.read("krita_scene_poser/LICENSE"), (self.root / "LICENSE").read_bytes())
            self.assertIsNone(archive.testzip())
            for info in archive.infolist():
                self.assertEqual(info.date_time, (1980, 1, 1, 0, 0, 0))
                self.assertEqual(info.external_attr >> 16,
                                 0o40755 if info.is_dir() else 0o100644)
                self.assertNotIn("\\", info.filename)

    def test_every_member_directory_has_an_explicit_entry(self):
        output = self.root / "release.zip"
        package_plugin(self.root, output)
        with zipfile.ZipFile(output) as archive:
            names = archive.namelist()
            for directory in parent_directories(names):
                self.assertIn(directory, names)
                self.assertEqual(archive.getinfo(directory).file_size, 0)

    def test_repository_package_satisfies_krita_importer_contract(self):
        # Krita's plugin_importer.py reads X-KDE-Library and Name from each
        # .desktop file, then requires a "<library>/" directory entry that
        # contains "__init__.py"; otherwise "No plugins found in archive".
        output = self.root / "repository.zip"
        package_plugin(REPOSITORY, output)
        with zipfile.ZipFile(output) as archive:
            names = archive.namelist()
            desktops = [name for name in names if name.endswith(".desktop")]
            self.assertEqual(desktops, ["krita_scene_poser.desktop"])
            config = ConfigParser()
            config.read_string(archive.read(desktops[0]).decode("utf-8"))
            entry = config["Desktop Entry"]
            self.assertEqual(entry["ServiceTypes"], "Krita/PythonPlugin")
            self.assertTrue(entry["Name"])
            library = entry["X-KDE-Library"]
            self.assertIn(library + "/", names)
            self.assertIn(library + "/__init__.py", names)

    def test_runtime_allowlist_excludes_caches_secrets_and_development_files(self):
        excluded = (
            "README.md", ".env", "tests/test_runtime.py",
            "krita_scene_poser/debug.py", "krita_scene_poser/__pycache__/plugin.pyc",
            "krita_scene_poser/render/test_triangle.py", "krita_scene_poser/render/triangle_test.py",
            "krita_scene_poser/render/.private/key.py", "krita_scene_poser/render/tests/helper.py",
            "krita_scene_poser/assets/.secrets.json", "krita_scene_poser/assets/session.log",
            "krita_scene_poser/assets/helper.exe", "krita_scene_poser/assets/cache.zip",
        )
        for name in excluded:
            self.write(name, "must not ship")
        output = self.root / "release.zip"
        package_plugin(self.root, output)
        with zipfile.ZipFile(output) as archive:
            for name in excluded:
                self.assertNotIn(name, archive.namelist())
            self.assertIn("krita_scene_poser/render/triangle.py", archive.namelist())
            self.assertIn("krita_scene_poser/assets/shaders/triangle.vert", archive.namelist())

    def test_missing_required_file_fails_without_replacing_existing_output(self):
        output = self.write("release.zip", "previous release")
        (self.root / "LICENSE").unlink()
        with self.assertRaisesRegex(ValueError, "Required packaging file"):
            package_plugin(self.root, output)
        self.assertEqual(output.read_text(encoding="utf-8"), "previous release")

    def test_refuses_linked_runtime_source(self):
        target = self.write("private.py", "private material")
        link = self.root / "krita_scene_poser/render/linked.py"
        try:
            link.symlink_to(target)
        except (OSError, NotImplementedError):
            self.skipTest("Creating symlinks requires privileges on this host")
        with self.assertRaisesRegex(ValueError, "symlinks or junctions"):
            package_plugin(self.root, self.root / "release.zip")

    def test_refuses_non_zip_or_runtime_output(self):
        for output in (self.root / "LICENSE", self.root / "krita_scene_poser/release.zip"):
            with self.subTest(output=output):
                with self.assertRaises(ValueError):
                    package_plugin(self.root, output)


if __name__ == "__main__":
    unittest.main()
