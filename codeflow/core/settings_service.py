"""Admin-controlled, runtime-toggleable global settings — see core/models.AppSettings."""
from sqlalchemy.orm import Session

from core.models import AppSettings

_SETTINGS_ID = 1


def get_settings(session: Session) -> AppSettings:
    settings = session.get(AppSettings, _SETTINGS_ID)
    if settings is None:
        settings = AppSettings(id=_SETTINGS_ID)
        session.add(settings)
        session.flush()
    return settings


def set_ai_feedback_enabled(session: Session, enabled: bool) -> None:
    get_settings(session).ai_feedback_enabled = enabled
