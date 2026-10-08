"""Configure application events without logging request bodies or database credentials."""

import logging
import time
import traceback
from pathlib import Path


def configure_logging() -> None:
    application = logging.getLogger("app")
    if any(handler.name == "newai-events" for handler in application.handlers):
        return
    handler = logging.StreamHandler()
    handler.set_name("newai-events")
    formatter = logging.Formatter(
        "%(asctime)sZ %(levelname)s %(name)s %(message)s", datefmt="%Y-%m-%dT%H:%M:%S"
    )
    formatter.converter = time.gmtime
    handler.setFormatter(formatter)
    application.addHandler(handler)
    application.setLevel(logging.INFO)
    application.propagate = False


def exception_location(exc: Exception) -> str:
    """Keep an actionable location without exception text, SQL parameters or source lines."""
    frames = traceback.extract_tb(exc.__traceback__)
    if not frames:
        return "unknown"
    frame = frames[-1]
    return f"{Path(frame.filename).name}:{frame.lineno}:{frame.name}"
