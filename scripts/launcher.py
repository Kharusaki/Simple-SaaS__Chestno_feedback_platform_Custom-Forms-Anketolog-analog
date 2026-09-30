"""Запуск одной командой: готовит базу, открывает браузер, поднимает сервер.

Отдельный модуль, а не код в `start.bat`, потому что cmd.exe читает файл в
системной кодировке: русский текст внутри `.bat` превращается в команды, а
путь проекта с кириллицей и эмодзи бьётся. `start.bat` остаётся ASCII,
все сообщения печатает Python.
"""

from __future__ import annotations

import socket
import sys
import threading
import time
import webbrowser

from auth.service import ensure_admin_account
from core.config import configure_logging, settings


def _fail(lines: list[str], pause: bool = True) -> int:
    print()
    for line in lines:
        print(f"  {line}")
    print()
    if pause:
        try:
            input("  Нажмите Enter, чтобы закрыть окно.")
        except EOFError:
            pass
    return 1


def port_is_free(host: str, port: int) -> bool:
    """Занят ли порт.

    Проверка идёт через `bind`, а не через `connect`: подключение к
    незанятому порту на Windows возвращает WSAEWOULDBLOCK и оставляет сокет
    в полуоткрытом состоянии, из-за чего следующая проверка того же порта
    даёт противоположный ответ. `bind` отвечает на вопрос, который нас
    действительно волнует: сможет ли сервер занять порт.

    `SO_REUSEADDR` намеренно не выставляется: на Windows он разрешает
    перехватить порт, который уже слушает чужой сокет.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind((host, port))
        except OSError:
            return False
    return True


def free_port_suggestion(host: str, port: int) -> int:
    for candidate in range(port + 1, port + 20):
        if port_is_free(host, candidate):
            return candidate
    return port


def open_browser_later(url: str, delay: float = 1.5) -> None:
    def _open() -> None:
        time.sleep(delay)
        try:
            webbrowser.open(url)
        except Exception:  # noqa: BLE001 - браузер не критичен для работы
            pass

    threading.Thread(target=_open, daemon=True).start()


def main() -> int:
    # Без этого print буферизуется, и при перенаправлении вывода в файл
    # сообщения появляются только после завершения процесса.
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except (AttributeError, ValueError):
        pass

    configure_logging()
    host, port = settings.host, settings.port
    base = f"http://{'127.0.0.1' if host in ('0.0.0.0', '127.0.0.1') else host}:{port}"

    print()
    print(f"  {settings.app_name}")
    print("  " + "-" * 40)
    print()

    if not port_is_free(host, port):
        other = free_port_suggestion(host, port)
        return _fail(
            [
                f"Порт {port} уже занят — значит, сервер уже запущен.",
                "",
                f"Откройте {base} и пользуйтесь им.",
                "",
                f"Если нужен другой порт, поменяйте PORT в .env или запустите",
                f"вручную на свободном порту, например {other}:",
                f"  .venv\\Scripts\\python.exe -m uvicorn core.main:app --port {other}",
            ]
        )

    from database.session import SessionLocal, create_all

    create_all()
    print("  База данных готова.")

    with SessionLocal() as session:
        has_admin = ensure_admin_account(session) is not None

    open_browser_later(base)

    print(f"  Сайт:      {base}")
    print(f"  Вход:      {base}/login")
    if has_admin:
        print()
        print("  Создана учётка администратора: логин admin, пароль admin.")
        print("  Пароль стоит сменить: " + f"{base}/account/password")
    print(f"  Остановка: закройте это окно или нажмите Ctrl+C")
    print()
    print("  Сервер запущен. Не закрывайте это окно.")
    print()

    import uvicorn

    try:
        uvicorn.run("core.main:app", host=host, port=port, log_level="warning")
    except KeyboardInterrupt:
        print()
        print("  Остановлено.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
