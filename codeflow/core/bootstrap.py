"""Creates the very first admin user + course — the one thing the login
page's signup flow can't do on an empty database (it requires picking an
existing course; see pages/login_page.py, core/course_service.create_course).

Two entry points share this:
- bootstrap_admin.py — run manually, e.g. from your own machine against a
  platform database with no shell access.
- bootstrap_admin_from_env(), wired into main.py's startup — lets a platform
  deploy (Render included) create the admin just from env vars + a redeploy,
  no shell needed at all. Idempotent: does nothing if BOOTSTRAP_ADMIN_EMAIL
  is unset, or an account with that email already exists (so it's safe left
  on across restarts/redeploys).
"""
import os

from auth.db import get_session
from auth.service import create_password_user, get_user_by_email
from core.course_service import create_course, enroll_user
from core.models import Course, EnrollmentRole
from auth.models import User


def create_admin_account(
    *, email: str, password: str, full_name: str, institution: str, course_title: str,
) -> tuple[User, Course]:
    with get_session() as session:
        user = create_password_user(session, email, password, full_name)
        session.flush()
        course = create_course(session, title=course_title, institution=institution, created_by_id=user.id)
        enroll_user(session, user_id=user.id, course_id=course.id, role=EnrollmentRole.instructor)
        return user, course


def bootstrap_admin_from_env() -> None:
    """Called on every app startup — a no-op unless BOOTSTRAP_ADMIN_EMAIL/
    BOOTSTRAP_ADMIN_PASSWORD are set and no account with that email exists yet."""
    email = os.environ.get('BOOTSTRAP_ADMIN_EMAIL')
    password = os.environ.get('BOOTSTRAP_ADMIN_PASSWORD')
    if not email or not password:
        return

    with get_session() as session:
        if get_user_by_email(session, email):
            return  # already bootstrapped — never re-run on restart/redeploy

    institution = os.environ.get('BOOTSTRAP_ADMIN_INSTITUTION')
    course_title = os.environ.get('BOOTSTRAP_COURSE_TITLE')
    if not institution or not course_title:
        print(
            '[bootstrap] BOOTSTRAP_ADMIN_EMAIL is set but BOOTSTRAP_ADMIN_INSTITUTION / '
            'BOOTSTRAP_COURSE_TITLE are missing — skipping.'
        )
        return

    full_name = os.environ.get('BOOTSTRAP_ADMIN_NAME', 'Admin')
    user, course = create_admin_account(
        email=email, password=password, full_name=full_name,
        institution=institution, course_title=course_title,
    )
    print(f"[bootstrap] created {user.email} (role={user.role.value}), enrolled in '{course.title}'.")
