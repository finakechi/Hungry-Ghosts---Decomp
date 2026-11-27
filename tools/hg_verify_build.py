"""Route legacy verification builds into the active attempt when requested."""
from pathlib import Path
import os


def build_path(default):
    destination = os.environ.get('HG_VERIFY_BUILD_DIR')
    if not destination:
        return default
    directory = Path(destination).resolve()
    return default if default.resolve().is_relative_to(directory.parent) else directory / default.name
