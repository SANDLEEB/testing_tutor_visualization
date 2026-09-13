"""One-time bootstrap: create the first admin user + course directly in the
database, bypassing the login page's signup flow (which requires picking an
existing course — impossible when none exist yet, and no wired-up page can
create one either; see core/course_service.py's create_course, which nothing
currently calls).

Edit the constants below, then run once with the project's venv:
    .venv/bin/python3.14 bootstrap_admin.py     (local)
    python bootstrap_admin.py                   (Render shell)

EMAIL must be listed in ADMIN_EMAILS for the account to come back as admin —
otherwise it's created as a regular student. Safe to delete after running.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from auth.db import get_session
from auth.service import create_password_user
from core.course_service import create_course, enroll_user
from core.models import EnrollmentRole

EMAIL = 'shizaandleeb1@gmail.com'
PASSWORD = 'shiza786'
FULL_NAME = 'Admin'
INSTITUTION = 'University of Alabama'
COURSE_TITLE = 'CS101'

with get_session() as session:
    user = create_password_user(session, EMAIL, PASSWORD, FULL_NAME)
    session.flush()
    course = create_course(session, title=COURSE_TITLE, institution=INSTITUTION, created_by_id=user.id)
    enroll_user(session, user_id=user.id, course_id=course.id, role=EnrollmentRole.instructor)
    print(f"Created {user.email} (role={user.role.value}), enrolled in '{course.title}' @ '{course.institution}'.")
