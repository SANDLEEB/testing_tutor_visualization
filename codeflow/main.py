"""
Testing Tutor — NiceGUI entry point.
Run: python main.py
"""
import html
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from nicegui import ui, app

import config
from auth import routes as auth_routes
from auth.db import init_db
from auth.gateway import require_role
from pages.login_page    import create_login_page
from pages.admin_page    import create_admin_page
from pages.instructor_assignments_page import create_instructor_assignments_page
from pages.instructor_practice_page import create_instructor_practice_page
from pages.instructor_topics_page import create_instructor_topics_page
from pages.student_adaptive_practice_page import create_adaptive_practice_page
from pages.student_assignments_page import (
    create_student_assignment_detail_page, create_student_assignments_list_page,
)
from pages.student_topics_page import create_student_topics_page

# Student and Instructor modules are being rebuilt from scratch — every other
# page under pages/ that backed the old nav is still on disk (and in git
# history) but deliberately unwired here. Only Home, AI Question Authoring,
# Assignments (instructor + student), Practice Questions (instructor +
# student adaptive), Topics (instructor + student mastery dashboards — no
# detail-page drill-down; pages/topic_hub_page.py's content-library view is
# unwired on purpose, see instructor_topics_page.py's docstring), and the
# shared login/logout/admin scaffolding are live so far.

app.on_startup(init_db)
auth_routes.register(app)

ACCENT = '#0969da'  # the app's one brand color — every `color='primary'` element picks this up

# ── Static files & global CSS/JS ────────────────────────────────────────────

app.add_static_files('/static', os.path.join(os.path.dirname(__file__), 'static'))

GLOBAL_HEAD = '''
<script src="/static/engine.js"></script>
<style>
  /* Reset & base */
  *, *::before, *::after { box-sizing: border-box; }
  body { margin:0; font-family: "Inter", system-ui, sans-serif; }

  /* Sidebar */
  .sidebar {
    width: 240px; min-width: 240px;
    background: #0f172a;
    color: #e2e8f0;
    display: flex; flex-direction: column;
    height: 100vh; position: fixed; left: 0; top: 0; z-index: 100;
    overflow-y: auto;
  }
  .sidebar-logo {
    padding: 20px 16px 12px;
    font-size: 18px; font-weight: 700; color: #58a6ff;
    border-bottom: 1px solid #1e293b;
    display: flex; align-items: center; gap: 10px;
  }
  .sidebar-section {
    padding: 12px 16px 4px;
    font-size: 10px; font-weight: 600; letter-spacing: .08em;
    color: #475569; text-transform: uppercase;
  }
  .sidebar-portal-badge {
    margin: 8px 16px 0;
    padding: 3px 8px;
    font-size: 10px; font-weight: 600; letter-spacing: .04em;
    color: #79c0ff; background: #0c2d6b; border-radius: 4px;
    display: inline-block; width: fit-content;
  }
  .mode-switcher {
    display: flex; gap: 4px;
    margin: 10px 16px 4px;
    background: #1e293b; border-radius: 6px; padding: 3px;
  }
  .mode-tab {
    flex: 1; text-align: center;
    padding: 6px 4px; font-size: 11px; font-weight: 600;
    color: #94a3b8; border-radius: 4px; text-decoration: none;
    cursor: pointer; transition: background .15s, color .15s;
  }
  .mode-tab:hover { color: #e2e8f0; }
  .mode-tab.active { background: #0969da; color: #fff; }
  .sidebar-item {
    display: flex; align-items: center; gap: 10px;
    padding: 9px 16px; font-size: 13px; color: #94a3b8;
    text-decoration: none; border-radius: 6px; margin: 1px 8px;
    cursor: pointer; transition: background .15s, color .15s;
  }
  .sidebar-item:hover { background: #1e293b; color: #e2e8f0; }
  .sidebar-item.active { background: #0969da; color: #fff; }
  .sidebar-icon { width: 20px; text-align: center; font-size: 15px; }
  .sidebar-footer {
    margin-top: auto; padding: 12px 16px;
    border-top: 1px solid #1e293b; font-size: 11px; color: #475569;
  }

  /* Main content offset for sidebar */
  .page-content {
    margin-left: 240px;
    min-height: 100vh;
    background: #f8fafc;
  }

  /* Dark mode */
  body.dark .page-content { background: #0f172a; }

  /* Responsive */
  @media (max-width: 1100px) {
    .sidebar { transform: translateX(-240px); }
    .page-content { margin-left: 0; }
  }
</style>
'''

