"""Запуск сервера вместе с туннелем Cloudflare.

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

Тоннель бесплатный и поддомен в нём случайный: адрес меняется при каждом
запуске, и старую ссылку открыть уже нельзя. Постоянный адрес требует
своего домена и именованного туннеля, это отдельная задача.
"""

from __future__ import annotations

import re
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

from auth.service import ensure_admin_account
from core.config import configure_logging, settings
from scripts.launcher import (
    _fail,
    free_port_suggestion,
    lan_address,
    open_browser_later,
    port_is_free,
)

# Адрес quick-туннеля печатается в выводе cloudflared строкой вида
# `https://длинное-слово-1234.trycloudflare.com`. Разбирать вывод проще,
# чем поднимать локальный API-клиент туннеля.
URL_PATTERN = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")

CLOUDFLARED = "cloudflared"

# winget ставит бинарник сюда и не добавляет папку в PATH, поэтому поиск
# только по PATH объявлял бы установленный туннель отсутствующим.
WELL_KNOWN_PATHS = (
    r"C:\Program Files (x86)\cloudflared\cloudflared.exe",
    r"C:\Program Files\cloudflared\cloudflared.exe",
)

INSTALL_HINT = (
    "Установите туннель и попробуйте снова:",
    "  winget install --id Cloudflare.cloudflared",
    "После установки перезапустите это окно.",
)


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


def read_tunnel_url(
    process: subprocess.Popen[str],
    holder: dict[str, str],
    found: threading.Event,
) -> None:
    """Читает stderr туннеля и запоминает публичный адрес.

    cloudflared пишет всё в stderr, поэтому stdout не перехватываем.
    Событие ставится в конце, после печати адреса: иначе основной поток
    успевает допечатать своё поверх рамки с адресом.
    """
    assert process.stderr is not None
    for line in process.stderr:
        match = URL_PATTERN.search(line)
        if match and "url" not in holder:
            holder["url"] = match.group(0)
            print()
            print("  " + "=" * 62)
            print()
            print(f"   {holder['url']}")
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
            print()
            found.set()


def wait_until_serving(url: str, timeout: float = 120.0) -> bool:
    """Ждёт, пока адрес начнёт отвечать.

    Cloudflare выдаёт имя туннеля раньше, чем край начинает его отдавать.
    Первые полминуты по ссылке приходит ошибка 530, и человек успевает
    решить, что всё сломалось, и закрыть окно. Поэтому ждём рабочего
    ответа и только потом говорим, что можно открывать.
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


def main() -> int:
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except (AttributeError, ValueError):
        pass

    configure_logging()
    host, port = settings.host, settings.port

    print()
    print(f"  {settings.app_name} — запуск с туннелем")
    print("  " + "-" * 40)
    print()

    executable = cloudflared_path()
    if executable is None:
        return _fail(
            [
                "Не найден cloudflared — без него туннель не поднять.",
                "",
                *INSTALL_HINT,
                "",
                "Обычный запуск без туннеля: start.bat",
            ]
        )

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
        has_admin = ensure_admin_account(session) is not None

    import uvicorn

    server_config = uvicorn.Config("core.main:app", host=host, port=port, log_level="warning")
    server = uvicorn.Server(server_config)
    server_thread = threading.Thread(target=server.run, daemon=True)
    server_thread.start()

    print("  Сервер поднят, запускаю туннель...")
    # Протокол задан явно. Замеры на этой машине: дефолтный QUIC не смог
    # зарегистрироваться вовсе ("Register tunnel error from server side:
    # context deadline exceeded"), а HTTP/2 регистрируется всегда, поэтому
    # берём его. Соединение всё равно может рваться с стороны сети или
    # Cloudflare - это не лечится протоколом, скрипт лишь честно ждёт
    # готовности адреса и не открывает браузер на 530.
    # Запускаем именно найденный путь, а не имя: winget кладёт бинарник в
    # Program Files (x86) и не добавляет папку в PATH, а подстановка в
    # `subprocess` ищет только по PATH и падает с WinError 2.
    tunnel = subprocess.Popen(  # noqa: S603 - путь найден выше, аргументы фиксированы
        [
            executable,
            "tunnel",
            "--url",
            f"http://127.0.0.1:{port}",
            "--protocol",
            "http2",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    found = threading.Event()
    holder: dict[str, str] = {}
    reader = threading.Thread(
        target=read_tunnel_url, args=(tunnel, holder, found), daemon=True
    )
    reader.start()

    lan = lan_address()
    if lan:
        print(f"  В своей сети сайт и так доступен: http://{lan}:{port}")
        print("  Туннель нужен, только если показываете из интернета.")
    print()
    print("  Жду публичный адрес...")

    # Адрес появляется за несколько секунд; если туннель не поднялся,
    # сообщаем об этом, а не молчим вечно.
    if not found.wait(timeout=60):
        print()
        print("  Туннель не ответил за минуту. Проверьте подключение")
        print("  к интернету и попробуйте ещё раз.")
        tunnel.terminate()
        server.should_exit = True
        return 1

    public = holder["url"]

    # Адрес выдан, но край Cloudflare ещё не начал его отдавать: первые
    # полминуты по ссылке приходит 530. Не открываем браузер на заведомо
    # нерабочий адрес и ждём настоящей готовности.
    if wait_until_serving(public):
        print()
        print("  Ссылка отвечает, можно открывать.")
    else:
        print()
        print("  Адрес выдан, но край Cloudflare не отвечает уже две минуты.")
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
        while not tunnel.poll() is not None:
            time.sleep(0.5)
    except KeyboardInterrupt:
        print()
        print("  Останавливаю...")
    finally:
        # Туннель гасим первым: иначе он секунду-другую продолжит
        # принимать запросы в уже остановившееся приложение.
        for process in (tunnel, None):
            if process is None:
                break
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
        server.should_exit = True
        server_thread.join(timeout=10)

    print("  Остановлено.")
    return 0


if __name__ == "__main__":
    sys.exit(main())