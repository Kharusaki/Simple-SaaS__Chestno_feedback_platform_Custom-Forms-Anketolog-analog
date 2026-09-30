"""SQLAlchemy-модели проекта. Слой данных: ничего не знает про HTTP."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database.base import Base
from .themes import DEFAULT_FONT, DEFAULT_THEME
from .utils import utcnow

QUESTION_TYPES = ("single", "multiple", "yes_no", "scale", "text")
CHOICE_TYPES = ("single", "multiple")
SCALE_TYPES = ("scale",)
ANSWER_SKIPPED = "skipped"
YES_CHOICE = "Да"
NO_CHOICE = "Нет"
YES_NO_CHOICES = (YES_CHOICE, NO_CHOICE)

# Режим проверки ответов — один на всю анкету, а не на вопрос.
#   none   — обычный опрос, респонденту показывают только «спасибо»;
#   score  — после отправки показывают набранный балл;
#   explain — балл плюс правильные ответы с пояснениями.
REVIEW_NONE = "none"
REVIEW_SCORE = "score"
REVIEW_EXPLAIN = "explain"
REVIEW_MODES = (REVIEW_NONE, REVIEW_SCORE, REVIEW_EXPLAIN)
REVIEW_SCORED_MODES = (REVIEW_SCORE, REVIEW_EXPLAIN)

ROLE_ADMIN = "admin"
ROLE_CREATOR = "creator"
USER_ROLES = (ROLE_ADMIN, ROLE_CREATOR)


class User(Base):
    """Аккаунт, который может создавать и править анкеты.

    Регистрация добровольная и только для создателя анкет: респондент
    отвечает по публичной ссылке и в `users` не попадает вовсе.

    `must_change_password` живёт для девелоперской учётки `admin/admin`:
    пароль известен всем, поэтому при входе показывается предупреждение.
    Пользователь может его закрыть и продолжить работу, флаг при этом
    сбрасывается, чтобы напоминание не возвращалось на каждой странице.

    `password_changed_at` — момент последней смены пароля. Cookie сессии
    подписана, но не хранится на сервере, поэтому сама по себе она
    переживает смену пароля: старый cookie на чужом устройстве продолжал бы
    работать ещё месяц. Этот столбец — момент, раньше которого cookie
    считается выданной до смены пароля и больше не принимается. Пустое
    значение означает «пароль не меняли», и такие cookie действительны:
    иначе после переноса базы на новую версию всех бы залогинило из
    одного только нового столбца.
    """

    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("role IN ('admin','creator')", name="ck_users_role"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False, default=ROLE_CREATOR)
    must_change_password: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    password_changed_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow, server_default=None
    )

    surveys: Mapped[list[Survey]] = relationship(
        back_populates="owner", cascade="all, delete-orphan", passive_deletes=True
    )

    @property
    def is_admin(self) -> bool:
        return self.role == ROLE_ADMIN

    def __repr__(self) -> str:
        return f"<User id={self.id} username={self.username!r} role={self.role!r}>"


class Survey(Base):
    """Опрос. Публичная ссылка — /s/{slug}, статистика закрыта ключом."""

    __tablename__ = "surveys"
    __table_args__ = (
        CheckConstraint(
            "review_mode IN ('none','score','explain')", name="ck_surveys_review_mode"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(16), nullable=False, unique=True, index=True)
    owner_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    cover_image: Mapped[str | None] = mapped_column(String(200), nullable=True)
    theme: Mapped[str] = mapped_column(String(32), nullable=False, default=DEFAULT_THEME)
    font: Mapped[str] = mapped_column(String(32), nullable=False, default=DEFAULT_FONT)
    bg_color: Mapped[str | None] = mapped_column(String(7), nullable=True)
    ink_color: Mapped[str | None] = mapped_column(String(7), nullable=True)
    accent_color: Mapped[str | None] = mapped_column(String(7), nullable=True)
    is_open: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    review_mode: Mapped[str] = mapped_column(String(16), nullable=False, default=REVIEW_NONE)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow, server_default=None
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow, onupdate=utcnow, server_default=None
    )

    owner: Mapped[User] = relationship(back_populates="surveys")
    questions: Mapped[list[Question]] = relationship(
        back_populates="survey",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="Question.position",
    )
    responses: Mapped[list[Response]] = relationship(
        back_populates="survey", cascade="all, delete-orphan", passive_deletes=True
    )
    keys: Mapped[list[SurveyKey]] = relationship(
        back_populates="survey", cascade="all, delete-orphan", passive_deletes=True
    )

    @property
    def is_empty(self) -> bool:
        return not self.questions

    @property
    def reviews_answers(self) -> bool:
        """Показывать ли респонденту результат после отправки."""
        return self.review_mode in REVIEW_SCORED_MODES

    def __repr__(self) -> str:
        return f"<Survey id={self.id} slug={self.slug!r}>"


class Question(Base):
    """Вопрос опроса. Варианты ответов лежат в `choices`."""

    __tablename__ = "questions"
    __table_args__ = (
        CheckConstraint(
            "type IN ('single','multiple','yes_no','scale','text')", name="ck_questions_type"
        ),
        CheckConstraint("scale_min IS NULL OR scale_max IS NULL OR scale_max > scale_min",
                        name="ck_questions_scale_range"),
        Index("ix_questions_survey_position", "survey_id", "position"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    survey_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("surveys.id", ondelete="CASCADE"), nullable=False, index=True
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    text: Mapped[str] = mapped_column(String(500), nullable=False)
    type: Mapped[str] = mapped_column(String(16), nullable=False)
    is_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    image: Mapped[str | None] = mapped_column(String(200), nullable=True)
    scale_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    scale_max: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Проверка ответов. Пояснение показывается только в режиме `explain`,
    # а верный ответ хранится там, где он и живёт: у `single`/`multiple` —
    # флагом на варианте, у `yes_no` и `scale` — отдельной колонкой.
    explanation: Mapped[str | None] = mapped_column(Text, nullable=True)
    correct_value_text: Mapped[str | None] = mapped_column(String(300), nullable=True)
    correct_value_int: Mapped[int | None] = mapped_column(Integer, nullable=True)

    survey: Mapped[Survey] = relationship(back_populates="questions")
    choices: Mapped[list[Choice]] = relationship(
        back_populates="question",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="Choice.position",
    )
    answers: Mapped[list[Answer]] = relationship(
        back_populates="question", cascade="all, delete-orphan", passive_deletes=True
    )

    @property
    def needs_choices(self) -> bool:
        return self.type in CHOICE_TYPES

    @property
    def is_scale(self) -> bool:
        return self.type in SCALE_TYPES

    @property
    def is_gradable(self) -> bool:
        """Можно ли поставить вопрос в зачёт.

        Свободный текст не оценивается: сравнивать его с «правильным» —
        значит врать респонденту про его балл.
        """
        return self.type != "text"

    @property
    def correct_choices(self) -> list[Choice]:
        return [choice for choice in self.choices if choice.is_correct]

    def correct_display(self) -> str:
        """Правильный ответ одной строкой — для подсказки и результата."""
        if self.type in CHOICE_TYPES:
            return ", ".join(choice.text for choice in self.correct_choices)
        if self.type == "yes_no":
            return self.correct_value_text or ""
        if self.type == "scale":
            return "" if self.correct_value_int is None else str(self.correct_value_int)
        return ""

    def has_correct_answer(self) -> bool:
        return bool(self.correct_display())

    def __repr__(self) -> str:
        return f"<Question id={self.id} type={self.type!r}>"


class Choice(Base):
    """Вариант ответа для вопросов типа single/multiple."""

    __tablename__ = "choices"
    __table_args__ = (Index("ix_choices_question_position", "question_id", "position"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    question_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("questions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    text: Mapped[str] = mapped_column(String(300), nullable=False)
    is_correct: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    question: Mapped[Question] = relationship(back_populates="choices")

    def __repr__(self) -> str:
        return f"<Choice id={self.id} position={self.position}>"


class Response(Base):
    """Одна сессия прохождения опроса. `submitted_at is None` — брошенный."""

    __tablename__ = "responses"
    __table_args__ = (Index("ix_responses_survey_submitted", "survey_id", "submitted_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    survey_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("surveys.id", ondelete="CASCADE"), nullable=False, index=True
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow, server_default=None
    )
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    respondent_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    respondent_token: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    survey: Mapped[Survey] = relationship(back_populates="responses")
    answers: Mapped[list[Answer]] = relationship(
        back_populates="response", cascade="all, delete-orphan", passive_deletes=True
    )

    @property
    def is_submitted(self) -> bool:
        return self.submitted_at is not None

    def __repr__(self) -> str:
        return f"<Response id={self.id} submitted={self.is_submitted}>"


class Answer(Base):
    """Один ответ на один вопрос.

    kind = тип вопроса (single|multiple|yes_no|scale|text) либо 'skipped'.
    Для multiple создаётся по ряду на каждый выбранный вариант.
    """

    __tablename__ = "answers"
    __table_args__ = (Index("ix_answers_question_kind", "question_id", "kind"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    response_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("responses.id", ondelete="CASCADE"), nullable=False, index=True
    )
    question_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("questions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    value_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    value_int: Mapped[int | None] = mapped_column(Integer, nullable=True)

    response: Mapped[Response] = relationship(back_populates="answers")
    question: Mapped[Question] = relationship(back_populates="answers")

    def __repr__(self) -> str:
        return f"<Answer kind={self.kind!r} value={self.value_text or self.value_int}>"


class SurveyKey(Base):
    """Секретный ключ доступа к статистике опроса.

    Основной ключ (can_edit=True) выдаётся при создании опроса.
    Дополнительные — read-only, для коллег.
    """

    __tablename__ = "survey_keys"
    __table_args__ = (UniqueConstraint("survey_id", "label", name="uq_survey_keys_survey_label"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    survey_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("surveys.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    label: Mapped[str] = mapped_column(String(100), nullable=False)
    can_edit: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow, server_default=None
    )

    survey: Mapped[Survey] = relationship(back_populates="keys")

    def __repr__(self) -> str:
        return f"<SurveyKey id={self.id} label={self.label!r} can_edit={self.can_edit}>"


class SiteSettings(Base):
    """Оформление сайта: фон и логотип. Запись всегда одна, id=1.

    Настройки меняет администратор (см. `admin`). Пустой путь в колонке
    означает «картинка не задана» — поле опционально, дефолтов в коде нет.
    """

    __tablename__ = "site_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    background_image: Mapped[str | None] = mapped_column(String(200), nullable=True)
    logo_image: Mapped[str | None] = mapped_column(String(200), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow, onupdate=utcnow, server_default=None
    )

    def __repr__(self) -> str:
        return f"<SiteSettings id={self.id} background={self.background_image!r}>"
