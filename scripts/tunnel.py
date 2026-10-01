"""Запуск сервера вместе с туннелем в интернет.

Зачем отдельный запуск, если есть `start.bat`: туннель нужен не всегда, а
показывать его в обычном окне запуска значило бы путать человека лишним
адресом. Здесь всё, что нужно для показа в интернете, в одном окне: сервер,
туннель, готовую публичную ссылку крупно и предупреждение, что она живёт
до перезапуска.

Почему это не кнопка в самом сайте. Сайт слушает все интерфейсы и потому
доступен каждому в локальной сети. Кнопка «включить туннель» на такой
странице означала бы, что сосед по Wi-Fi может одним щелчком выставить
вашу анкету в интернет, а пароль администратора по умолчанию известен всем,
кто открыл репозиторий. Управляет тем, кто включает компьютер, тот, кто
сидит за ним, — и это единственное разумное разделение.

## Почему LocalTunnel, а не Cloudflare

Cloudflare был основным вариантом и на этой машине перестал работать: туннель
регистрируется (`Registered tunnel connection`), но край отдаёт 530 с
кодом 1033 — «туннель не найден». Проверено и без VPN, и через VPN, то есть
дело не в сети региона, а в самом бесплатном quick-туннеле Cloudflare.

Cloudflare поэтому оставлен как запасной вариант и включается только явно:
`python -m scripts.tunnel --cloudflare`. Молчаливого переключения на
заведомо нерабочий вариант нет намеренно: человек должен знать, чем он
пользуется.

Поддомен в обоих случаях случайный: адрес меняется при каждом запуске, и
старую ссылку открыть уже нельзя. Постоянный адрес требует своего домена и
именованного туннеля, это отдельная задача.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from core.config import configure_logging, settings
from scripts.launcher import (
    _fail,
    free_port_suggestion,
    lan_address,
    open_browser_later,
    port_is_free,
)

# LocalTunnel печатает адрес в stdout строкой `your url is: https://x.loca.lt`,
# cloudflared пишет в stderr рамку с `https://x.trycloudflare.com`.
LOCALTUNNEL_URL = re.compile(r"https://[a-z0-9-]+\.loca\.lt")
CLOUDFLARE_URL = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")

# Первый запуск докачивает пакет localtunnel с npm, поэтому адрес появляется
# заметно позже, чем при повторных запусках.
LOCALTUNNEL_URL_TIMEOUT = 240.0
CLOUDFLARE_URL_TIMEOUT = 60.0

CLOUDFLARED = "cloudflared"

# winget ставит бинарник сюда и не добавляет папку в PATH, поэтому поиск
# только по PATH объявлял бы установленный туннель отсутствующим.
WELL_KNOWN_PATHS = (
    r"C:\Program Files (x86)\cloudflared\cloudflared.exe",
    r"C:\Program Files\cloudflared\cloudflared.exe",
)

CLOUDFLARE_HINT = (
    "Установите туннель и попробуйте снова:",
    "  winget install --id Cloudflare.cloudflared",
    "После установки перезапустите это окно.",
)


@dataclass
class Tunnel:
    """Запущенный туннель: процесс и выданный им публичный адрес."""

    process: subprocess.Popen[str]
    url: str
    provider: str


def cloudflared_path() -> str | None:
    """Путь к cloudflared или None, если его нет в системе."""
    from shutil import which

    found = which(CLOUDFLARED)
    if found:
        return found
    for candidate in WELL_KNOWN_PATHS:
        if Path(candidate).is_file():
            return candidate
    return None


def _watch_for_url(
    stream: "object",
    pattern: re.Pattern[str],
    holder: dict[str, str],
    found: threading.Event,
) -> None:
    """Читает поток туннеля и запоминает публичный адрес."""
    for line in stream:
        match = pattern.search(line)
        if match and "url" not in holder:
            holder["url"] = match.group(0)
            found.set()


def _await_url(
    process: subprocess.Popen[str],
    stream: "object",
    pattern: re.Pattern[str],
    timeout: float,
) -> str:
    holder: dict[str, str] = {}
    found = threading.Event()
    threading.Thread(
        target=_watch_for_url, args=(stream, pattern, holder, found), daemon=True
    ).start()
    if not found.wait(timeout=timeout):
        raise TimeoutError("туннель не выдал публичный адрес")
    return holder["url"]


def start_localtunnel(port: int, npx: Path) -> Tunnel:
    """Поднимает LocalTunnel и возвращает публичный адрес."""
    command = [
        str(npx),
        "-y",
        "localtunnel",
        "--port",
        str(port),
    ]
    process = subprocess.Popen(  # noqa: S603 - путь проверен, аргументы фиксированы
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    try:
        url = _await_url(
            process,
            process.stdout,
            LOCALTUNNEL_URL,
            LOCALTUNNEL_URL_TIMEOUT,
        )
    except TimeoutError:
        _terminate(process)
        raise
    return Tunnel(process=process, url=url, provider="LocalTunnel")


def start_cloudflared(port: int, executable: str) -> Tunnel:
    """Поднимает Cloudflare quick-туннель и возвращает публичный адрес."""
    process = subprocess.Popen(  # noqa: S603 - путь проверен, аргументы фиксированы
        [
            executable,
            "tunnel",
            "--url",
            f"http://127.0.0.1:{port}",
            # Протокол задан явно. Замеры на этой машине: дефолтный QUIC не
            # смог зарегистрироваться вовсе ("Register tunnel error from server
            # side: context deadline exceeded"), а HTTP/2 регистрируется всегда.
            "--protocol",
            "http2",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    try:
        url = _await_url(
            process,
            process.stderr,
            CLOUDFLARE_URL,
            CLOUDFLARE_URL_TIMEOUT,
        )
    except TimeoutError:
        _terminate(process)
        raise
    return Tunnel(process=process, url=url, provider="Cloudflare")


def _terminate(process: subprocess.Popen[str]) -> None:
    """Останавливает туннель вместе со всеми его дочерними процессами.

    npx запускает node отдельным процессом, и terminate на родителе оставил бы
    его жить: порт оставался бы занят, а туннель — отвечать.
    """
    if process.poll() is not None:
        return
    if sys.platform == "win32":
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


def wait_until_serving(url: str, timeout: float = 120.0) -> bool:
    """Ждёт, пока адрес начнёт отвечать.

    Туннель выдаёт имя раньше, чем край начинает его отдавать. Первые
    полминуты по ссылке приходит ошибка, и человек успевает решить, что всё
    сломалось, и закрыть окно. Поэтому ждём рабочего ответа и только потом
    говорим, что можно открывать.
    """
    import urllib.error
    import urllib.request

    deadline = time.time() + timeout
    print("  Жду, пока адрес начнёт отвечать (обычно 20-40 секунд)...")
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url + "/health", timeout=15) as response:
                return response.status == 200
        except urllib.error.HTTPError:
            pass
        except Exception:  # noqa: BLE001 - туннель может ещё не подняться
            pass
        time.sleep(4)
    return False


def _describe_install_notice() -> list[str]:
    """Что скачивается при первом запуске туннеля, нормальным языком."""
    return [
        "Первый запуск скачает две вещи, обе нужны только для туннеля:",
        "",
        "  1. Node.js — программа, на которой туннель работает.",
        "     Официальный сайт nodejs.org, около 38 МБ, без прав администратора.",
        "  2. LocalTunnel — сам туннель, маленькая программа с npm.",
        "",
        "Ваше участие не нужно: всё поставится само. Файлы безопасные,",
        "качаются по HTTPS и сверяются с официальными контрольными суммами.",
        "Интернет не отключайте, пока идёт загрузка.",
        "",
        "Обычному запуску start.bat ничего из этого не требуется.",
    ]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Запуск сайта вместе с туннелем в интернет.",
    )
    parser.add_argument(
        "--cloudflare",
        action="store_true",
        help="запасной вариант: туннель Cloudflare вместо LocalTunnel",
    )
    args = parser.parse_args()

    try:
        # Кодировка задаётся явно: иначе текст уходит в системную кодировку
        # и в окне запуска рассыпается на русском.
        sys.stdout.reconfigure(line_buffering=True, encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

    configure_logging()
    host, port = settings.host, settings.port

    print()
    print(f"  {settings.app_name} — запуск с туннелем")
    print("  " + "-" * 40)
    print()

    provider = "Cloudflare" if args.cloudflare else "LocalTunnel"

    if args.cloudflare:
        executable = cloudflared_path()
        if executable is None:
            return _fail(
                [
                    "Не найден cloudflared — без него туннель не поднять.",
                    "",
                    *CLOUDFLARE_HINT,
                    "",
                    "Обычный запуск без туннеля: start.bat",
                ]
            )
        tool: Path | str = executable
    else:
        from scripts import node_setup

        existing = node_setup.find_npx()
        if existing is not None and node_setup.npx_works(existing):
            tool = existing
        else:
            print()
            for line in _describe_install_notice():
                print(f"  {line}" if line else "")
            print()
            print("  Начинаю. Это займёт около минуты.")
            print()
            try:
                tool = node_setup.install_node(
                    notify=lambda line: print(f"  {line}")
                )
            except node_setup.NodeSetupError as error:
                return _fail([str(error), "", "Обычный запуск: start.bat"])
            print()

    if not port_is_free(host, port):
        other = free_port_suggestion(host, port)
        return _fail(
            [
                f"Порт {port} уже занят — значит, сервер уже запущен.",
                "",
                "Закройте другое окно или запустите на свободном порту:",
                f"  .venv\\Scripts\\python.exe -m uvicorn core.main:app --port {other}",
            ]
        )

    from database.session import SessionLocal, create_all

    create_all()

    with SessionLocal() as session:
        from auth.service import ensure_admin_account

        has_admin = ensure_admin_account(session) is not None

    import uvicorn

    server_config = uvicorn.Config("core.main:app", host=host, port=port, log_level="warning")
    server = uvicorn.Server(server_config)
    server_thread = threading.Thread(target=server.run, daemon=True)
    server_thread.start()

    lan = lan_address()
    if lan:
        print(f"  В своей сети сайт и так доступен: http://{lan}:{port}")
        print("  Туннель нужен, только если показываете из интернета.")
    print()
    print(f"  Запускаю туннель {provider}...")

    tunnel: Tunnel | None = None
    try:
        tunnel = start_cloudflared(port, str(tool)) if args.cloudflare else start_localtunnel(port, Path(tool))
    except (TimeoutError, OSError) as error:
        server.should_exit = True
        server_thread.join(timeout=10)
        return _fail([f"Туннель {provider} не поднялся: {error}", "", "Обычный запуск: start.bat"])

    public = tunnel.url
    print()
    print("  " + "=" * 62)
    print()
    print(f"   {public}")
    print()
    print("  " + "=" * 62)
    print()
    print("  Открывайте анкеты по этому адресу: ссылки, которые")
    print("  вы скопируете в кабинете, будут начинаться с него.")
    print()
    print("  ВНИМАНИЕ: адрес случайный и меняется при каждом запуске.")
    print("  Старые ссылки после перезапуска перестанут открываться.")
    print("  Ссылка работает, пока открыто это окно и включён")
    print("  компьютер.")
    if args.cloudflare:
        print()
        print("  Cloudflare — запасной вариант, на этой машине он может")
        print("  отдавать ошибку 530. Если так, запустите без --cloudflare.")
    print()

    if wait_until_serving(public):
        print()
        print("  Ссылка отвечает, можно открывать.")
    else:
        print()
        print("  Адрес выдан, но край не отвечает уже две минуты.")
        print("  Подождите минуту и обновите страницу, либо попробуйте")
        print("  ещё раз: иногда такое бывает при слабом интернете.")
    print()

    open_browser_later(public, delay=0.5)

    if has_admin:
        print("  Учётка администратора: логин admin, пароль admin.")
        print("  Если сайт виден из интернета, смените пароль прямо сейчас.")
        print()
    print("  Остановка: закройте это окно или нажмите Ctrl+C")
    print("  Сервер и туннель остановятся вместе.")
    print()

    try:
        while tunnel.process.poll() is None:
            time.sleep(0.5)
    except KeyboardInterrupt:
        print()
        print("  Останавливаю...")
    finally:
        # Туннель гасим первым: иначе он секунду-другую продолжит
        # принимать запросы в уже остановившееся приложение.
        _terminate(tunnel.process)
        server.should_exit = True
        server_thread.join(timeout=10)

    print("  Остановлено.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
