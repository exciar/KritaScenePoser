"""Environment report for bug reports, built only on request. It holds versions and GPU
details, never file paths or document names.
"""

import json
import platform

from . import __version__, log

SECTIONS = ("environment", "opengl", "canvas", "document", "probe", "last_error")
DOCUMENT_FIELDS = ("width", "height", "color_model", "color_depth", "color_profile")


def _environment():
    info = {
        "ksp": __version__,
        "python": platform.python_version(),
        "os": platform.platform(),
        "diagnostic_log": "enabled" if log.enabled() else "disabled",
    }
    try:
        from PyQt5.QtCore import (
            PYQT_VERSION_STR, QT_VERSION_STR, QCoreApplication, Qt, qVersion,
        )
        from PyQt5.QtGui import QSurfaceFormat
    except ImportError:
        info["pyqt"] = "unavailable"
    else:
        info.update(pyqt=PYQT_VERSION_STR, qt_compiled=QT_VERSION_STR, qt_runtime=qVersion())
        # Krita sets this attribute when its preferred renderer is ANGLE.
        info["opengl_es_requested"] = QCoreApplication.testAttribute(Qt.AA_UseOpenGLES)
        default = QSurfaceFormat.defaultFormat()
        info["default_surface"] = "{}.{} profile={} renderable={}".format(
            default.majorVersion(), default.minorVersion(),
            int(default.profile()), int(default.renderableType()))
    try:
        from krita import Krita
        info["krita"] = Krita.instance().version()
    except Exception:  # Outside Krita, or a partially initialized host.
        info["krita"] = "unavailable"
    return info


def collect(renderer_details=None, probe=None, error=None, document=None, canvas=None):
    """Return a report dictionary; every argument is optional."""
    return {
        "environment": _environment(),
        "opengl": dict(renderer_details or {}) or {"status": "not initialized"},
        "canvas": dict(canvas or {}) or {"status": "not used"},
        "document": ({field: getattr(document, field) for field in DOCUMENT_FIELDS}
                     if document is not None else {"status": "none checked"}),
        "probe": dict(probe or {}) or {"status": "not run"},
        "last_error": {"message": str(error) if error else "none"},
    }


def format_report(data):
    lines = ["KSP diagnostics"]
    for section in SECTIONS:
        lines.append("[{}]".format(section))
        for key, value in sorted(data.get(section, {}).items()):
            if isinstance(value, (dict, list, tuple)):
                value = json.dumps(value, sort_keys=True)
            lines.append("{}: {}".format(key, value))
    return "\n".join(lines) + "\n"
