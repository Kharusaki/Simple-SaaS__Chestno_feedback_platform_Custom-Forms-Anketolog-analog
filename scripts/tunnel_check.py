"""Живая проверка туннеля LocalTunnel.

Поднимает сайт на временной базе, пробрасывает его наружу и проходит сценарий
по публичной ссылке настоящим HTTP — тем же путём, каким пойдёт респондент.

    python -m scripts.tunnel_check

Рабочая база не затрагивается: сервер поднимается на `_tunnel_check.db`,
которая удаляется в конце прогона.
"""

from __future__ import annotations

import argparse
import os
import re
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx

PROJECT_ROOT = Path(__file__).resolve().parent.parent

PASSED: list[str] = []
FAILED: list[str] = []

URL_PATTERN = re.compile(r"https://[a-z0-9-]+\.loca\.lt")

# Страница-пароль localtunnel показывает первым посетителю с нового адреса.
INTERSTITIAL_MARKERS = (
    "tunnel password",
    "click to continue",
    "bypass-tunnel-reminder",
)


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASSED.append(name)
        print(f"  ok   {name}")
    else:
        FAILED.append(f"{name} {detail}".strip())
        print(f"  FAIL {name} {detail}".rstrip())


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _serve(port: int, db_path: Path) -> "uvicorn.Server":
    import uvicorn

    os.environ["DATABASE_URL"] = f"sqlite:///{db_path.as_posix()}"
    os.environ["RATE_LIMIT_SUBMIT_PER_MINUTE"] = "0"
    os.environ["RATE_LIMIT_CREATE_PER_HOUR"] = "0"
    os.environ["RATE_LIMIT_PAGE_PER_MINUTE"] = "0"
    # Подробные логи забивают вывод проверки, а DEBUG по умолчанию взят из
    # .env, где он включён ради разработки.
    os.environ["DEBUG"] = "false"

    from core.main import app

    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    )
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(100):
        if server.started:
            return server
        time.sleep(0.1)
    raise SystemExit("сервер не поднялся")


def _stop(server: "uvicorn.Server") -> None:
    server.should_exit = True
    for _ in range(100):
        if not server.started:
            return
        time.sleep(0.1)


def _kill_tree(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/T", "/F", "/PID", str(process.pid)],
            capture_output=True,
            check=False,
        )
    else:
        process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()


def _dispose_engine() -> None:
    try:
        from database.session import engine

        engine.dispose()
    except Exception:  # noqa: BLE001 - уборка не должна ломать прогон
        pass


def _drop_db(db_path: Path) -> None:
    """Удаляет файлы базы, молча переживая занятый файл.

    На Windows удаление открытого файла даёт PermissionError, и незакрытая
    база не должна превращать успешный прогон в ошибку запуска.
    """
    for suffix in ("", "-wal", "-shm"):
        try:
            Path(str(db_path) + suffix).unlink(missing_ok=True)
        except OSError:
            pass


def _read_url(
    process: subprocess.Popen[str],
    holder: dict[str, str],
    found: threading.Event,
) -> None:
    assert process.stdout is not None
    for line in process.stdout:
        match = URL_PATTERN.search(line)
        if match and "url" not in holder:
            holder["url"] = match.group(0)
            found.set()


def start_localtunnel(port: int, timeout: float = 240.0) -> tuple[subprocess.Popen[str], str]:
    """Поднимает туннель и возвращает процесс вместе с публичным адресом.

    Первый запуск докачивает пакет localtunnel с npm, поэтому таймаут здесь
    заметно больше, чем у повторных.
    """
    command = [
        "npx.cmd" if os.name == "nt" else "npx",
        "-y",
        "localtunnel",
        "--port",
        str(port),
    ]
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=PROJECT_ROOT,
    )
    holder: dict[str, str] = {}
    found = threading.Event()
    threading.Thread(
        target=_read_url, args=(process, holder, found), daemon=True
    ).start()
    if not found.wait(timeout=timeout):
        _kill_tree(process)
        raise SystemExit("localtunnel не выдал публичный адрес")
    return process, holder["url"]


