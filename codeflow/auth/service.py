from sqlalchemy import select
from sqlalchemy.orm import Session

import config
from auth.models import AuthProvider, Role, User
from auth.security import hash_password, verify_password


def _role_for_new_account(email: str, requested_role: Role = Role.student) -> Role:
    """Admin allowlist always wins; otherwise honor whatever role the signup form asked
    for (student or faculty) — see pages/login_page.py's role picker."""
    return Role.admin if email.lower() in config.ADMIN_EMAILS else requested_role


def get_user_by_email(session: Session, email: str) -> User | None:
    return session.scalar(select(User).where(User.email == email.lower()))


def get_user_by_id(session: Session, user_id: int) -> User | None:
    return session.get(User, user_id)


def create_password_user(
    session: Session, email: str, password: str, full_name: str, role: Role = Role.student,
) -> User:
    user = User(
        email=email.lower(),
        full_name=full_name,
        hashed_password=hash_password(password),
        auth_provider=AuthProvider.password,
        role=_role_for_new_account(email, role),
    )
    session.add(user)
    session.flush()
    return user


def authenticate_password(session: Session, email: str, password: str) -> User | None:
    user = get_user_by_email(session, email)
    if not user or not user.hashed_password or not user.is_active:
        return None
    if not verify_password(password, user.hashed_password):
        return None
    return user


def get_or_create_oauth_user(
    session: Session, email: str, full_name: str, provider: AuthProvider, provider_sub: str
) -> User:
    user = get_user_by_email(session, email)
    if user:
        return user
    user = User(
        email=email.lower(),
        full_name=full_name,
        auth_provider=provider,
        provider_sub=provider_sub,
        role=_role_for_new_account(email),
    )
    session.add(user)
    session.flush()
    return user


def list_users(session: Session) -> list[User]:
    return list(session.scalars(select(User).order_by(User.created_at)))


def set_role(session: Session, user_id: int, role: Role) -> None:
    user = session.get(User, user_id)
    if user:
        user.role = role


def set_active(session: Session, user_id: int, is_active: bool) -> None:
    user = session.get(User, user_id)
    if user:
        user.is_active = is_active
