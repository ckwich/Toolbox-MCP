from __future__ import annotations

from toolbox import server as server_module


def test_main_runs_stdio_server(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class StubServer:
        def run(self, transport: str) -> None:
            captured["transport"] = transport

    monkeypatch.setattr(server_module, "create_server", lambda: StubServer())

    server_module.main()

    assert captured == {"transport": "stdio"}


def test_main_swallows_keyboard_interrupt(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class StubServer:
        def run(self, transport: str) -> None:
            captured["transport"] = transport
            raise KeyboardInterrupt

    monkeypatch.setattr(server_module, "create_server", lambda: StubServer())

    server_module.main()

    assert captured == {"transport": "stdio"}
