"""Login / signup page (standalone — no sidebar)."""
from nicegui import app, ui

import config
from auth.db import get_session
from auth.models import Role
from auth.service import authenticate_password, create_password_user, get_user_by_email
from core.course_service import enroll_user, list_courses_by_institution, list_institutions
from core.models import EnrollmentRole

_SIGNUP_ROLES = {'Student': (Role.student, EnrollmentRole.student), 'Instructor': (Role.faculty, EnrollmentRole.instructor)}


def _log_in(user) -> None:
    app.storage.user.update({
        'user_id': user.id,
        'email': user.email,
        'full_name': user.full_name,
        'role': user.role.value,
    })
    ui.navigate.to('/')


def create_login_page():
    ui.colors(primary='#0969da')
    ui.add_head_html('''
    <style>
      body { background: #f8fafc; }
      .auth-card { max-width: 380px; }
    </style>
    ''')

    mode = {'signup': False}

    with ui.column().classes('w-full items-center justify-center gap-4').style('min-height: 100vh'):
        ui.label('🔬 Testing Tutor').classes('text-2xl font-bold').style('color:#0969da')

        with ui.card().classes('auth-card w-full p-6 gap-2') as card:
            title = ui.label('Sign in').classes('text-xl font-semibold mb-2')
            error_label = ui.label('').classes('text-red-500 text-sm')
            error_label.set_visibility(False)

            full_name_input = ui.input(label='Full name').classes('w-full')
            full_name_input.set_visibility(False)
            role_toggle = ui.toggle(list(_SIGNUP_ROLES), value='Student').classes('w-full')
            role_toggle.set_visibility(False)
            email_input = ui.input(label='Email').classes('w-full')
            password_input = ui.input(label='Password', password=True).classes('w-full')

            with get_session() as session:
                institutions = list_institutions(session)
            institution_select = ui.select(institutions, label='Institution').classes('w-full')
            course_select = ui.select([], label='Course').classes('w-full')
            institution_select.set_visibility(False)
            course_select.set_visibility(False)

            def on_institution_change():
                with get_session() as session:
                    options = {c.id: c.title for c in list_courses_by_institution(session, institution_select.value)}
                course_select.set_options(options)

            institution_select.on_value_change(on_institution_change)

            def show_error(message: str) -> None:
                error_label.text = message
                error_label.set_visibility(True)

            def submit():
                email = email_input.value.strip().lower()
                password = password_input.value
                if not email or not password:
                    show_error('Email and password are required.')
                    return

                with get_session() as session:
                    if mode['signup']:
                        full_name = full_name_input.value.strip() or email
                        if get_user_by_email(session, email):
                            show_error('An account with that email already exists.')
                            return
                        if not course_select.value:
                            show_error('Select your institution and course first.')
                            return
                        account_role, enrollment_role = _SIGNUP_ROLES[role_toggle.value]
                        user = create_password_user(session, email, password, full_name, role=account_role)
                        enroll_user(session, user_id=user.id, course_id=course_select.value, role=enrollment_role)
                    else:
                        user = authenticate_password(session, email, password)
                        if not user:
                            show_error('Incorrect email or password.')
                            return
                    _log_in(user)

            for _field in (full_name_input, email_input, password_input):
                _field.on('keydown.enter', submit)

            ui.button('Sign In', on_click=submit, color='primary').classes('w-full mt-2')

            def toggle_mode():
                mode['signup'] = not mode['signup']
                title.text = 'Create account' if mode['signup'] else 'Sign in'
                full_name_input.set_visibility(mode['signup'])
                role_toggle.set_visibility(mode['signup'])
                institution_select.set_visibility(mode['signup'])
                course_select.set_visibility(mode['signup'])
                toggle_link.text = 'Already have an account? Sign in' if mode['signup'] else "Don't have an account? Create one"
                error_label.set_visibility(False)

            toggle_link = ui.link("Don't have an account? Create one", '#').classes('text-sm mt-1').style('color:#0969da')
            toggle_link.on('click', lambda: toggle_mode())

            if config.GOOGLE_SSO_ENABLED or config.MICROSOFT_SSO_ENABLED:
                ui.separator().classes('my-2')
                if config.GOOGLE_SSO_ENABLED:
                    ui.button('Sign in with Google', icon='login', color=None) \
                        .classes('w-full') \
                        .on('click', lambda: ui.navigate.to('/auth/google/login'))
                if config.MICROSOFT_SSO_ENABLED:
                    ui.button('Sign in with Microsoft', icon='login', color=None) \
                        .classes('w-full') \
                        .on('click', lambda: ui.navigate.to('/auth/microsoft/login'))
