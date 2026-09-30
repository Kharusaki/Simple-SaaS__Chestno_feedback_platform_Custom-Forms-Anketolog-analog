"""Живой сквозной smoke: настоящий HTTP-сервер, настоящие запросы.

Запуск:
    python -m scripts.smoke
    python -m scripts.smoke --url http://127.0.0.1:8000   # по внешнему серверу
"""

from __future__ import annotations

import argparse
import os
import re
import socket
import sys
import threading
import time
from pathlib import Path

import httpx

PASSED: list[str] = []
FAILED: list[str] = []

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _option_selected(html: str, value: str) -> bool:
    """Отмечен ли `<option>` с таким значением.

    Разметка `<option>` менялась (появились подписи и `data-google`), поэтому
    отбор проверяется по признаку `selected` внутри тега, а не по точной
    подстроке: иначе любая правка разметки роняла бы проверку, ничего не
    говоря о приложении.
    """
    for tag in re.findall(r"<option\b[^>]*>", html):
        if f'value="{value}"' in tag and "selected" in tag:
            return True
    return False


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


def _form_fields(html: str) -> dict[str, str]:
    """Позиция вопроса (`q1`, `q2`, …) -> настоящее имя поля ответа.

    У `fieldset` в разметке стоит `id="q<id вопроса в базе>"`, поэтому он не
    равен позиции: id продолжаются сквозь анкеты и на непустой базе начинаются
    не с единицы. Позицию берём из порядка следования, а не из id.
    """
    mapping: dict[str, str] = {}
    for position, block in enumerate(
        re.findall(r"<fieldset[^>]*id=\"(q\d+)\"(.*?)</fieldset>", html, re.S), start=1
    ):
        _, body = block
        names = re.findall(r"name=\"(q\d+)\"", body)
        if names:
            mapping[f"q{position}"] = names[0]
    return mapping


def _serve(port: int, db_path: Path) -> "uvicorn.Server":
    import uvicorn

    os.environ["DATABASE_URL"] = f"sqlite:///{db_path.as_posix()}"
    # Лимиты запросов адресуются по IP, а весь smoke идёт с одного адреса, и
    # два прогона подряд упёрлись бы в лимит на середине сценария. Проверять
    # надо логику приложения, а не счётчик запросов.
    os.environ["RATE_LIMIT_SUBMIT_PER_MINUTE"] = "0"
    os.environ["RATE_LIMIT_CREATE_PER_HOUR"] = "0"
    os.environ["RATE_LIMIT_PAGE_PER_MINUTE"] = "0"

    from core.main import app

    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
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


