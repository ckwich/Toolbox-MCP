from __future__ import annotations

import re

from conftest import KNOWN_WINDOWS_PYTEST_UNRAISABLE_WARNING_REGEX


def test_known_windows_unraisable_warning_regex_matches_proactor_pipe_race() -> None:
    message = """
Exception ignored in: <function BaseSubprocessTransport.__del__ at 0x12345678>
Enable tracemalloc to get traceback where the object was allocated.
See https://docs.pytest.org/en/stable/how-to/capture-warnings.html#resource-warnings for more info.
""".strip()

    assert re.search(KNOWN_WINDOWS_PYTEST_UNRAISABLE_WARNING_REGEX, message) is not None


def test_known_windows_unraisable_warning_regex_does_not_match_unrelated_warnings() -> None:
    message = "Exception ignored in: <function SomeOtherThing.__del__ at 0x12345678>\nRuntimeError: unrelated"

    assert re.search(KNOWN_WINDOWS_PYTEST_UNRAISABLE_WARNING_REGEX, message) is None
