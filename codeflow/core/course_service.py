"""Course CRUD and enrollment — the multi-tenancy boundary. Every topic (Concept) belongs
to exactly one Course, and everything else (material/content/questions/assignments) hangs
off a Concept, so scoping here is what keeps two courses' data from mixing.

Courses are created by an admin (see pages/admin_page.py), not self-served by instructors.
Both instructors and students pick their course — by institution, then course name — at
signup, which creates a single Enrollment row each; that's the one join table that governs
both "which course is this instructor teaching" and "which course is this student in."
"""
import random
import string

from sqlalchemy import select
from sqlalchemy.orm import Session

from auth.models import User
from core.models import Course, Enrollment, EnrollmentRole

_CODE_ALPHABET = string.ascii_uppercase + string.digits
_CODE_LENGTH = 6


def _generate_code(session: Session) -> str:
    while True:
        code = ''.join(random.choices(_CODE_ALPHABET, k=_CODE_LENGTH))
        if not session.scalar(select(Course).where(Course.code == code)):
            return code


def create_course(session: Session, *, title: str, institution: str, created_by_id: int) -> Course:
    course = Course(
        code=_generate_code(session), title=title.strip(), institution=institution.strip(),
        created_by_id=created_by_id,
    )
    session.add(course)
    session.flush()
    return course


def list_courses(session: Session) -> list[Course]:
    return list(session.scalars(select(Course).order_by(Course.institution, Course.title)))


def list_institutions(session: Session) -> list[str]:
    return sorted({c.institution for c in list_courses(session) if c.institution})


def list_courses_by_institution(session: Session, institution: str) -> list[Course]:
    stmt = select(Course).where(Course.institution == institution).order_by(Course.title)
    return list(session.scalars(stmt))


def get_course(session: Session, course_id: int) -> Course | None:
    return session.get(Course, course_id)


def get_enrollment(session: Session, user_id: int) -> Enrollment | None:
    """A user's one course membership — set at signup (or via the fallback picker for
    accounts that predate it), whether they're a student or an instructor."""
    return session.scalar(select(Enrollment).where(Enrollment.user_id == user_id))


def enroll_user(session: Session, *, user_id: int, course_id: int, role: EnrollmentRole) -> Enrollment:
    existing = get_enrollment(session, user_id)
    if existing is not None:
        existing.course_id = course_id
        existing.role = role
        return existing
    enrollment = Enrollment(course_id=course_id, user_id=user_id, role=role)
    session.add(enrollment)
    session.flush()
    return enrollment


def set_enrollment_role(session: Session, user_id: int, role: EnrollmentRole) -> None:
    """Keep Enrollment.role in sync when an admin promotes/demotes a user's account role
    (pages/admin_page.py) — roster queries filter on this, so a stale value would miscount
    who's a student vs. an instructor."""
    enrollment = get_enrollment(session, user_id)
    if enrollment is not None:
        enrollment.role = role


def count_students(session: Session, course_id: int) -> int:
    stmt = select(Enrollment).where(Enrollment.course_id == course_id, Enrollment.role == EnrollmentRole.student)
    return len(list(session.scalars(stmt)))


def list_students(session: Session, course_id: int) -> list[User]:
    """Every student enrolled in course_id, by name — for the instructor's
    per-student mastery breakdown (core/bkt_service.list_student_mastery_matrix)."""
    stmt = (
        select(User).join(Enrollment, Enrollment.user_id == User.id)
        .where(Enrollment.course_id == course_id, Enrollment.role == EnrollmentRole.student)
        .order_by(User.full_name)
    )
    return list(session.scalars(stmt))
