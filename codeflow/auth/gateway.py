"""API Gateway — the single choke point that authenticates a request and
decides whether it's allowed to reach the page it asked for. Mirrors the
"API Gateway (authenticates + routes)" box in the system architecture:
every page in main.py goes through here rather than checking session
state itself.
"""
from nicegui import app, ui


def current_user() -> dict:
    return app.storage.user


def require_login() -> dict | None:
    """Ensure a session exists; redirect to /login and return None if not."""
    user = current_user()
    if not user.get('user_id'):
        ui.navigate.to('/login')
        return None
    return user


def require_role(*roles: str) -> dict | None:
    """Ensure a session exists and (if roles are given) the user holds one of them."""
    user = require_login()
    if user is None:
        return None
    if roles and user.get('role') not in roles:
        ui.navigate.to('/')
        return None
    return user


def _render_select_course_gate(user: dict) -> None:
    """Fallback for an account that predates course selection at signup (or otherwise has
    no enrollment yet) — picks by institution, then course, same two-step flow as signup,
    just reached from inside the app instead of before it."""
    from auth.db import get_session
    from core.course_service import (
        enroll_user, list_courses_by_institution, list_institutions,
    )
    from core.models import EnrollmentRole

    with get_session() as session:
        institutions = list_institutions(session)

    with ui.column().classes('w-full items-center py-16 gap-3'):
        ui.label('Select your course').classes('text-2xl font-bold text-[#0969da]')
        ui.label("Pick your institution and course — you'll only see that class's topics, "
                 "content, and assignments.").classes('text-gray-500 text-center max-w-md')
        with ui.card().classes('w-full max-w-sm p-4 gap-2'):
            institution_select = ui.select(institutions, label='Institution').classes('w-full')
            course_select = ui.select([], label='Course').classes('w-full')
            error_label = ui.label('').classes('text-red-500 text-xs')
            error_label.set_visibility(False)

            def on_institution_change():
                with get_session() as session:
                    options = {c.id: c.title for c in list_courses_by_institution(session, institution_select.value)}
                course_select.set_options(options)

            institution_select.on_value_change(on_institution_change)

            def select_course():
                error_label.set_visibility(False)
                if not course_select.value:
                    error_label.text = 'Pick a course first.'
                    error_label.set_visibility(True)
                    return
                role = EnrollmentRole.instructor if user.get('role') == 'faculty' else EnrollmentRole.student
                with get_session() as session:
                    enroll_user(session, user_id=user['user_id'], course_id=course_select.value, role=role)
                ui.navigate.reload()

            ui.button('Continue', on_click=select_course, color='primary').classes('w-full')


def require_course(user: dict) -> int | None:
    """Resolve the course a page's data should be scoped to — every user (student,
    instructor, or admin) has exactly one Enrollment, picked at signup. Renders a
    select-course prompt and returns None if the account somehow doesn't have one yet
    (accounts created before course selection existed).
    """
    from auth.db import get_session
    from core.course_service import get_enrollment

    with get_session() as session:
        enrollment = get_enrollment(session, user['user_id'])
        course_id = enrollment.course_id if enrollment is not None else None
    if course_id is None:
        _render_select_course_gate(user)
        return None
    return course_id
