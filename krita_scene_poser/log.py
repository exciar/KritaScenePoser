"""Structured diagnostic log; disabled by default.

When enabled, events are appended as JSON lines to a small rotating file that
the caller places in Krita's application-data folder. Nothing is sent
anywhere. Events carry no file paths, document names, or pixel data, and
exceptions are recorded as type and message only, because tracebacks contain
local paths.
"""

import json
import logging
import logging.handlers
import os
import time

LOGGER = logging.getLogger("krita_scene_poser")
LOGGER.addHandler(logging.NullHandler())
LOGGER.propagate = False  # Never write into Krita's or another plugin's handlers.
DISABLED = logging.CRITICAL + 10
LOGGER.setLevel(DISABLED)

FILENAME = "ksp.log"
MAX_BYTES = 512 * 1024
BACKUP_COUNT = 2


class JsonLineFormatter(logging.Formatter):
    def format(self, record):
        entry = {
            "time": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created))
                    + ".{:03d}Z".format(int(record.msecs)),
            "level": record.levelname.lower(),
            "event": record.getMessage(),
        }
        entry.update(getattr(record, "fields", {}))
        if record.exc_info and record.exc_info[1] is not None:
            error = record.exc_info[1]
            entry["error"] = "{}: {}".format(type(error).__name__, error)
        return json.dumps(entry, sort_keys=True, default=str)


def enabled():
    return LOGGER.level != DISABLED


def configure(directory=None):
    """Log to ``directory``, or disable logging when it is None.

    Returns the log file path, or None when disabled.
    """
    for handler in list(LOGGER.handlers):
        if isinstance(handler, logging.FileHandler):
            LOGGER.removeHandler(handler)
            handler.close()
    if directory is None:
        LOGGER.setLevel(DISABLED)
        return None
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, FILENAME)
    handler = logging.handlers.RotatingFileHandler(
        path, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8", delay=True)
    handler.setFormatter(JsonLineFormatter())
    LOGGER.addHandler(handler)
    LOGGER.setLevel(logging.INFO)
    return path


def event(name, level=logging.INFO, error=None, **fields):
    """Record one event; cheap and silent while logging is disabled."""
    if LOGGER.isEnabledFor(level):
        exc_info = (type(error), error, None) if error is not None else None
        LOGGER.log(level, name, exc_info=exc_info, extra={"fields": fields})
