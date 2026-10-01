"""Тесты туннеля: `scripts/tunnel.py`, `scripts/node_setup.py` и `start_tunnel.bat`.

Замену Cloudflare на LocalTunnel пришлось проверить на живых запусках: туннель
регистрировался, а край отдавал 530 с кодом 1033, и без VPN, и через VPN —
то есть дело было не в сети региона. Cloudflare поэтому остался только под
ручным флагом, и его нельзя вернуть в основной путь молча.
"""

from __future__ import annotations

import io
import os
import ssl
import urllib.error
import zipfile
from pathlib import Path

import pytest

from scripts import node_setup, tunnel

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class TestUrlPatterns:
    def test_localtunnel_line(self):
        line = "your url is: https://dull-candles-doubt.loca.lt\n"
        assert tunnel.LOCALTUNNEL_URL.search(line).group(0) == (
            "https://dull-candles-doubt.loca.lt"
        )

    def test_localtunnel_ignores_cloudflare_address(self):
        line = "https://shortcuts-dirt.trycloudflare.com"
        assert tunnel.LOCALTUNNEL_URL.search(line) is None

    def test_cloudflare_banner(self):
        line = "|  https://tunnels-mailed.trycloudflare.com  |"
        assert tunnel.CLOUDFLARE_URL.search(line).group(0) == (
            "https://tunnels-mailed.trycloudflare.com"
        )

    def test_cloudflare_ignores_localtunnel_address(self):
        assert tunnel.CLOUDFLARE_URL.search("https://a-b.loca.lt") is None

    def test_patterns_do_not_match_uppercase(self):
        """Адрес всегда строчный: иначе рамка с ссылкой осталась бы пустой."""
        assert tunnel.LOCALTUNNEL_URL.search("https://AAA-BBB.loca.lt") is None


class TestCloudflarePath:
    def test_found_in_path(self, monkeypatch):
        monkeypatch.setattr(
            node_setup.shutil, "which", lambda name: r"C:\tools\cloudflared.exe"
        )
        monkeypatch.setattr(Path, "is_file", lambda self: False)
        assert tunnel.cloudflared_path() == r"C:\tools\cloudflared.exe"

    def test_found_in_well_known_folder(self, monkeypatch):
        """winget кладёт бинарник в Program Files и не добавляет его в PATH."""
        monkeypatch.setattr(node_setup.shutil, "which", lambda name: None)
        target = r"C:\Program Files (x86)\cloudflared\cloudflared.exe"
        monkeypatch.setattr(Path, "is_file", lambda self: str(self) == target)
        assert tunnel.cloudflared_path() == target

    def test_absent(self, monkeypatch):
        monkeypatch.setattr(node_setup.shutil, "which", lambda name: None)
        monkeypatch.setattr(Path, "is_file", lambda self: False)
        assert tunnel.cloudflared_path() is None


class TestNodePaths:
    def test_archive_name_follows_architecture(self, monkeypatch):
        monkeypatch.setattr(node_setup, "arch_suffix", lambda: "x64")
        assert node_setup.archive_name("v24.21.0") == "node-v24.21.0-win-x64.zip"

    def test_archive_name_for_arm(self, monkeypatch):
        monkeypatch.setattr(node_setup, "arch_suffix", lambda: "arm64")
        assert node_setup.archive_name("v24.1.0") == "node-v24.1.0-win-arm64.zip"

    def test_install_dir_is_under_localappdata(self, monkeypatch):
        monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\u\AppData\Local")
        assert node_setup.install_dir() == Path(
            r"C:\Users\u\AppData\Local\Programs\NodeTunnel"
        )

    def test_path_wins_over_install_dir(self, monkeypatch):
        monkeypatch.setattr(node_setup.shutil, "which", lambda name: r"C:\Program Files\npx.CMD")
        assert node_setup.find_npx() == Path(r"C:\Program Files\npx.CMD")

    def test_falls_back_to_install_dir(self, monkeypatch):
        monkeypatch.setattr(node_setup.shutil, "which", lambda name: None)
        target = node_setup.install_dir() / "npx.cmd"
        monkeypatch.setattr(Path, "is_file", lambda self: self == target)
        assert node_setup.find_npx() == target

    def test_nothing_found(self, monkeypatch):
        monkeypatch.setattr(node_setup.shutil, "which", lambda name: None)
        monkeypatch.setattr(Path, "is_file", lambda self: False)
        assert node_setup.find_npx() is None