def run(base: str) -> None:
    owner = httpx.Client(base_url=base, timeout=15.0, follow_redirects=True)
    guest = httpx.Client(base_url=base, timeout=15.0, follow_redirects=True)

    print("0. Вход создателя анкет")
    r = guest.get("/dashboard", follow_redirects=False)
    check("дашборд закрыт гостю", r.status_code == 303, f"статус {r.status_code}")
    check("гостя зовёт форма входа", r.headers.get("location", "").startswith("/login?next="),
          r.headers.get("location", ""))
    # Логин счётный, чтобы прогон по рабочей базе не спотыкался о занятый логин.
    creator_login = f"smoke-{int(time.time()) % 100000}"
    r = owner.post(
        "/register",
        data={
            "username": creator_login,
            "password": "smoke-secret",
            "password_repeat": "smoke-secret",
        },
        follow_redirects=False,
    )
    check("регистрация создателя", r.status_code == 303, f"статус {r.status_code}")
    r = owner.get("/dashboard")
    check("дашборд открывается по своей сессии", r.status_code == 200)
    r = guest.get("/dashboard", follow_redirects=False)
    check("чужая сессия не открывает чужой дашборд", r.status_code == 303,
          f"статус {r.status_code}")

    print("1. Создание анкеты со всеми типами вопросов")
    form = {
        "title": ["Дымовой прогон"],
        "description": ["Проверка перед сдачей"],
        "theme": ["sand"],
        "font": ["serif"],
        "q1_text": ["Один вариант"], "q1_type": ["single"], "q1_required": ["1"],
        "q1_choices": ["Да\nНет"],
        "q2_text": ["Несколько вариантов"], "q2_type": ["multiple"], "q2_required": ["1"],
        "q2_choices": ["Скорость\nУдобство"],
        "q3_text": ["Да или нет"], "q3_type": ["yes_no"], "q3_required": ["1"],
        "q4_text": ["Шкала"], "q4_type": ["scale"], "q4_required": ["1"],
        "q4_scale": ["1-10"],
        "q5_text": ["Комментарий"], "q5_type": ["text"], "q5_required": ["0"],
    }
    r = owner.get("/surveys/new")
    check("в форме создания есть выбор темы и шрифта",
          'name="theme"' in r.text and 'name="font"' in r.text)
    check("в форме создания есть живое превью",
          "data-preview" in r.text and "data-appearance-catalog" in r.text)
    r = owner.post("/surveys/new", data=form, follow_redirects=False)
    check("анкета создана", r.status_code == 303, f"статус {r.status_code}")
    location = r.headers.get("location", "")
    # После создания открывается сразу редактор, поэтому slug берём из адреса
    # `/s/<slug>/edit?created=1`, а не со страницы списка.
    slug = location.split("/s/")[-1].split("/")[0] if "/s/" in location else ""
    check("редирект ведёт в редактор со slug", bool(slug), location)
    r = owner.get(location)
    check("редактор сразу доступен", r.status_code == 200 and "Редактирование анкеты" in r.text,
          f"статус {r.status_code}")
    check("тема и шрифт сохранились вместе с анкетой",
          _option_selected(r.text, "sand") and _option_selected(r.text, "serif"))

    print("1а. Оформление применилось к публичной странице")
    r = guest.get(f"/s/{slug}")
    # Значения едут в атрибут style, поэтому сравниваем по переменным, а не
    # по строке целиком: кавычки внутри названия шрифта экранирует Jinja.
    check("публичная страница отдаёт выбранный шрифт",
          "--font-body:Georgia" in r.text, "")
    check("публичная страница отдаёт выбранную тему",
          "--font-body:Georgia" in r.text and "themed" in r.text, "")

    print("1в. Google Fonts и единое меню кастомизации")
    r = owner.get("/surveys/new")
    check("в меню есть группа Google Fonts", 'data-google="Manrope"' in r.text, "")
    check("системные шрифты помечены как локальные", 'data-google=""' in r.text, "")
    check("в меню есть пипетка подложки",
          'data-color-picker="bg_color"' in r.text
          and 'data-color-text="bg_color"' in r.text, "")
    check("в меню есть пипетка акцента и текста",
          'data-color-picker="accent_color"' in r.text
          and 'data-color-picker="ink_color"' in r.text, "")
    r = owner.get(f"/s/{slug}/edit")
    check("редактор показывает то же меню", "data-appearance" in r.text
          and 'data-color-text="bg_color"' in r.text, "")
    check("подсказка про сеть у Google Fonts честная",
          "системном шрифте" in r.text or "Google Fonts" in r.text, "")

    print("1г. Свои цвета анкеты")
    r = owner.post(
        f"/s/{slug}/appearance",
        data={
            "theme": ["light"],
            "font": ["manrope"],
            "bg_color": ["#101014"],
            "accent_color": ["#ff0066"],
            "ink_color": [""],
        },
    )
    check("цвета сохранены без ошибки", r.status_code == 200,
          f"статус {r.status_code}")
    r = guest.get(f"/s/{slug}")
    check("подложка применена к публичной странице", "--bg:#101014" in r.text, "")
    check("текст пересчитан под тёмную подложку", "--text:#ffffff" in r.text, "")
    check("акцент применён", "--signal:#ff0066" in r.text, "")
    check("карточки не остались белыми на тёмном фоне",
          "--surface:#232327" in r.text, "")
    check("веб-шрифт подключён на публичной странице",
          "fonts.googleapis.com/css2" in r.text, "")

    r = owner.post(
        f"/s/{slug}/appearance",
        data={
            "theme": ["light"],
            "font": ["system"],
            "bg_color": ["#fff"],
            "accent_color": ["мусор"],
            "ink_color": [""],
        },
    )
    check("короткий hex разворачивается в полный",
          'value="#ffffff"' in r.text, "")
    check("мусорный цвет не попал в анкету",
          'value="мусор"' not in r.text, "")
    r = guest.get(f"/s/{slug}")
    check("короткая запись вернулась как полная", "--bg:#ffffff" in r.text, "")
    check("мусорный акцент не применён", "#ff0066" not in r.text, "")

    r = owner.post(
        f"/s/{slug}/appearance",
        data={
            "theme": ["light"],
            "font": ["system"],
            "bg_color": [""],
            "accent_color": [""],
            "ink_color": [""],
        },
    )
    r = guest.get(f"/s/{slug}")
    check("пустой цвет вернул цвета темы", "--bg:#f6f7fb" in r.text, "")

    print("1д. Вопрос не висит на рамке")
    r = guest.get(f"/s/{slug}")
    check("легенда вопроса отвязана от рамки",
          "question-legend" in r.text and "fieldset" in r.text, "")

    print("1б. Обложка анкеты видна в шапке публичной страницы")
    # Обложку грузим через редактор: раньше в `src` попадал весь объект
    # `ImageView(...)` вместо адреса, и картинка не показывалась. Проверяем
    # точное значение атрибута и что по нему реально отдаётся картинка.
    png = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08"
        b"\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00"
        b"\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    r = owner.post(
        f"/s/{slug}/cover", files={"cover": ("cover.png", png, "image/png")}
    )
    check("обложка загрузилась", r.status_code == 200, f"статус {r.status_code}")
    r = guest.get(f"/s/{slug}")
    sources = re.findall(r'<img[^>]*class="survey-cover"[^>]*src="([^"]*)"', r.text)
    src = sources[0] if sources else ""
    check("в шапке стоит адрес картинки, а не объект", src.startswith("/media/"), src)
    media = guest.get(src) if src else None
    check("картинка по адресу отдаётся", media is not None and media.status_code == 200
          and media.headers.get("content-type") == "image/png",
          f"статус {media.status_code if media is not None else '—'}")

    print("2. Прохождение без cookie")
    r = guest.get(f"/s/{slug}")
    check("публичная страница открыта", r.status_code == 200)

    # Имена полей — это `q<id вопроса>`, а не позиция в анкете. Гадать нельзя:
    # в базе id продолжаются сквозь анкеты, и ответ молча уйдёт в пустоту.
    fields = _form_fields(r.text)
    check("у формы пять вопросов", len(fields) == 5, f"найдено {len(fields)}: {fields}")
    if len(fields) != 5:
        return
    single, multiple, yes_no, scale, text = (fields[f"q{i}"] for i in range(1, 6))
    answers = [
        {single: ["Да"], multiple: ["Скорость", "Удобство"], yes_no: ["Да"],
         scale: "9", text: ["быстро и удобно, рекомендую"]},
        {single: ["Нет"], multiple: ["Скорость"], yes_no: ["Нет"],
         scale: "3", text: ["удобно"]},
    ]
    # Анти-дубль считается по IP + User-Agent, поэтому каждый респондент
    # должен выглядеть отдельным устройством.
    devices = [
        httpx.Client(base_url=base, timeout=15.0, headers={"user-agent": f"smoke-device-{i}"})
        for i in range(len(answers) + 1)
    ]
    codes = [devices[i].post(f"/s/{slug}", data=payload, follow_redirects=False).status_code
             for i, payload in enumerate(answers)]
    check("оба ответа приняты", codes == [303, 303], f"коды {codes}")
    thanks = devices[0].get(f"/s/{slug}/thanks")
    check("страница благодарности", thanks.status_code == 200 and "Спасибо" in thanks.text)

    print("3. Анти-дубль")
    dup = devices[0].post(f"/s/{slug}", data=answers[0], follow_redirects=False)
    check("повтор с того же устройства отбит", dup.status_code == 409, f"статус {dup.status_code}")
    for device in devices:
        device.close()

    print("4. Заморозка структуры после ответа")
    r = owner.get(f"/s/{slug}/edit")
    check("форма правки доступна владельцу", r.status_code == 200)
    r = owner.post(f"/s/{slug}/edit", data={**form, "title": ["Дымовой прогон 2"], "is_open": ["1"]},
                   follow_redirects=False)
    check("название поменялось", r.status_code == 303, f"статус {r.status_code}")

    print("5. Статистика только по ключу")
    stranger = guest.get(f"/s/{slug}/stats")
    check("чужой не видит статистику", stranger.status_code in (403, 404), f"статус {stranger.status_code}")
    check("чужой не видит CSV", guest.get(f"/s/{slug}/stats.csv").status_code in (403, 404))

    settings = owner.get(f"/s/{slug}/settings")
    tokens = list(dict.fromkeys(re.findall(r"/k/([A-Za-z0-9]{48})", settings.text)))
    check("в настройках есть основной ключ", len(tokens) == 1, f"найдено {len(tokens)}")
    if not tokens:
        return
    key = tokens[0]

    stats = guest.get(f"/s/{slug}/stats?key={key}")
    check("статистика по ключу открыта", stats.status_code == 200)
    for marker, label in (
        ("Дымовой прогон", "название анкеты в шапке"),
        ("tag-cloud", "облако слов"),
        ("быстро", "слово из свободного ответа в облаке"),
        ("Да", "вариант ответа в распределении"),
    ):
        check(f"на странице есть: {label}", marker in stats.text)
    check("счётчик ответов показывает оба ответа",
          bool(re.search(r"2\s+отв?е?т?а?", stats.text)), "ищем «2 ответа»")

    csv = guest.get(f"/s/{slug}/stats.csv?key={key}")
    check("CSV отдаётся", csv.status_code == 200 and "text/csv" in csv.headers.get("content-type", ""))
    check("CSV содержит заголовок вопроса", "Один вариант" in csv.text)

    print("6. Кэш: повторный запрос не ломает страницу")
    second = guest.get(f"/s/{slug}/stats?key={key}")
    check("статистика из кэша отдаётся", second.status_code == 200 and "tag-cloud" in second.text)

    print("7. Ключи: выпуск, права, отзыв")
    settings = owner.get(f"/s/{slug}/settings")
    check("страница настроек открыта", settings.status_code == 200)
    r = owner.post(f"/s/{slug}/keys", data={"label": "для коллеги"}, follow_redirects=False)
    check("выпущен дополнительный ключ", r.status_code == 303, f"статус {r.status_code}")
    r = owner.post(f"/s/{slug}/keys", data={"label": "редактор", "can_edit": "1"}, follow_redirects=False)
    check("выпущен ключ с правом правки", r.status_code == 303, f"статус {r.status_code}")

    settings = owner.get(f"/s/{slug}/settings")
    tokens = list(dict.fromkeys(re.findall(r"/k/([A-Za-z0-9]{48})", settings.text)))
    check("в настройках видны три ключа", len(tokens) == 3, f"найдено {len(tokens)}")
    rows = re.findall(r"/s/" + re.escape(slug) + r"/keys/(\d+)/revoke", settings.text)
    check("основной ключ нельзя отозвать, два остальных можно", len(rows) == 2,
          f"кнопок отзыва {len(rows)}")

    extra = [t for t in tokens if t != key]
    editor_token = extra[1] if len(extra) > 1 else ""
    read_only = extra[0] if extra else ""
    if editor_token:
        r = guest.get(f"/k/{editor_token}", follow_redirects=False)
        check("вход по ключу-редактору ведёт в настройки", "/settings" in r.headers.get("location", ""))
        r = guest.get(f"/s/{slug}/edit?key={editor_token}")
        check("ключ-редактор открывает правку", r.status_code == 200)
        r = guest.post(f"/s/{slug}/edit", data={**form, "key": [editor_token]},
                       follow_redirects=False)
        check("правка ключом сохраняется", r.status_code == 303, f"статус {r.status_code}")
        r = guest.get(f"/s/{slug}/settings?key={editor_token}")
        check("ключ-редактор не видит выпуск новых ключей", "Название ключа" not in r.text)
        # Ключ даёт правку текста, но не управление: приём и выпуск ключей
        # остаются за учётной записью владельца.
        for path, payload, label in (
            ("/open", {}, "остановить приём"),
            ("/keys", {"label": "вор"}, "выпускать ключи"),
        ):
            r = guest.post(f"/s/{slug}{path}", data=payload, follow_redirects=False)
            check(f"ключ-редактор не может {label}",
                  r.status_code == 303 and r.headers.get("location", "").startswith("/login?next="),
                  f"статус {r.status_code} {r.headers.get('location', '')}")
        check('чекбокс приёма скрыт у ключа-редактора',
              'name="is_open"' not in guest.get(f"/s/{slug}/edit?key={editor_token}").text)

    if read_only:
        r = guest.get(f"/k/{read_only}", follow_redirects=False)
        check("обычный ключ ведёт в статистику", "/stats" in r.headers.get("location", ""))
        r = guest.get(f"/s/{slug}/edit?key={read_only}")
        check("обычный ключ не открывает правку", r.status_code == 404, f"статус {r.status_code}")

    print("8. Отзыв ключа с правом правки")
    settings = owner.get(f"/s/{slug}/settings")
    rows = re.findall(r"/s/" + re.escape(slug) + r"/keys/(\d+)/revoke", settings.text)
    if editor_token and rows:
        r = owner.post(f"/s/{slug}/keys/{rows[-1]}/revoke", follow_redirects=False)
        check("ключ-редактор отозван", r.status_code == 303, f"статус {r.status_code}")
        r = guest.get(f"/k/{editor_token}")
        check("отозванный ключ больше не работает", r.status_code == 404, f"статус {r.status_code}")
        check("основной ключ пережил отзыв",
              owner.get(f"/s/{slug}/stats?key={key}").status_code == 200)

    print("9. Зарезервированная метка")
    r = owner.post(f"/s/{slug}/keys", data={"label": "основной доступ"}, follow_redirects=False)
    check("метка основного ключа занята", r.status_code == 400, f"статус {r.status_code}")

    print("10. Приём ответов: пауза и возобновление")
    r = owner.post(f"/s/{slug}/open", data={}, follow_redirects=False)
    check("владелец остановил приём", r.status_code == 303, f"статус {r.status_code}")
    r = guest.get(f"/s/{slug}")
    check("закрытая анкета не пускает респондента",
          r.status_code == 403 or (r.status_code == 200 and "закрыт" in r.text.lower()),
          f"статус {r.status_code}")
    r = owner.post(f"/s/{slug}/open", data={}, follow_redirects=False)
    check("приём возобновлён", r.status_code == 303, f"статус {r.status_code}")
    r = guest.get(f"/s/{slug}")
    check("после возобновления анкета снова открыта",
          r.status_code == 200 and "закрыт" not in r.text.lower(), f"статус {r.status_code}")

    print("11. Приватность респондента")
    # Отдельный клиент-респондент: учётки у него нет и не заводится.
    fresh = httpx.Client(base_url=base, timeout=15.0, headers={"user-agent": "smoke-privacy"})
    r = fresh.post(f"/s/{slug}", data=answers[0], follow_redirects=False)
    check("новый респондент принят", r.status_code == 303, f"статус {r.status_code}")
    r = fresh.get(f"/s/{slug}")
    check("после ответа баннер виден сразу", r.status_code == 200 and "Вы уже заполнили" in r.text)
    check("в навигации нет «Мои анкеты»", ">Мои анкеты<" not in r.text)
    check("в навигации есть «Мои ответы»", "Мои ответы" in r.text)
    r = fresh.get("/my")
    check("«Мои ответы» открывается", r.status_code == 200 and "Дымовой прогон" in r.text)
    r = fresh.get("/dashboard", follow_redirects=False)
    check("респонденту кабинет недоступен",
          r.status_code == 303 and r.headers.get("location", "").startswith("/login?next="),
          f"статус {r.status_code} {r.headers.get('location', '')}")
    r = fresh.get(f"/s/{slug}/stats")
    check("респондент не видит статистику", r.status_code in (403, 404), f"статус {r.status_code}")
    r = fresh.get(f"/s/{slug}/settings")
    check("респондент не видит настройки анкеты", r.status_code == 404, f"статус {r.status_code}")
    fresh.close()

    print("12. Служебные маршруты")
    check("/health отвечает", owner.get("/health").json().get("status") == "ok")
    redis_health = owner.get("/health/redis").json()
    check("/health/redis отвечает", redis_health.get("backend") in ("memory", "redis"),
          str(redis_health))
    # Redis в compose есть, при запуске без него должен быть фолбэк.
    # Проверяем инвариант, а не конкретный бэкенд: настроен и доступен ->
    # работаем через Redis, не настроен -> живём на памяти. Поле `available`
    # не годится: память «доступна» всегда.
    expected = (
        "redis"
        if redis_health.get("configured") and redis_health.get("available")
        else "memory"
    )
    check(f"бэкенд соответствует окружению ({expected})",
          redis_health.get("backend") == expected, str(redis_health))
    # Схема API описывает закрытые процессы, поэтому наружу не отдаётся.
    check("публичная документация закрыта",
          all(owner.get(p).status_code == 404 for p in ("/docs", "/redoc", "/openapi.json")))

    print("13. Раздел администратора")
    mod = httpx.Client(base_url=base, timeout=15.0, follow_redirects=True)
    # Пароль администратора задаёт оператор через ADMIN_PASSWORD. Проверяем
    # инвариант, а не конкретное значение: не задан пароль — вход с паролем
    # по умолчанию не работает, и это не должно ломать прогон.
    admin_login = os.environ.get("ADMIN_USERNAME", "").strip() or "admin"
    admin_password = os.environ.get("ADMIN_PASSWORD", "").strip()
    for path in ("", "/appearance", "/instructions", "/openapi.json"):
        r = mod.get(f"/admin{path}", follow_redirects=False)
        check(f"гостю админ-раздел недоступен: {path or '/admin'}",
              r.status_code == 303 and r.headers.get("location", "").startswith("/login?next="),
              f"статус {r.status_code}")
    # Страница-обёртка схемы удалена: теперь 404 и гостю, и администратору.
    r = owner.get("/admin/api", follow_redirects=False)
    check("удалённая /admin/api отдаёт 404", r.status_code == 404, f"статус {r.status_code}")
    check("создателю админ-раздел скрыт (404)", owner.get("/admin").status_code == 404)
    if admin_password:
        r = mod.post(
            "/login",
            data={"username": admin_login, "password": admin_password},
            follow_redirects=False,
        )
        check("вход администратора", r.status_code == 303, f"статус {r.status_code}")
        r = mod.get("/admin")
        check("главная админки открыта", r.status_code == 200 and "Оформление сайта" in r.text)
        r = mod.get("/admin/appearance")
        check("форма загрузки фона есть", 'name="background"' in r.text)
        check("форма загрузки логотипа есть", 'name="logo"' in r.text)
        check("обложек чужих анкет в оформлении нет", "Обложки анкет" not in r.text)
        check("схема API отдаётся администратору",
              mod.get("/admin/openapi.json").status_code == 200)
    else:
        r = mod.post(
            "/login",
            data={"username": admin_login, "password": "заведомо-неверный"},
            follow_redirects=False,
        )
        check("неверный пароль администратора отбит", r.status_code == 401, f"статус {r.status_code}")
    mod.close()

    print("14. Встроенный шаблон «Тест знаний» и проверка ответов")
    r = owner.get("/surveys/new")
    check("страница создания предлагает шаблоны", "preset=knowledge" in r.text)
    r = owner.get("/surveys/new?preset=knowledge")
    check("шаблон раскрылся в форму", 'name="q4_text"' in r.text and 'name="q5_text"' not in r.text)
    check("в шаблоне уже выбран режим проверки", 'value="explain" selected' in r.text)

    # Отправляем ровно те поля, которые рисует форма: правильные ответы едут
    # скрытым полем `correct`, а не галочками.
    quiz = {
        "title": ["Тест знаний"],
        "description": ["Дымовая проверка"],
        "review_mode": ["explain"],
        "q1_type": ["single"], "q1_text": ["2 + 2?"], "q1_required": ["1"],
        "q1_choices": ["4\n5"], "q1_correct": ["4"],
        "q1_explanation": ["Сложение двух двоек даёт четыре."],
        "q2_type": ["yes_no"], "q2_text": ["Земля круглая?"], "q2_required": ["1"],
        "q2_correct_yes_no": ["Да"],
    }
    r = owner.post("/surveys/new", data=quiz, follow_redirects=False)
    check("анкета по шаблону создана", r.status_code == 303, f"статус {r.status_code}")
    quiz_slug = r.headers.get("location", "").split("/s/")[-1].split("/")[0]

    r = guest.get(f"/s/{quiz_slug}")
    check("тест открыт для участника", r.status_code == 200)
    check("правильный ответ не торчит в анкете", "Сложение двух двоек" not in r.text)
    quiz_fields = _form_fields(r.text)
    r = guest.post(
        f"/s/{quiz_slug}",
        data={quiz_fields["q1"]: "4", quiz_fields["q2"]: "Да"},
        follow_redirects=True,
    )
    check("после ответа показан балл", "2" in r.text)
    check("показан разбор с пояснением", "Сложение двух двоек" in r.text)

    r = guest.post(
        f"/s/{quiz_slug}",
        data={quiz_fields["q1"]: "5", quiz_fields["q2"]: "Нет"},
        follow_redirects=True,
    )
    check("за неверные ответы балл ниже", "0" in r.text)
    owner.post(f"/s/{quiz_slug}/delete", follow_redirects=False)

    print("15. Удаление и выход")
    r = owner.post(f"/s/{slug}/delete", follow_redirects=False)
    check("удаление выполнено", r.status_code == 303, f"статус {r.status_code}")
    check("анкета исчезла", guest.get(f"/s/{slug}").status_code == 404)
    check("ключи анкеты не работают", guest.get(f"/k/{key}").status_code == 404)

    r = owner.post("/logout", follow_redirects=False)
    check("выход работает", r.status_code == 303)
    r = httpx.get(f"{base}/dashboard", timeout=15.0, follow_redirects=False)
    check("после выхода кабинет снова закрыт",
          r.status_code == 303 and r.headers.get("Location", "").startswith("/login?next="),
          f"статус {r.status_code}")

    owner.close()
    guest.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Живой сквозной smoke")
    parser.add_argument("--url", help="базовый URL внешнего сервера")
    args = parser.parse_args(argv)

    if args.url:
        base = args.url.rstrip("/")
        own_db: Path | None = None
        server = None
    else:
        # Своя база: smoke создаёт анкеты и юзеров, и в рабочей `survey.db`
        # они копились бы после каждого прогона.
        own_db = PROJECT_ROOT / "_smoke.db"
        _drop_db(own_db)
        port = _free_port()
        server = _serve(port, own_db)
        base = f"http://127.0.0.1:{port}"

    print(f"Smoke против {base}\n")
    try:
        run(base)
    finally:
        if server is not None:
            _stop(server)
        if own_db is not None:
            try:
                from database.session import engine

                engine.dispose()
            except Exception:  # noqa: BLE001 - уборка не должна ломать прогон
                pass
            _drop_db(own_db)

    total = len(PASSED) + len(FAILED)
    print(f"\nИтог: {len(PASSED)} из {total} проверок прошли")
    if FAILED:
        print("Не прошли:")
        for item in FAILED:
            print(f"  - {item}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
