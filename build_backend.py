"""PEP 517 backend that installs editable builds as regular wheels.

macOS may mark files inside hidden virtual environments with the hidden flag.
Python 3.13 skips hidden .pth files, which breaks conventional editable
installs. Building the standard wheel for PEP 660 keeps `uv sync` reliable.
"""

from typing import Any

from hatchling.build import (
    build_sdist,
    build_wheel,
    get_requires_for_build_sdist,
    get_requires_for_build_wheel,
    prepare_metadata_for_build_wheel,
)

__all__ = [
    "build_editable",
    "build_sdist",
    "build_wheel",
    "get_requires_for_build_editable",
    "get_requires_for_build_sdist",
    "get_requires_for_build_wheel",
    "prepare_metadata_for_build_editable",
    "prepare_metadata_for_build_wheel",
]


def get_requires_for_build_editable(
    config_settings: dict[str, Any] | None = None,
) -> list[str]:
    return get_requires_for_build_wheel(config_settings)


def prepare_metadata_for_build_editable(
    metadata_directory: str,
    config_settings: dict[str, Any] | None = None,
) -> str:
    return prepare_metadata_for_build_wheel(metadata_directory, config_settings)


def build_editable(
    wheel_directory: str,
    config_settings: dict[str, Any] | None = None,
    metadata_directory: str | None = None,
) -> str:
    return build_wheel(wheel_directory, config_settings, metadata_directory)