class TestVersionAndChecksum:
    def test_picks_lts_with_matching_archive(self, monkeypatch):
        payload = (
            b'[{"version":"v25.0.0","lts":false,"files":["win-x64-zip"]},'
            b'{"version":"v24.21.0","lts":"Krypton","files":["win-x64-zip"]}]'
        )
        monkeypatch.setattr(node_setup, "_fetch", lambda url: payload)
        assert node_setup.latest_lts_version() == "v24.21.0"

    def test_skips_release_without_our_archive(self, monkeypatch):
        payload = (
            b'[{"version":"v24.22.0","lts":"Krypton","files":["linux-x64-tar"]}]'
        )
        monkeypatch.setattr(node_setup, "_fetch", lambda url: payload)
        assert node_setup.latest_lts_version() is None

    def test_unreachable_index_returns_none(self, monkeypatch):
        def boom(url):
            raise urllib.error.URLError("offline")

        monkeypatch.setattr(node_setup, "_fetch", boom)
        assert node_setup.latest_lts_version() is None

    def test_garbage_index_returns_none(self, monkeypatch):
        monkeypatch.setattr(node_setup, "_fetch", lambda url: b"not json")
        assert node_setup.latest_lts_version() is None

    def test_parses_shasums_line(self, monkeypatch):
        listing = (
            b"aaaa  node-v24.21.0-win-x64.tar.gz\n"
            b"bb*bb  node-v24.21.0-win-x64.zip\n"
        )
        monkeypatch.setattr(node_setup, "_fetch", lambda url: listing)
        assert node_setup.expected_sha256("v24.21.0", "node-v24.21.0-win-x64.zip") == "bb*bb"

    def test_absent_checksum_returns_none(self, monkeypatch):
        monkeypatch.setattr(node_setup, "_fetch", lambda url: b"aaaa  other.zip\n")
        assert node_setup.expected_sha256("v24.21.0", "node-v24.21.0-win-x64.zip") is None


