"""Тесты лаунчера `scripts/launcher.py` и файла `start.bat`.

Лаунчер запускает сервер, поэтому его ошибки видны только при ручном
двойном клике. Отдельно проверяется `start.bat`: `cmd.exe` читает `.bat`
в системной кодировке, и любой не-ASCII символ внутри превращается в
текст, который `cmd` пытается выполнить как команду.
"""

from __future__ import annotations

import socket
from pathlib import Path

import pytest

from scripts import launcher

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _bind(port: int) -> socket.socket:
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", port))
    server.listen(1)
    return server


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class TestPortChecks:
    def test_free_port_detected(self):
        assert launcher.port_is_free("127.0.0.1", _free_port()) is True

    def test_busy_port_detected(self):
        server = _bind(0)
        try:
            port = server.getsockname()[1]
            assert launcher.port_is_free("127.0.0.1", port) is False
        finally:
            server.close()

    def test_repeated_checks_agree(self):
        """Регрессия: `connect_ex` давал разные ответы для одного порта."""
        server = _bind(0)
        try:
            port = server.getsockname()[1]
            assert [launcher.port_is_free("127.0.0.1", port) for _ in range(5)] == [False] * 5
            assert [
                launcher.port_is_free("127.0.0.1", port + 1) for _ in range(5)
            ] == [True] * 5
        finally:
            server.close()

    def test_port_frees_up_after_close(self):
        server = _bind(0)
        port = server.getsockname()[1]
        server.close()

        assert launcher.port_is_free("127.0.0.1", port) is True

    def test_suggestion_is_free_and_nearby(self):
        taken = _bind(0)
        try:
            port = taken.getsockname()[1]
            suggestion = launcher.free_port_suggestion("127.0.0.1", port)

            assert 0 < suggestion - port < 20
            assert launcher.port_is_free("127.0.0.1", suggestion) is True
        finally:
            taken.close()

    def test_suggestion_falls_back_to_original_when_window_is_full(self, monkeypatch):
        monkeypatch.setattr(launcher, "port_is_free", lambda host, port: False)

        assert launcher.free_port_suggestion("127.0.0.1", 8000) == 8000


class TestFailBranch:
    def test_returns_one_and_survives_closed_stdin(self, monkeypatch, capsys):
        monkeypatch.setattr("builtins.input", _raise_eof)

        result = launcher._fail(["первая строка", "вторая строка"], pause=True)

        assert result == 1
        out = capsys.readouterr().out
        assert "первая строка" in out
        assert "вторая строка" in out


def _raise_eof(prompt: str = "") -> str:
    raise EOFError


class TestMainRefusesBusyPort:
    def test_does_not_start_server(self, monkeypatch, capsys):
        monkeypatch.setattr(launcher, "port_is_free", lambda host, port: False)
        monkeypatch.setattr(launcher, "free_port_suggestion", lambda host, port: port + 1)
        monkeypatch.setattr(launcher, "_fail", lambda lines, pause=True: 1)
        started: list[str] = []
        monkeypatch.setattr(launcher, "configure_logging", lambda: None)

        def _boom(*args, **kwargs):
            started.append("ran")
            raise AssertionError("сервер не должен запускаться при занятом порте")

        import uvicorn

        monkeypatch.setattr(uvicorn, "run", _boom)

        assert launcher.main() == 1
        assert started == []

    def test_message_mentions_busy_port(self, monkeypatch, capsys):
        monkeypatch.setattr(launcher, "port_is_free", lambda host, port: False)
        monkeypatch.setattr(launcher, "free_port_suggestion", lambda host, port: 8001)
        monkeypatch.setattr(launcher, "configure_logging", lambda: None)
        monkeypatch.setattr("builtins.input", _raise_eof)

        launcher.main()

        out = capsys.readouterr().out
        assert "8000" in out
        assert "8001" in out


class TestMainHappyPath:
    @pytest.fixture
    def captured_run(self, monkeypatch):
        import uvicorn

        calls: list[dict] = []
        monkeypatch.setattr(uvicorn, "run", lambda *a, **kw: calls.append(kw or dict(a)))
        monkeypatch.setattr(launcher, "port_is_free", lambda host, port: True)
        monkeypatch.setattr(launcher, "open_browser_later", lambda url, delay=1.5: None)
        monkeypatch.setattr(launcher, "configure_logging", lambda: None)
        monkeypatch.setattr(launcher.settings, "port", 8000)
        monkeypatch.setattr(launcher.settings, "host", "127.0.0.1")
        return calls

    def test_prepares_database_and_serves(self, captured_run):
        from database.session import engine
        from sqlalchemy import inspect

        assert launcher.main() == 0

        assert len(captured_run) == 1
        assert captured_run[0]["port"] == 8000
        assert "site_settings" in inspect(engine).get_table_names()

    def test_prints_links(self, captured_run, capsys):
        launcher.main()

        out = capsys.readouterr().out
        assert "http://127.0.0.1:8000" in out
        assert "/login" in out
        assert "Ctrl+C" in out

    def test_prints_admin_credentials_on_first_run(
        self, captured_run, capsys, db_session, clean_tables
    ):
        """Учётку администратора нужно показать: иначе в раздел не попасть."""
        launcher.main()

        out = capsys.readouterr().out
        assert "admin" in out
        assert "/account/password" in out

    def test_existing_admin_password_is_not_reprinted(
        self, captured_run, capsys, db_session, clean_tables
    ):
        from auth.service import apply_new_password, ensure_admin_account

        user = ensure_admin_account(db_session)
        apply_new_password(db_session, user, "свой-пароль")

        launcher.main()

        out = capsys.readouterr().out
        assert "пароль admin" not in out


class TestBrowserOpen:
    def test_browser_failure_is_not_fatal(self, monkeypatch):
        import webbrowser

        monkeypatch.setattr(webbrowser, "open", _raise_runtime_error)

        launcher.open_browser_later("http://127.0.0.1:8000", delay=0)


def _raise_runtime_error(url: str) -> None:
    raise RuntimeError("нет браузера")


class TestStartBatchIsAscii:
    @pytest.fixture
    def batch_path(self) -> Path:
        return PROJECT_ROOT / "start.bat"

    def test_file_exists(self, batch_path):
        assert batch_path.is_file()

    def test_contains_no_non_ascii(self, batch_path):
        """Регрессия: не-ASCII в `.bat` ломает запуск при двойном клике.

        Путь проекта содержит кириллицу и эмодзи, `cmd.exe` декодирует файл в
        OEM-кодировке, и русские строки превращаются в невыполнимые команды.
        """
        raw = batch_path.read_bytes()

        offenders = [
            (index, byte)
            for index, byte in enumerate(raw)
            if byte > 0x7F
        ]
        assert offenders == [], f"start.bat должен быть ASCII, найдено: {offenders[:5]}"

    def test_launches_python_launcher(self, batch_path):
        text = batch_path.read_text(encoding="ascii")

        assert "scripts.launcher" in text
        assert ".venv\\Scripts\\python.exe" in text
        assert "chcp 65001" in text

    def test_fails_loudly_when_python_missing(self, batch_path):
        text = batch_path.read_text(encoding="ascii")

        assert ":no_python" in text
        assert ":failed" in text
