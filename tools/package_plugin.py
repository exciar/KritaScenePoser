#!/usr/bin/env python3
"""Build a reproducible Krita plugin ZIP using only the Python standard library.

Past builds are never replaced: the default name comes from the plugin's
``__version__``, an identical rebuild is reported and left alone, and a
different build under an existing name is refused. Bump the version instead.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import sys
import tempfile
from typing import NamedTuple
import zipfile


PLUGIN_NAME = "krita_scene_poser"
FIXED_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
ROOT_MODULES = frozenset({
    "__init__.py", "plugin.py", "compatibility.py", "compat.py",
    "diagnostics.py", "log.py", "version.py", "__version__.py",
})
SOURCE_DIRECTORIES = frozenset({"ui", "core", "render", "integration", "storage"})
ASSET_SUFFIXES = frozenset({
    ".vert", ".frag", ".glsl", ".json", ".png", ".svg", ".mesh", ".rig", ".txt",
})
# Text files are stored with LF line endings whatever the working tree uses,
# so any git checkout rebuilds the same bytes. Binary assets are untouched.
TEXT_SUFFIXES = frozenset({
    ".py", ".json", ".html", ".svg", ".desktop", ".txt", ".vert", ".frag", ".glsl", ".md",
})
TEXT_NAMES = frozenset({"LICENSE"})
EXCLUDED_DIRECTORIES = frozenset({
    "__pycache__", "tests", "test", "tools", "docs", "logs", "dist",
    "build", "venv", "node_modules",
})


class BuildExistsError(ValueError):
    """A different build already has this name; it is never overwritten."""


class Build(NamedTuple):
    members: tuple
    status: str  # "built", or "unchanged" when an identical file already existed


def plugin_version(source_root: Path) -> str:
    source = (source_root / PLUGIN_NAME / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'^__version__\s*=\s*"([^"]+)"', source, re.MULTILINE)
    if not match:
        raise ValueError("The plugin __version__ was not found.")
    return match.group(1)


def default_output(source_root: Path) -> Path:
    return Path("dist") / "ksp-{}.zip".format(plugin_version(source_root))


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


def _archive_bytes(path: Path) -> bytes:
    data = path.read_bytes()
    if path.suffix.lower() in TEXT_SUFFIXES or path.name in TEXT_NAMES:
        data = data.replace(b"\r\n", b"\n")
    return data


def package_plugin(source_root: Path, output: Path) -> Build:
    """Write a deterministic ZIP atomically; never replace an existing file."""
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
                    archive.writestr(info, _archive_bytes(path), compresslevel=9)
        if output.exists():
            if output.read_bytes() == temporary_path.read_bytes():
                return Build(tuple(sorted(members)), "unchanged")
            raise BuildExistsError(
                "{} already exists and differs from this build. Past builds are never "
                "overwritten; bump __version__ in {}/__init__.py.".format(output.name, PLUGIN_NAME))
        temporary_path.rename(output)  # Fails rather than replaces if the name appeared meanwhile.
    finally:
        # Only this build's own temporary file is ever removed.
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()
    return Build(tuple(sorted(members)), "built")


def main(argv: list[str] | None = None) -> int:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="defaults to dist/ksp-<__version__>.zip")
    args = parser.parse_args(argv)
    try:
        output = args.output or default_output(root)
        output = output if output.is_absolute() else root / output
        build = package_plugin(root, output)
    except (OSError, ValueError, zipfile.BadZipFile) as error:
        parser.exit(1, "Packaging failed: " + str(error) + "\n")
    directories = sum(name.endswith("/") for name in build.members)
    if build.status == "unchanged":
        print("Already built: {} is identical; nothing was written.".format(output))
    else:
        print("Built {} ({} files, {} directories)".format(
            output, len(build.members) - directories, directories))
    return 0


if __name__ == "__main__":
    sys.exit(main())
