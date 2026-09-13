"""Shared Due Date control widget for Assignments — a whole-class default deadline
plus optional per-section/per-student overrides (core/assignment_service.resolve_due_at:
a student-specific override beats a section override beats the class default). Once
the resolved deadline passes, core/assignment_service.submit_and_grade refuses new
submissions — it does NOT hide the assignment (that's AccessScope's job, a separate
control). Used by pages/instructor_assignments_page.py's _AssignmentFields (New + Edit).
Practice Questions (core/models.Question) have no submission/grading flow, so no due
date concept applies there — this widget is Assignment-only.
"""
from datetime import datetime, timezone

from nicegui import ui


def _parse_local_dt(value: str | None) -> datetime | None:
    """'YYYY-MM-DDTHH:MM' from a type=datetime-local input -> a UTC-tagged datetime.
    No real timezone conversion happens (the app has none anywhere else either) — the
    entered wall-clock value is just tagged UTC and displayed back the same way."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _to_input_value(dt: datetime | None) -> str:
    return dt.strftime('%Y-%m-%dT%H:%M') if dt else ''


class DueDateFields:
    def __init__(
        self, *, course_sections: list[str] = (), course_students: list[tuple[int, str]] = (),
        due_at: datetime | None = None, overrides: list[dict] = (),
    ):
        self._student_labels = dict(course_students)
        # Each override: {'section': str|None, 'student_id': int|None, 'due_at': datetime}
        # — exactly one of section/student_id set. At most one override per target
        # (adding a second for the same section/student replaces the first).
        self._overrides = [dict(o) for o in overrides]

        with ui.column().classes('w-full gap-1 border border-gray-200 rounded p-3 mt-1'):
            ui.label('Due Date').classes('text-xs font-semibold text-gray-500 uppercase tracking-wide')
            ui.label(
                "Blocks new submissions once passed — doesn't hide the assignment, students "
                "can still view it and their past submissions. Leave blank for no deadline."
            ).classes('text-[11px] text-gray-400')
            self.due_input = ui.input(label='Default deadline (whole class)', value=_to_input_value(due_at)) \
                .props('type=datetime-local dense outlined clearable').classes('w-64')

            self._overrides_list = ui.column().classes('w-full gap-1 mt-2')
            self._render_overrides()

            with ui.row().classes('w-full items-end gap-2 mt-2'):
                self._override_scope = ui.toggle({'section': 'Section', 'student': 'Student'}, value='section') \
                    .props('dense')
                self._override_section_select = ui.select(
                    {s: s for s in course_sections}, label='Section',
                ).props('dense outlined').classes('w-40')
                self._override_student_select = ui.select(
                    {sid: label for sid, label in course_students}, label='Student', with_input=True,
                ).props('dense outlined').classes('flex-1')
                self._override_date_input = ui.input(label='Due').props('type=datetime-local dense outlined').classes('w-56')
                ui.button('+ Add override', on_click=self._add_override).props('dense outline size=sm')

            def _update_override_target_visibility() -> None:
                is_section = self._override_scope.value == 'section'
                self._override_section_select.set_visibility(is_section)
                self._override_student_select.set_visibility(not is_section)

            self._override_scope.on_value_change(_update_override_target_visibility)
            _update_override_target_visibility()

    def _override_label(self, o: dict) -> str:
        if o.get('section'):
            target, kind = o['section'], 'Section'
        else:
            target, kind = self._student_labels.get(o.get('student_id'), 'Unknown student'), 'Student'
        return f"{kind}: {target} → due {o['due_at'].strftime('%b %d, %Y %I:%M %p')}"

    def _render_overrides(self) -> None:
        self._overrides_list.clear()
        with self._overrides_list:
            for i, o in enumerate(self._overrides):
                with ui.row().classes('w-full items-center gap-2 text-xs bg-gray-50 rounded px-2 py-1'):
                    ui.label(self._override_label(o)).classes('flex-1')
                    ui.button(icon='close', on_click=lambda i=i: self._remove_override(i)) \
                        .props('flat dense size=xs round')

    def _add_override(self) -> None:
        due = _parse_local_dt(self._override_date_input.value)
        if due is None:
            ui.notify('Pick a due date first.', color='warning')
            return
        if self._override_scope.value == 'section':
            section = self._override_section_select.value
            if not section:
                ui.notify('Pick a section first.', color='warning')
                return
            self._overrides = [o for o in self._overrides if o.get('section') != section]
            self._overrides.append({'section': section, 'student_id': None, 'due_at': due})
        else:
            student_id = self._override_student_select.value
            if not student_id:
                ui.notify('Pick a student first.', color='warning')
                return
            self._overrides = [o for o in self._overrides if o.get('student_id') != student_id]
            self._overrides.append({'section': None, 'student_id': student_id, 'due_at': due})
        self._override_date_input.set_value('')
        self._render_overrides()

    def _remove_override(self, index: int) -> None:
        del self._overrides[index]
        self._render_overrides()

    def read(self) -> dict:
        return {'due_at': _parse_local_dt(self.due_input.value), 'due_date_overrides': list(self._overrides)}

    def reset(self) -> None:
        self.due_input.set_value('')
        self._overrides = []
        self._render_overrides()
