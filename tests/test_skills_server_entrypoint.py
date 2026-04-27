from __future__ import annotations

from toolbox import skills_server as skills_server_module


def test_main_runs_stdio_server(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class StubServer:
        def run(self, transport: str) -> None:
            captured["transport"] = transport

    monkeypatch.setattr(skills_server_module, "create_server", lambda: StubServer())

    skills_server_module.main()

    assert captured == {"transport": "stdio"}


def test_main_swallows_keyboard_interrupt(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class StubServer:
        def run(self, transport: str) -> None:
            captured["transport"] = transport
            raise KeyboardInterrupt

    monkeypatch.setattr(skills_server_module, "create_server", lambda: StubServer())

    skills_server_module.main()

    assert captured == {"transport": "stdio"}
