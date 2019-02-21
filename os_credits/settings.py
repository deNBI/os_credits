from __future__ import annotations

from collections import UserDict
from pathlib import Path
from typing import Any, Optional, TextIO

from toml import loads

from os_credits.log import internal_logger

REPO_DIR = Path(__file__).parent.parent
# Files are read in this order and the first found will be used
default_config_paths = [
    # config inside repo
    REPO_DIR / "config" / "credits.toml",
    Path("/etc/credits.toml"),
]
default_config_path: Optional[Path] = None
for path in default_config_paths:
    if path.is_file():
        default_config_path = path


class _Config(UserDict):
    def __init__(self, config: Optional[str] = None):
        super().__init__()

    def __getitem__(self, key: str) -> Any:
        if not self.data:
            internal_logger.info(
                "No config file loaded but attribute %s was accessed. Trying to load "
                "default config from default paths (%s).",
                key,
                default_config_paths,
            )
            if not default_config_path:
                raise RuntimeError("Could not load any default config.")
            self.data.update(**loads(default_config_path.read_text()))
        try:
            return super().__getitem__(key)
        except KeyError as e:
            internal_logger.exception(
                "Config value %s was requested but not known. Appending stacktrace", e
            )
            raise


def load_config(config_io: TextIO) -> _Config:
    """
    Loads the given config. Raises an AssertionError in case a critical value is not
    given.
    :return: Config instance loaded
    """
    global config
    config_str = config_io.read()
    config = _Config(config_str)
    return config


config = _Config()