def wait_until_serving(url: str, timeout: float = 120.0) -> bool:
    """Ждёт, пока адрес начнёт отвечать.

    Туннель печатает адрес раньше, чем край начинает его отдавать, поэтому
    первый запрос может не пройти.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with httpx.Client(timeout=15.0) as client:
                if client.get(f"{url}/health").status_code == 200:
                    return True
        except httpx.HTTPError:
            pass
        time.sleep(3)
    return False


def run_scenario(base: str) -> None:
    owner = httpx.Client(base_url=base, timeout=30.0, follow_redirects=False)
    guest = httpx.Client(base_url=base, timeout=30.0, follow_redirects=False)

    print("1. Публичная страница")
    home = guest.get("/")
    check("главная отдаётся", home.status_code == 200, f"статус {home.status_code}")
    lowered = home.text.lower()
    check(
        "страницы-пароля нет",
        not any(marker in lowered for marker in INTERSTITIAL_MARKERS),
        "localtunnel показал Click to Continue",
    )
    check(
        "вёрстка приложения на месте",
        "<title>" in home.text and "/static/css/style.css" in home.text,
    )
    css = guest.get("/static/css/style.css")
    check(
        "стили отдаются",
        css.status_code == 200 and len(css.content) > 10_000,
        f"статус {css.status_code}, {len(css.content)} байт",
    )

    print("2. Создатель анкет")
    username = f"tc-{int(time.time()) % 100000}"
    r = owner.post(
        "/register",
        data={
            "username": username,
            "password": "tunnel-secret",
            "password_repeat": "tunnel-secret",
        },
    )
    check("регистрация", r.status_code == 303, f"статус {r.status_code}")
    check("дашборд открыт своей сессией", owner.get("/dashboard").status_code == 200)

    r = owner.post(
        "/surveys/new",
        data={
            "title": "Анкета через туннель",
            "description": "Проверка туннеля",
            "theme": "light",
            "font": "system",
            "q1_text": "Как вам сайт",
            "q1_type": "single",
            "q1_required": "1",
            "q1_choices": "Отлично\nНормально",
        },
    )
    check("анкета создана", r.status_code == 303, f"статус {r.status_code}")
    location = r.headers.get("location", "")
    slug = location.split("/s/")[-1].split("/")[0] if "/s/" in location else ""
    check("slug есть в адресе", bool(slug), location)
    if not slug:
        owner.close()
        guest.close()
        return

    print("3. Респондент без регистрации")
    public = guest.get(f"/s/{slug}")
    check(
        "анкета видна по ссылке",
        public.status_code == 200 and "Как вам сайт" in public.text,
        f"статус {public.status_code}",
    )
    radio = re.search(
        r'<input type="radio" name="(q\d+)" value="([^"]*)"', public.text
    )
    check("у вопроса есть варианты ответа", bool(radio))
    if radio:
        r = guest.post(f"/s/{slug}", data={radio.group(1): radio.group(2)})
        check("ответ принят", r.status_code == 303, f"статус {r.status_code}")
    check("страница благодарности", guest.get(f"/s/{slug}/thanks").status_code == 200)

    print("4. Права и статистика")
    check("статистика владельца доступна", owner.get(f"/s/{slug}/stats").status_code == 200)
    stranger = httpx.Client(base_url=base, timeout=30.0, follow_redirects=False)
    r = stranger.get(f"/s/{slug}/stats")
    check(
        "чужая сессия не видит статистику",
        r.status_code in (403, 404),
        f"статус {r.status_code}",
    )
    stranger.close()
    owner.close()
    guest.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Проверка туннеля LocalTunnel.")
    parser.add_argument(
        "--port", type=int, default=0, help="локальный порт сайта, 0 = любой свободный"
    )
    args = parser.parse_args()

    port = args.port or _free_port()
    db_path = PROJECT_ROOT / "_tunnel_check.db"
    _drop_db(db_path)

    print("Поднимаю сайт на временной базе...")
    server = _serve(port, db_path)
    tunnel: subprocess.Popen[str] | None = None
    try:
        print("Запускаю localtunnel, первый запуск докачивает пакет...")
        tunnel, url = start_localtunnel(port)
        print(f"Публичный адрес: {url}")
        print()
        if wait_until_serving(url):
            check("публичный адрес отвечает", True)
            run_scenario(url)
        else:
            check("публичный адрес отвечает", False, "не дождался ответа")
    finally:
        if tunnel is not None:
            _kill_tree(tunnel)
        _stop(server)
        _dispose_engine()
        _drop_db(db_path)

    print()
    print(f"Пройдено: {len(PASSED)}, провалено: {len(FAILED)}")
    for name in FAILED:
        print(f"  FAIL {name}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
