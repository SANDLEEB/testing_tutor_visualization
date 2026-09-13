"""One-way pseudonymous tokens for AssignmentSubmission — HMAC-SHA256 keyed by
config.SUBMISSION_HASH_SECRET. Irreversible: given a token alone there's no way back to
the id it was computed from, only forward (recompute the hash for a known id and
compare). student_token is the same value every time for a given student, across all
their submissions — a stable pseudonym for linking their submissions to each other
without exposing the real user id. submission_token is unique per submission (hashed
from the submission's own row id).

The distinct 'student:'/'submission:' prefixes keep the two token spaces from
colliding — student_id=7 and submission_id=7 must not hash to the same token.
"""
import hashlib
import hmac

import config


def _token(kind: str, id_: int) -> str:
    return hmac.new(
        config.SUBMISSION_HASH_SECRET.encode(), f'{kind}:{id_}'.encode(), hashlib.sha256,
    ).hexdigest()


def hash_student_id(user_id: int) -> str:
    return _token('student', user_id)


def hash_submission_id(submission_id: int) -> str:
    return _token('submission', submission_id)
