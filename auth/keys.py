"""Доступ к статистике по ключу `?key=`.

Ключ — случайная строка в `survey_keys`, выдаётся при создании опроса.
Сравнение всегда константное по времени, чтобы по задержке ответа нельзя
было подбирать токен перебором.
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from database.repositories import keys as keys_repo
from core.models import Survey, SurveyKey, User
from core.utils import constant_time_equals

logger = logging.getLogger(__name__)

KEY_QUERY_PARAM = "key"


def find_key(session: Session, survey: Survey, token: str | None) -> SurveyKey | None:
    """Ключ этого опроса с таким токеном или None."""
    if not token:
        return None
    for key in keys_repo.list_for_survey(session, survey.id):
        if constant_time_equals(key.token, token):
            return key
    return None


def find_key_anywhere(session: Session, token: str | None) -> SurveyKey | None:
    """Ключ в любой анкете — для входа по ссылке `/k/{token}`."""
    if not token:
        return None
    return keys_repo.get_by_token(session, token)


def is_owner(survey: Survey, user: User | None) -> bool:
    return user is not None and survey.owner_id == user.id


def can_edit_survey(
    session: Session, survey: Survey, user: User | None, token: str | None
) -> bool:
    """Владелец по cookie или ключ, выданный с правом редактирования.

    Обычные ключи «для коллеги» дают только чтение статистики: иначе ссылка,
    которой поделились ради графиков, дала бы ещё и редактирование анкеты.
    """
    if is_owner(survey, user):
        return True
    key = find_key(session, survey, token)
    return key is not None and key.can_edit


def check_access(
    session: Session, survey: Survey, user: User | None, token: str | None
) -> bool:
    """Статистика открыта владельцу по cookie или по ключу из ссылки."""
    if is_owner(survey, user):
        return True
    key = find_key(session, survey, token)
    if key is not None:
        logger.info("Доступ к статистике по ключу id=%s survey=%s", key.id, survey.id)
        return True
    return False
