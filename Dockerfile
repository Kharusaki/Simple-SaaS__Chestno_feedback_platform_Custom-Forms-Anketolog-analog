# Синтаксис для сборки образа: pip install -r requirements.txt
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Приложение работает не от root. Папки создаются и передаются ему заранее:
# Docker создаёт именованный том от содержимого каталога образа вместе с его
# владельцем, поэтому том, созданный позже, уже будет доступен приложению.
# Иначе свежий том достался бы root, и непривилегированный процесс не смог
# бы ни записать базу, ни принять загруженную картинку.
RUN groupadd --system app \
 && useradd --system --gid app --home-dir /app --shell /usr/sbin/nologin app \
 && mkdir -p /data /app/media \
 && chown -R app:app /app /data

# БД и картинки оформления живут на volume, чтобы переживать пересборку образа.
# MEDIA_DIR_NAME — папка внутри проекта, поэтому путь в контейнере /app/media.
ENV DATABASE_URL=sqlite:////data/survey.db
VOLUME ["/data", "/app/media"]

USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"

# Точка входа лежит в пакете `core/`: корень проекта содержит только
# README, файлы запуска и базу, поэтому путь указывает на `core.main`.
CMD ["python", "-m", "uvicorn", "core.main:app", "--host", "0.0.0.0", "--port", "8000"]