def _fake_archive(tmp_path: Path, inner: str = "node-v1.0.0-win-x64") -> bytes:
    """Собирает zip как у Node: одна папка внутри."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        bundle.writestr(f"{inner}/npx.cmd", "@echo off\n")
        bundle.writestr(f"{inner}/node.exe", "MZ")
    return buffer.getvalue()


class TestExtract:
    def test_inner_folder_is_lifted(self, tmp_path):
        target = tmp_path / "NodeTunnel"
        payload = _fake_archive(tmp_path)

        result = node_setup._extract(payload, target)

        assert result == target
        assert (target / "npx.cmd").is_file()
        assert not (target / "node-v1.0.0-win-x64").exists()

    def test_replaces_broken_previous_install(self, tmp_path):
        target = tmp_path / "NodeTunnel"
        target.mkdir()
        (target / "old-leftover.txt").write_text("x", encoding="utf-8")

        node_setup._extract(_fake_archive(tmp_path), target)

        assert not (target / "old-leftover.txt").exists()
        assert (target / "npx.cmd").is_file()

    def test_staging_is_removed(self, tmp_path):
        target = tmp_path / "NodeTunnel"
        node_setup._extract(_fake_archive(tmp_path), target)
        assert not (tmp_path / "NodeTunnel-new").exists()

    def test_unexpected_layout_is_rejected(self, tmp_path):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as bundle:
            bundle.writestr("a/npx.cmd", "x")
            bundle.writestr("b/node.exe", "y")

        with pytest.raises(node_setup.NodeSetupError):
            node_setup._extract(buffer.getvalue(), tmp_path / "NodeTunnel")


class TestInstallNode:
    @pytest.fixture(autouse=True)
    def isolated_install_dir(self, monkeypatch, tmp_path):
        """Уводит установку во временную папку.

        Тесты не должны писать в настоящий `%LOCALAPPDATA%`: он общий для всех
        воркеров, и при `-n 8` два прогона одновременно ломают друг другу
        распаковку, а заодно оставляют мусор на машине разработчика.
        """
        target = tmp_path / "NodeTunnel"
        monkeypatch.setattr(node_setup, "install_dir", lambda: target)
        return target

    @pytest.fixture
    def ready(self, monkeypatch):
        """Ситуация «Node уже есть»: установка не должна ничего качать."""
        monkeypatch.setattr(node_setup, "find_npx", lambda: Path(r"C:\npx.cmd"))
        monkeypatch.setattr(node_setup, "npx_works", lambda exe: True)

    def test_skips_download_when_npx_works(self, ready, monkeypatch):
        def explode(url):
            raise AssertionError("не должно качать")

        monkeypatch.setattr(node_setup, "_fetch", explode)

        assert node_setup.install_node() == Path(r"C:\npx.cmd")

    def test_reports_nothing_to_do(self, ready):
        assert node_setup.install_node() == Path(r"C:\npx.cmd")

    def test_installs_when_npx_broken(self, monkeypatch):
        """Сломанный npx в PATH должен быть заменён, а не принят за готовый."""
        monkeypatch.setattr(node_setup, "find_npx", lambda: Path(r"C:\npx.cmd"))
        monkeypatch.setattr(node_setup, "latest_lts_version", lambda: "v24.21.0")
        monkeypatch.setattr(node_setup, "MIN_ARCHIVE_BYTES", 10)
        monkeypatch.setattr(node_setup, "_fetch", lambda url: _fake_archive(Path()))
        monkeypatch.setattr(node_setup, "expected_sha256", lambda v, n: None)
        installed = {"path": None}

        def fake_works(exe):
            if exe == node_setup.npx_in_install() and exe is not None:
                installed["path"] = exe
                return True
            return False

        monkeypatch.setattr(node_setup, "npx_works", fake_works)

        result = node_setup.install_node()

        assert installed["path"] == result
        assert result.parent == node_setup.install_dir()
        assert result != Path(r"C:\npx.cmd")

    def test_rejects_captive_portal_page(self, monkeypatch):
        monkeypatch.setattr(node_setup, "find_npx", lambda: None)
        monkeypatch.setattr(node_setup, "latest_lts_version", lambda: "v24.21.0")
        monkeypatch.setattr(node_setup, "_fetch", lambda url: b"<html>login</html>")

        with pytest.raises(node_setup.NodeSetupError) as error:
            node_setup.install_node()

        assert "не архив" in str(error.value)

    def test_rejects_hash_mismatch(self, monkeypatch):
        monkeypatch.setattr(node_setup, "find_npx", lambda: None)
        monkeypatch.setattr(node_setup, "latest_lts_version", lambda: "v24.21.0")
        monkeypatch.setattr(
            node_setup, "_fetch", lambda url: b"x" * (node_setup.MIN_ARCHIVE_BYTES + 1)
        )
        monkeypatch.setattr(node_setup, "expected_sha256", lambda v, n: "a" * 64)

        with pytest.raises(node_setup.NodeSetupError) as error:
            node_setup.install_node()

        assert "контрольная сумма" in str(error.value)

    def test_no_network(self, monkeypatch):
        monkeypatch.setattr(node_setup, "find_npx", lambda: None)
        monkeypatch.setattr(node_setup, "latest_lts_version", lambda: "v24.21.0")

        def boom(url):
            raise urllib.error.URLError("offline")

        monkeypatch.setattr(node_setup, "_fetch", boom)

        with pytest.raises(node_setup.NodeSetupError):
            node_setup.install_node()

    def test_packs_right_size_but_is_not_zip(self, monkeypatch):
        monkeypatch.setattr(node_setup, "find_npx", lambda: None)
        monkeypatch.setattr(node_setup, "latest_lts_version", lambda: "v24.21.0")
        monkeypatch.setattr(
            node_setup, "_fetch", lambda url: b"x" * (node_setup.MIN_ARCHIVE_BYTES + 1)
        )
        monkeypatch.setattr(node_setup, "expected_sha256", lambda v, n: None)

        with pytest.raises(node_setup.NodeSetupError):
            node_setup.install_node()

    def test_tells_user_when_checksum_unavailable(self, monkeypatch):
        monkeypatch.setattr(node_setup, "find_npx", lambda: None)
        monkeypatch.setattr(node_setup, "latest_lts_version", lambda: "v24.21.0")
        monkeypatch.setattr(node_setup, "MIN_ARCHIVE_BYTES", 10)
        monkeypatch.setattr(node_setup, "_fetch", lambda url: _fake_archive(Path()))
        monkeypatch.setattr(node_setup, "expected_sha256", lambda v, n: None)
        monkeypatch.setattr(node_setup, "npx_works", lambda exe: True)
        lines: list[str] = []

        node_setup.install_node(notify=lines.append)

        assert any("официальным списком" in line for line in lines)

    def test_falls_back_to_pinned_version(self, monkeypatch):
        monkeypatch.setattr(node_setup, "find_npx", lambda: None)
        monkeypatch.setattr(node_setup, "latest_lts_version", lambda: None)
        seen: list[str] = []

        def capture(url):
            seen.append(url)
            raise urllib.error.URLError("offline")

        monkeypatch.setattr(node_setup, "_fetch", capture)

        with pytest.raises(node_setup.NodeSetupError):
            node_setup.install_node()

        assert node_setup.FALLBACK_VERSION in seen[0]


class TestInstallNotice:
    def test_names_both_things_being_installed(self):
        """Пользователь должен знать про оба пакета, а не только про Node."""
        notice = "\n".join(tunnel._describe_install_notice())

        assert "Node.js" in notice
        assert "LocalTunnel" in notice

    def test_says_action_is_not_needed(self):
        notice = "\n".join(tunnel._describe_install_notice())

        assert "участие не нужно" in notice

    def test_warns_about_internet_and_admins(self):
        notice = "\n".join(tunnel._describe_install_notice())

        assert "Интернет не отключайте" in notice
        assert "прав администратора" in notice

    def test_explains_start_bat_needs_nothing(self):
        """Обычный запуск не должен выглядеть сломанным из-за туннеля."""
        notice = "\n".join(tunnel._describe_install_notice())

        assert "start.bat" in notice

    def test_mentions_https(self):
        notice = "\n".join(tunnel._describe_install_notice())

        assert "HTTPS" in notice


class TestTerminate:
    def test_already_exited_is_noop(self):
        class Dead:
            pid = 1

            def poll(self):
                return 0

        tunnel._terminate(Dead())  # не должно бросить

    def test_kills_children_on_windows(self, monkeypatch):
        started: dict = {}

        class Running:
            pid = 4242

            def poll(self):
                return None

            def wait(self, timeout=None):
                return 0

            def kill(self):
                started["killed"] = True

        monkeypatch.setattr(tunnel.sys, "platform", "win32")
        monkeypatch.setattr(
            tunnel.subprocess, "run", lambda *a, **kw: started.setdefault("taskkill", a)
        )

        tunnel._terminate(Running())

        assert "taskkill" in started


class TestStartTunnelBatchIsAscii:
    @pytest.fixture
    def batch_path(self) -> Path:
        return PROJECT_ROOT / "start_tunnel.bat"

    def test_file_exists(self, batch_path):
        assert batch_path.is_file()

    def test_contains_no_non_ascii(self, batch_path):
        """Регрессия: не-ASCII в `.bat` ломает запуск при двойном клике.

        Те же причины, что и в `start.bat`: `cmd.exe` читает файл в OEM-кодировке,
        а путь проекта содержит кириллицу и эмодзи.
        """
        raw = batch_path.read_bytes()

        offenders = [
            (index, byte)
            for index, byte in enumerate(raw)
            if byte > 0x7F
        ]
        assert offenders == [], f"start_tunnel.bat должен быть ASCII, найдено: {offenders[:5]}"

    def test_launches_tunnel_module(self, batch_path):
        text = batch_path.read_text(encoding="ascii")

        assert "scripts.tunnel" in text
        assert ".venv\\Scripts\\python.exe" in text
        assert "chcp 65001" in text
