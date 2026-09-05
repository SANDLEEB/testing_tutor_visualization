"""Admin-only user management page."""
from nicegui import ui

import config
from auth.db import get_session
from auth.models import Role
from auth.service import list_users, set_active, set_role
from core import settings_service
from core.course_service import set_enrollment_role
from core.models import EnrollmentRole

ROLE_OPTIONS = [r.value for r in Role]


def create_admin_page():
    ui.label('User Management').classes('text-2xl font-bold mb-4')

    with ui.card().classes('w-full p-4 gap-1 mb-4'):
        ui.label('AI Feedback (global)').classes('font-semibold text-sm text-gray-500 uppercase tracking-wide')
        with get_session() as session:
            ai_feedback_enabled = settings_service.get_settings(session).ai_feedback_enabled

        def on_ai_feedback_change(e):
            with get_session() as session:
                settings_service.set_ai_feedback_enabled(session, e.value)
            ui.notify('AI feedback enabled.' if e.value else 'AI feedback disabled.', color='positive')

        ai_switch = ui.switch(
            'Allow AI feedback on assignments', value=ai_feedback_enabled, on_change=on_ai_feedback_change,
        )
        if not config.AI_ENABLED:
            ai_switch.disable()
            ui.label(
                'No AI provider key is configured — add one to codeflow/.env (see .env.example) '
                'before this can be turned on.'
            ).classes('text-xs text-gray-400')
        else:
            ui.label(
                "Master switch — when off, no assignment produces AI feedback regardless of "
                "its own per-assignment feedback setting."
            ).classes('text-xs text-gray-400')

    container = ui.column().classes('w-full gap-2')

    GRID_STYLE = 'grid-template-columns: 2fr 1.5fr 1fr 1fr 0.7fr 1fr'
    TRUNCATE = 'overflow-hidden text-ellipsis whitespace-nowrap min-w-0'

    def refresh():
        container.clear()
        with container:
            with get_session() as session:
                users = list_users(session)
                with ui.grid().classes('w-full gap-2').style(GRID_STYLE):
                    for header in ('Email', 'Name', 'Provider', 'Role', 'Active', 'Created'):
                        ui.label(header).classes('font-semibold text-sm text-gray-500')

                    for user in users:
                        ui.label(user.email).classes(f'items-center {TRUNCATE}')
                        ui.label(user.full_name).classes(f'items-center {TRUNCATE}')
                        ui.label(user.auth_provider.value).classes('items-center')

                        def on_role_change(e, user_id=user.id):
                            new_role = Role(e.value)
                            with get_session() as s:
                                set_role(s, user_id, new_role)
                                # Keep enrollment role in sync — the roster's student/
                                # instructor split reads this, not the account role.
                                enrollment_role = EnrollmentRole.instructor if new_role == Role.faculty else EnrollmentRole.student
                                set_enrollment_role(s, user_id, enrollment_role)
                            ui.notify('Role updated', color='positive')

                        ui.select(ROLE_OPTIONS, value=user.role.value, on_change=on_role_change).classes('w-32')

                        def on_active_change(e, user_id=user.id):
                            with get_session() as s:
                                set_active(s, user_id, e.value)
                            ui.notify('Status updated', color='positive')

                        ui.switch(value=user.is_active, on_change=on_active_change)
                        ui.label(user.created_at.strftime('%Y-%m-%d')).classes('items-center')

    refresh()
