from __future__ import annotations

import os
from pathlib import Path


STATE_DIR_NAME = ".toolbox"
STATE_FILE_NAME = "state.json"
FAKE_MANIFEST_FILE_NAME = "fake_toolset_manifest.json"


def workspace_root() -> Path:
    return Path.cwd()


def state_dir(root: Path | None = None) -> Path:
    return (root or workspace_root()) / STATE_DIR_NAME


def default_state_path(root: Path | None = None) -> Path:
    return state_dir(root) / STATE_FILE_NAME


def default_fake_manifest_path(root: Path | None = None) -> Path:
    return state_dir(root) / FAKE_MANIFEST_FILE_NAME


def configured_state_path(root: Path | None = None) -> Path:
    configured = os.getenv("TOOLBOX_STATE_PATH")
    if configured:
        return Path(configured)
    return default_state_path(root)