STUDENT_NAV = [
    ('Grading', [
        ('📝', 'Assignments',      '/student/assignments'),
    ]),
    ('Practice', [
        ('🧠', 'Adaptive Practice', '/student/practice/adaptive'),
    ]),
    ('Content', [
        ('📚', 'Topics',            '/student/topics'),
    ]),
    ('', [
        ('⌂', 'Home',               '/student/home'),
    ]),
]

INSTRUCTOR_NAV = [
    ('Content', [
        ('📝', 'Assignments',      '/instructor/assignments'),
        ('🧠', 'Practice Questions', '/instructor/practice'),
        ('📚', 'Topics',             '/instructor/topics'),
    ]),
    ('', [
        ('⌂', 'Home',               '/instructor/home'),
    ]),
]

ADMIN_NAV = [
    ('Admin', [
        ('⚙', 'User Management',   '/admin/users'),
    ]),
]


def _nav_section_html(sections: list, current_path: str) -> str:
    html = ''
    for section, links in sections:
        if section:
            html += f'<div class="sidebar-section">{section}</div>'
        for icon, label, path in links:
            active = 'active' if path == current_path else ''
            html += f'''
              <a class="sidebar-item {active}" href="{path}">
                <span class="sidebar-icon">{icon}</span>
                <span>{label}</span>
              </a>'''
    return html


def _current_course_name(user: dict) -> str | None:
    from auth.db import get_session
    from core.course_service import get_course, get_enrollment

    with get_session() as session:
        enrollment = get_enrollment(session, user['user_id'])
        if enrollment is None:
            return None
        course = get_course(session, enrollment.course_id)
        return course.title if course else None


def sidebar_html(current_path: str, user: dict) -> str:
    role = user.get('role')
    if role == 'admin':
        portal = 'Admin — All Access'
    elif role == 'faculty':
        portal = 'Instructor Portal'
    else:
        portal = 'Student Portal'

    course_name = _current_course_name(user)
    course_badge_html = (
        f'<div class="sidebar-portal-badge" style="background:#374151;color:#e5e7eb">'
        f'🎓 {html.escape(course_name)}</div>'
    ) if course_name else ''

    switcher_html = ''
    if role == 'admin':
        # Admin can act as either mode, but sees exactly one at a time — never
        # both merged together — via an explicit switcher, mirroring the
        # current URL rather than a separately-tracked preference.
        if current_path.startswith('/student'):
            current_mode = 'student'
        elif current_path.startswith('/admin'):
            current_mode = 'admin'
        else:
            current_mode = 'instructor'

        tabs = [
            ('instructor', 'Instructor', '/instructor/home'),
            ('student',    'Student',    '/student/home'),
            ('admin',      'Admin',      '/admin/users'),
        ]
        switcher_html = '<div class="mode-switcher">'
        for key, label, href in tabs:
            active = 'active' if key == current_mode else ''
            switcher_html += f'<a class="mode-tab {active}" href="{href}">{label}</a>'
        switcher_html += '</div>'

        items_html = _nav_section_html(
            {'instructor': INSTRUCTOR_NAV, 'student': STUDENT_NAV, 'admin': ADMIN_NAV}[current_mode],
            current_path,
        )
    elif role == 'faculty':
        items_html = _nav_section_html(INSTRUCTOR_NAV, current_path)
    else:
        items_html = _nav_section_html(STUDENT_NAV, current_path)

    return f'''
<div class="sidebar" id="app-sidebar">
  <div class="sidebar-logo">
    <span>🔬</span>
    <span>Testing Tutor</span>
  </div>
  <div class="sidebar-portal-badge">{portal}</div>
  {course_badge_html}
  {switcher_html}
  {items_html}
  <div class="sidebar-footer">
    Signed in as {user.get('full_name', '')} ({user.get('role', '')})<br>
    <a href="/logout" style="color:#f87171;">Logout</a>
    <div style="margin-top:6px;">
      <button onclick="document.body.classList.toggle('dark')"
              style="background:#1e293b;color:#94a3b8;border:1px solid #334155;
                     padding:3px 10px;border-radius:4px;font-size:11px;cursor:pointer;">
        ☀ / ☾ Toggle Theme
      </button>
    </div>
  </div>
</div>
'''


