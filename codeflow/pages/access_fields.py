"""Shared Access control widget — who beyond course membership can see a published
Assignment or Question once it's published: everyone enrolled (the default, and every
prior behavior before this control existed), specific section(s), or specific student(s).
Used identically by pages/instructor_assignments_page.py's _AssignmentFields and
pages/instructor_practice_page.py's practice-question form/edit dialog, so the two
can't drift out of sync — build it, let the instructor fill it in, call `read()`.
"""
from nicegui import ui

from core.models import AccessScope

_ACCESS_SCOPE_LABELS = {
    AccessScope.course: 'Entire class',
    AccessScope.sections: 'Specific section(s)',
    AccessScope.students: 'Specific student(s)',
}


class AccessFields:
    def __init__(
        self, *, course_sections: list[str] = (), course_students: list[tuple[int, str]] = (),
        access_scope: AccessScope = AccessScope.course, access_sections: list[str] = (),
        access_student_ids: list[int] = (),
    ):
        with ui.column().classes('w-full gap-1 border border-gray-200 rounded p-3 mt-1'):
            ui.label('Access').classes('text-xs font-semibold text-gray-500 uppercase tracking-wide')
            self.scope_toggle = ui.toggle(_ACCESS_SCOPE_LABELS, value=access_scope).classes('w-full')
            self.sections_select = ui.select(
                {s: s for s in course_sections}, value=list(access_sections), multiple=True, label='Sections',
            ).props('dense outlined use-chips').classes('w-full')
            # with_input=True turns this into a type-to-filter combobox (Quasar's
            # use-input) — course_students' labels already fold in name/email/CWID
            # (see callers), so typing any of the three narrows the option list.
            self.students_select = ui.select(
                {sid: label for sid, label in course_students}, value=list(access_student_ids),
                multiple=True, label='Students', with_input=True,
            ).props('dense outlined use-chips').classes('w-full')
            self._hint = ui.label('').classes('text-[11px] text-gray-400')

            # Section/student pickers only show for their matching scope, and the section
            # picker only appears at all once some student in the course actually has one
            # (core/course_service.list_sections) — no point offering a filter with
            # nothing to filter by.
            def _update_visibility() -> None:
                scope = self.scope_toggle.value
                self.sections_select.set_visibility(scope == AccessScope.sections)
                self.students_select.set_visibility(scope == AccessScope.students)
                if scope == AccessScope.sections and not course_sections:
                    self._hint.text = 'No sections exist yet — assign one to a student on the Students page first.'
                elif scope == AccessScope.students and not course_students:
                    self._hint.text = 'No students enrolled in this course yet.'
                else:
                    self._hint.text = ''

            self.scope_toggle.on_value_change(_update_visibility)
            _update_visibility()

    def read(self) -> dict:
        return {
            'access_scope': self.scope_toggle.value,
            'access_sections': self.sections_select.value or [],
            'access_student_ids': self.students_select.value or [],
        }

    def reset(self) -> None:
        self.scope_toggle.set_value(AccessScope.course)
        self.sections_select.set_value([])
        self.students_select.set_value([])


def access_summary(*, access_scope: AccessScope, access_sections: list, access_student_count: int) -> str:
    """Compact 'who can see this' label for a list row — full detail lives in Edit."""
    if access_scope == AccessScope.course:
        return 'Entire class'
    if access_scope == AccessScope.sections:
        n = len(access_sections or [])
        return f"{n} section{'s' if n != 1 else ''}" if n else 'No sections chosen'
    n = access_student_count
    return f"{n} student{'s' if n != 1 else ''}" if n else 'No students chosen'
