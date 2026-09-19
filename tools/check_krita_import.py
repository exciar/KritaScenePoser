#!/usr/bin/env python3
"""Run a Krita installation's own plugin importer against a release ZIP.

Uses Krita's bundled ``plugin_importer.py`` from outside the GUI and extracts
into a temporary resources folder, never Krita's real one. It needs only the
standard library; Krita's importer requires no Qt.

    py -3 tools/check_krita_import.py dist/ksp-0.0.1-phase0.zip --krita "X:/Program Files/Krita (x64)"
"""

import argparse
import builtins
import importlib.util
import os
from pathlib import Path
import sys
import tempfile

IMPORTER = Path("share", "krita", "pykrita", "plugin_importer", "plugin_importer.py")


def check(archive, krita_root):
    """Return the resources-relative paths Krita would install."""
    source = Path(krita_root) / IMPORTER
    if not source.is_file():
        raise FileNotFoundError("Krita's plugin importer is not at " + str(source))
    builtins.i18n = getattr(builtins, "i18n", lambda text: text)
    spec = importlib.util.spec_from_file_location("krita_plugin_importer", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with tempfile.TemporaryDirectory() as resources:
        importer = module.PluginImporter(str(archive), resources, lambda plugin: True)
        try:
            imported = importer.import_all()
        finally:
            importer.archive.close()
        installed = sorted(
            Path(root, name).relative_to(resources).as_posix()
            for root, _, names in os.walk(resources) for name in names)
    return [plugin["name"] for plugin in imported], installed


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("archive", type=Path)
    parser.add_argument("--krita", required=True, help="Krita installation root")
    args = parser.parse_args(argv)
    try:
        plugins, installed = check(args.archive, args.krita)
    except Exception as error:
        parser.exit(1, "Krita import failed: {}: {}\n".format(type(error).__name__, error))
    print("Imported plugins: " + ", ".join(plugins))
    for path in installed:
        print("  " + path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