def create_layout(current_path: str, page_fn, roles: tuple[str, ...] = ()):
    """Wrap a page-content function in the shared sidebar + header shell.

    All auth + role gating goes through auth.gateway.require_role — the
    API Gateway choke point — rather than checking session state here.
    """
    user = require_role(*roles)
    if user is None:
        return

    ui.colors(primary=ACCENT)
    ui.add_head_html(GLOBAL_HEAD)
    ui.add_body_html(sidebar_html(current_path, user))

    with ui.element('div').classes('page-content'):
        with ui.element('div').style('padding:16px'):
            page_fn()


# ── Routes ───────────────────────────────────────────────────────────────────
# Every non-shared route below is locked to its mode's role(s) via `roles=`.
# require_role() (auth/gateway.py) enforces this — a student hitting an
# /instructor/* or /admin/* URL directly is bounced to their own home, and
# vice versa. Nav links are also mode-scoped (see sidebar_html above), so
# neither mode is ever offered a link into the other.

@ui.page('/')
def root():
    """Role-based dispatcher — the only shared entry point."""
    user = require_role()
    if user is None:
        return
    if user.get('role') in ('faculty', 'admin'):
        ui.navigate.to('/instructor/home')
    else:
        ui.navigate.to('/student/home')


# ── Student mode ─────────────────────────────────────────────────────────────
# Placeholder Home only — the rest of the student module is being rebuilt.

def _student_home_placeholder():
    ui.label('Student Portal').classes('text-2xl font-bold mb-1')
    ui.label('The student module is being rebuilt.').classes('text-sm text-gray-500')


@ui.page('/student/home')
def student_home():
    create_layout('/student/home', _student_home_placeholder, roles=('student', 'admin'))


@ui.page('/student/assignments')
def student_assignments():
    create_layout('/student/assignments', create_student_assignments_list_page, roles=('student', 'admin'))


@ui.page('/student/assignments/{assignment_id}')
def student_assignment_detail(assignment_id: int):
    create_layout(
        '/student/assignments',
        lambda: create_student_assignment_detail_page(assignment_id),
        roles=('student', 'admin'),
    )


@ui.page('/student/practice/adaptive')
def student_practice_adaptive():
    create_layout('/student/practice/adaptive', create_adaptive_practice_page, roles=('student', 'admin'))


@ui.page('/student/topics')
def student_topics():
    create_layout('/student/topics', create_student_topics_page, roles=('student', 'admin'))


# ── Instructor mode ──────────────────────────────────────────────────────────
# Placeholder Home only — the rest of the instructor module is being rebuilt.

def _instructor_home_placeholder():
    ui.label('Instructor Portal').classes('text-2xl font-bold mb-1')
    ui.label('The instructor module is being rebuilt.').classes('text-sm text-gray-500')


@ui.page('/instructor/home')
def instructor_home():
    create_layout('/instructor/home', _instructor_home_placeholder, roles=('faculty', 'admin'))


@ui.page('/instructor/assignments')
def instructor_assignments():
    create_layout('/instructor/assignments', create_instructor_assignments_page, roles=('faculty', 'admin'))


@ui.page('/instructor/practice')
def instructor_practice():
    create_layout('/instructor/practice', create_instructor_practice_page, roles=('faculty', 'admin'))


@ui.page('/instructor/topics')
def instructor_topics():
    create_layout('/instructor/topics', create_instructor_topics_page, roles=('faculty', 'admin'))


# ── Admin ────────────────────────────────────────────────────────────────────

@ui.page('/admin/users')
def admin_users():
    create_layout('/admin/users', create_admin_page, roles=('admin',))


# ── Auth ─────────────────────────────────────────────────────────────────────

@ui.page('/login')
def login():
    if app.storage.user.get('user_id'):
        ui.navigate.to('/')
        return
    create_login_page()


@ui.page('/logout')
def logout():
    app.storage.user.clear()
    ui.navigate.to('/login')


# ── Launch ───────────────────────────────────────────────────────────────────

if __name__ in {'__main__', '__mp_main__'}:
    ui.run(
        port=8080,
        title='Testing Tutor',
        favicon='🔬',
        storage_secret=config.SESSION_SECRET,
        show=False,
    )
