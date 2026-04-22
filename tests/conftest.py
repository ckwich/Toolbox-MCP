from __future__ import annotations

import sys
import warnings

import pytest

KNOWN_WINDOWS_PYTEST_UNRAISABLE_WARNING_REGEX = (
    r"(?s)Exception ignored in: <function "
    r"(?:BaseSubprocessTransport|_ProactorBasePipeTransport)\.__del__ at 0x[0-9A-Fa-f]+>.*"
    r"Enable tracemalloc to get traceback where the object was allocated\."
)


def _install_windows_unraisable_filter() -> None:
    warnings.filterwarnings(
        "ignore",
        message=KNOWN_WINDOWS_PYTEST_UNRAISABLE_WARNING_REGEX,
        category=pytest.PytestUnraisableExceptionWarning,
    )


def pytest_configure() -> None:
    if sys.platform != "win32":
        return

    # The remaining Windows noise is the CPython proactor/pytest unraisable
    # destructor race around already-closed subprocess pipe handles. Keep the
    # filter narrow so unrelated unraisable warnings still fail loudly.
    _install_windows_unraisable_filter()


def pytest_runtest_setup() -> None:
    if sys.platform != "win32":
        return

    _install_windows_unraisable_filter()
