from __future__ import annotations

import datetime
import logging
import secrets
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Корень проекта — на уровень выше пакета `core`. Раньше модуль лежал в
# корне и `parent` указывал на него самого; после переноса без этой поправки
# пути к `.env`, базе, шаблонам и картинкам уехали бы в `core/`.
BASE_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = BASE_DIR / ".env"
DB_PATH = BASE_DIR / "survey.db"


DEFAULT_SECRET_KEY = "dev-secret-key-change-me-in-production"


def default_database_url() -> str:
    """Путь к базе по умолчанию — всегда от корня проекта.

    Раньше стояло `sqlite:///./survey.db`, а SQLite разрешает такой путь
    относительно рабочей папки процесса, а не корня проекта. Запуск из
    соседнего каталога молча создавал вторую пустую базу, и это выглядело
    как «все анкеты пропали», хотя настоящая база лежала рядом нетронутая.
    `.env` и картинки давно привязаны к `BASE_DIR`, база была последней
    относительной.

    Три слэша перед путём — требование SQLAlchemy, а не украшение: так
    абсолютный путь в POSIX получает четыре слэша, а в Windows с буквой
    диска — три, и в обоих случаях указывает на один и тот же файл.
    Явный `DATABASE_URL` из окружения по-прежнему главнее и берётся как есть.
    """
    return f"sqlite:///{DB_PATH}"


ENV_TEMPLATE = """\
# Файл создан автоматически при первом запуске {date}.
# Править нужно редко: чаще всего хватает этой строки.

# Подпись cookie сессии. Смените, если переносите базу на другой сервер:
# вместе с ключом перестают работать старые cookie.
SECRET_KEY={secret}

# --- Обычно менять не нужно ---

# Путь к базе задан по умолчанию: survey.db рядом с этим файлом.
# Раскомментируйте, только если база лежит в другом месте, и укажите
# ПОЛНЫЙ путь: относительный разрешается от папки запуска, и с другого
# каталога появится вторая пустая база.
# DATABASE_URL=sqlite:///{db_path}

# APP_NAME=ЧЕСТНО
# COOKIE_SECURE=false
# REDIS_URL=
# ALLOW_REGISTRATION=true
"""


def ensure_env_file() -> Path | None:
    """Создаёт `.env` со случайным ключом, если файла ещё нет.

    Без этого приложение стартовало бы на заведомо известном
    `SECRET_KEY`, и старые cookie можно было бы подделать. Идемпотентна:
    существующий файл не трогает, поэтому перезапуск и правки руками не
    перетираются.

    Учётка администратора сюда не попадает намеренно: она создаётся из
    `ADMIN_USERNAME` и `ADMIN_PASSWORD` и живёт в базе.
    """
    if ENV_PATH.exists():
        return None
    ENV_PATH.write_text(
        ENV_TEMPLATE.format(
            date=datetime.date.today().isoformat(),
            secret=secrets.token_urlsafe(48),
            db_path=DB_PATH,
        ),
        encoding="utf-8",
    )
    logging.getLogger(__name__).info("Создан файл настроек: %s", ENV_PATH)
    return ENV_PATH


class Settings(BaseSettings):
    """Конфигурация приложения. Значения читаются из переменных окружения
    и из файла `.env` в корне проекта."""

    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "ЧЕСТНО"
    debug: bool = True
    # Слушаем все интерфейсы, а не только свой компьютер: иначе ссылка на
    # анкету открывается лишь у того, кто её создал, и телефон в той же сети
    # её не видит. В локальной сети это безопасно, а для публикации в
    # интернете всё равно нужен HTTPS перед приложением.
    host: str = "0.0.0.0"
    port: int = 8000

    database_url: str = Field(default_factory=default_database_url)
    secret_key: str = DEFAULT_SECRET_KEY
    cookie_name: str = "session"
    cookie_secure: bool = False
    cookie_max_age: int = 60 * 60 * 24 * 30

    respondent_cookie_name: str = "respondent_id"
    respondent_cookie_max_age: int = 60 * 60 * 24 * 365

    # Cookie, которая на несколько часов прячет окно «пароль стандартный».
    # Флаг в базе при этом остаётся: пользователь выбрал «продолжить»,
    # но не выбрал «не напоминать больше», поэтому напоминание вернётся
    # при следующем входе с другого устройства или позже.
    password_notice_cookie_name: str = "pw_notice_off"
    password_notice_max_age: int = 8 * 60 * 60

    # Логин и пароль девелоперской учётки. Пароль известен по умолчанию,
    # поэтому после входа показывается предупреждение с предложением
    # сменить его; отказ от смены разрешён.
    admin_username: str = "admin"
    admin_password: str = "admin"

    # Регистрация создателей анкет. Выключается на боевом сервере, если
    # аккаунты заводит администратор вручную.
    allow_registration: bool = True

    media_dir_name: str = "media"
    max_upload_bytes: int = 2 * 1024 * 1024

    redis_url: str = ""
    redis_ttl_seconds: int = 30

    slug_length: int = Field(default=8, ge=4, le=24)

    rate_limit_create_per_hour: int = 20
    rate_limit_submit_per_minute: int = 10
    rate_limit_page_per_minute: int = 300

    @field_validator("redis_url")
    @classmethod
    def _strip_redis_url(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="after")
    def _refuse_known_secret_in_production(self) -> "Settings":
        """Боевой режим с ключом из учебных материалов — отказ, а не запуск.

        Ключ подписывает все cookie сессии и подсчёт респондентов, поэтому
        значение из `ENV_TEMPLATE` и `.env.example` известно любому, кто
        открыл репозиторий: с ним можно подделать чужую сессию. В режиме
        отладки всё оставлено как было — иначе `start.bat` перестал бы
        работать из коробки.
        """
        if self.is_production and self.secret_key == DEFAULT_SECRET_KEY:
            raise ValueError(
                "SECRET_KEY остался ключом по умолчанию. Подпись всех cookie "
                "им обесценивается, поэтому боевой запуск с ним невозможен. "
                "Задайте свой: "
                'python -c "import secrets; print(secrets.token_urlsafe(48))"'
            )
        return self

    @property
    def is_production(self) -> bool:
        return not self.debug

    @property
    def redis_enabled(self) -> bool:
        return bool(self.redis_url)

    @property
    def media_dir(self) -> Path:
        return BASE_DIR / self.media_dir_name


@lru_cache
def get_settings() -> Settings:
    ensure_env_file()
    return Settings()


settings = get_settings()


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.DEBUG if settings.debug else logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
