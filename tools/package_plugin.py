#!/usr/bin/env python3
"""Build a reproducible Krita plugin ZIP using only the Python standard library."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
import tempfile
import zipfile


PLUGIN_NAME = "krita_scene_poser"
DEFAULT_OUTPUT = "dist/ksp-0.0.4.zip"
FIXED_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
ROOT_MODULES = frozenset({
    "__init__.py", "plugin.py", "compatibility.py", "compat.py",
    "diagnostics.py", "log.py", "version.py", "__version__.py",
})
SOURCE_DIRECTORIES = frozenset({"ui", "core", "render", "integration", "storage"})
ASSET_SUFFIXES = frozenset({
    ".vert", ".frag", ".glsl", ".json", ".png", ".svg", ".mesh", ".rig", ".txt",
})
EXCLUDED_DIRECTORIES = frozenset({
    "__pycache__", "tests", "test", "tools", "docs", "logs", "dist",
    "build", "venv", "node_modules",
})


def _is_runtime_file(relative: Path) -> bool:
    """Allow runtime modules and known asset formats; reject developer files."""
    if any(part.startswith(".") or part in EXCLUDED_DIRECTORIES for part in relative.parts):
        return False
    if len(relative.parts) == 1:
        return relative.name in ROOT_MODULES or relative.name == "manual.html"
    if relative.parts[0] in SOURCE_DIRECTORIES:
        return (
            relative.suffix == ".py"
            and not relative.name.startswith("test_")
            and not relative.name.endswith("_test.py")
        )
    return relative.parts[0] == "assets" and relative.suffix.lower() in ASSET_SUFFIXES


def collect_files(source_root: Path) -> dict[str, Path]:
    """Return archive names and their local source files in sorted order."""
    root = source_root.resolve()
    plugin = root / PLUGIN_NAME
    required = [
        root / (PLUGIN_NAME + ".desktop"),
        root / "LICENSE",
        plugin / "__init__.py",
        plugin / "plugin.py",
        plugin / "manual.html",
    ]
    for path in required:
        if not path.is_file():
            raise ValueError("Required packaging file is missing: " + str(path))

    files = {
        PLUGIN_NAME + ".desktop": required[0],
        PLUGIN_NAME + "/LICENSE": required[1],
    }
    for path in plugin.rglob("*"):
        relative = path.relative_to(plugin)
        if path.is_file() and _is_runtime_file(relative):
            files[PLUGIN_NAME + "/" + relative.as_posix()] = path

    # A linked file or directory could leak a file outside the reviewed source tree.
    for path in files.values():
        if not path.resolve().is_relative_to(root):
            raise ValueError("Packaging source escapes the project: " + str(path))
        for component in (path, *path.parents):
            if component == root:
                break
            if component.is_symlink() or (
                hasattr(component, "is_junction") and component.is_junction()
            ):
                raise ValueError("Packaging symlinks or junctions is not supported: " + str(path))
    return dict(sorted(files.items()))


def directory_entries(archive_names) -> set[str]:
    """Every parent directory of the members, spelled "a/", "a/b/".

    Krita's plugin importer only recognizes a plugin when the archive has an
    explicit "<X-KDE-Library>/" entry, and zipfile never adds one implicitly.
    """
    directories = set()
    for name in archive_names:
        parts = name.split("/")[:-1]
        for depth in range(1, len(parts) + 1):
            directories.add("/".join(parts[:depth]) + "/")
    return directories


def package_plugin(source_root: Path, output: Path) -> tuple[str, ...]:
    """Write an atomic deterministic ZIP and return its member names."""
    files = collect_files(source_root)
    members = dict.fromkeys(directory_entries(files))
    members.update(files)
    output = output.resolve()
    if output.suffix.lower() != ".zip":
        raise ValueError("The output filename must end in .zip")
    if output.is_relative_to((source_root / PLUGIN_NAME).resolve()):
        raise ValueError("Write the release ZIP outside the runtime plugin directory")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix=".ksp-package-", suffix=".zip", dir=output.parent, delete=False
        ) as temporary:
            temporary_path = Path(temporary.name)
        with zipfile.ZipFile(temporary_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for archive_name, path in sorted(members.items()):
                info = zipfile.ZipInfo(archive_name, FIXED_TIMESTAMP)
                info.create_system = 3
                if path is None:
                    info.external_attr = (0o40755 << 16) | 0x10  # MS-DOS directory bit
                    info.compress_type = zipfile.ZIP_STORED
                    archive.writestr(info, b"")
                else:
                    info.external_attr = 0o100644 << 16
                    info.compress_type = zipfile.ZIP_DEFLATED
                    archive.writestr(info, path.read_bytes(), compresslevel=9)
        temporary_path.replace(output)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()
    return tuple(sorted(members))


def main(argv: list[str] | None = None) -> int:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(DEFAULT_OUTPUT), help=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    output = args.output if args.output.is_absolute() else root / args.output
    try:
        members = package_plugin(root, output)
    except (OSError, ValueError, zipfile.BadZipFile) as error:
        parser.exit(1, "Packaging failed: " + str(error) + "\n")
    directories = sum(name.endswith("/") for name in members)
    print("Built {} ({} files, {} directories)".format(
        output, len(members) - directories, directories))
    return 0


if __name__ == "__main__":
    sys.exit(main())
