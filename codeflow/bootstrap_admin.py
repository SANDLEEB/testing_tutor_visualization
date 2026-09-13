"""Manual one-time run of core.bootstrap.create_admin_account — for creating
the first admin user + course from your own machine, e.g. against a platform
database with no shell access (point DATABASE_URL at it for this command).

Prefer setting BOOTSTRAP_ADMIN_EMAIL/BOOTSTRAP_ADMIN_PASSWORD/etc. directly on
the platform instead (see core/bootstrap.py's bootstrap_admin_from_env,
already wired into main.py's startup) — that needs no manual run at all, just
a redeploy. Use this script only when you can't set env vars there either.

All values come from environment variables so nothing sensitive ever sits in
this file (or in git):

    BOOTSTRAP_ADMIN_EMAIL=you@example.com \
    BOOTSTRAP_ADMIN_PASSWORD='a-real-password' \
    BOOTSTRAP_ADMIN_INSTITUTION='My University' \
    BOOTSTRAP_COURSE_TITLE='CS101' \
    .venv/bin/python3.14 bootstrap_admin.py

BOOTSTRAP_ADMIN_EMAIL must be listed in ADMIN_EMAILS for the account to come
back as admin — otherwise it's created as a regular student.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from core.bootstrap import create_admin_account

REQUIRED_VARS = (
    'BOOTSTRAP_ADMIN_EMAIL', 'BOOTSTRAP_ADMIN_PASSWORD',
    'BOOTSTRAP_ADMIN_INSTITUTION', 'BOOTSTRAP_COURSE_TITLE',
)
missing = [name for name in REQUIRED_VARS if not os.environ.get(name)]
if missing:
    sys.exit(f"Missing required environment variable(s): {', '.join(missing)} — see this file's docstring.")

user, course = create_admin_account(
    email=os.environ['BOOTSTRAP_ADMIN_EMAIL'],
    password=os.environ['BOOTSTRAP_ADMIN_PASSWORD'],
    full_name=os.environ.get('BOOTSTRAP_ADMIN_NAME', 'Admin'),
    institution=os.environ['BOOTSTRAP_ADMIN_INSTITUTION'],
    course_title=os.environ['BOOTSTRAP_COURSE_TITLE'],
)
print(f"Created {user.email} (role={user.role.value}), enrolled in '{course.title}' @ '{course.institution}'.")
