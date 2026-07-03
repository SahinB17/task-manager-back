"""
Centralized entity resolver layer for AI Assistant inputs.

Normalisation, fuzzy matching, and entity resolution for users,
projects, priorities, task types, operational statuses, and deadlines.

All resolver functions return one of three shapes:

* Single confident match → the resolved object / value
* Ambiguous matches     → {"ambiguous": True, "<key>": [...]}
* No match              → None

Write-action callers MUST ask the user to clarify for ambiguous or
None results before proceeding.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from difflib import SequenceMatcher
from typing import Any, Optional
from uuid import UUID

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app import models
from app.auth import is_admin_or_manager

# ---------------------------------------------------------------------------
# Azerbaijani month names
# ---------------------------------------------------------------------------

_AZ_MONTHS: dict[str, int] = {
    "yanvar": 1, "fevral": 2, "mart": 3, "aprel": 4,
    "may": 5, "iyun": 6, "iyul": 7, "avqust": 8,
    "sentyabr": 9, "oktyabr": 10, "noyabr": 11, "dekabr": 12,
}

# ---------------------------------------------------------------------------
# Character normalisation
# ---------------------------------------------------------------------------

_AZ_CHAR_MAP: dict[str, str] = {
    "ə": "e",
    "ı": "i",
    "ö": "o",
    "ü": "u",
    "ş": "s",
    "ç": "c",
    "ğ": "g",
}


def normalize_az_text(text: Optional[str]) -> str:
    """Lower-case, collapse whitespace, and fold Azerbaijani special characters.

    Punctuation is stripped EXCEPT digits, letters, spaces, hyphens, and
    slashes (so codes like ``L026`` stay intact).
    """
    if not text:
        return ""

    cleaned = " ".join(str(text).lower().split()).strip()

    # Fold Azerbaijani characters
    for old, new in _AZ_CHAR_MAP.items():
        cleaned = cleaned.replace(old, new)

    # Remove punctuation but keep letters, digits, spaces, hyphens, slashes
    cleaned = re.sub(r"[^\w\s/-]", "", cleaned, flags=re.UNICODE)

    # Collapse whitespace again after punctuation removal
    cleaned = " ".join(cleaned.split()).strip()

    return cleaned


# ---------------------------------------------------------------------------
# Azerbaijani name-suffix stripping
# ---------------------------------------------------------------------------

# Suffixes ordered longest-first so we strip the most specific match.
# NOTE: "nə"/"na" are NOT suffixes — the "n" is part of the root
# (e.g. Nərmin + ə → Nərminə, Elmin + ə → Elminə).
# Dative suffixes are just "ə"/"a"; "yə"/"ya" for vowel-ending names;
# "ğa"/"gə" for some back-vowel words.
_AZ_NAME_SUFFIXES: list[str] = [
    # Possessive / genitive (longest first)
    "nin", "nın", "nun", "nün",
    "in", "ın", "un", "ün",
    # Dative / directional (buffer + vowel)
    "yə", "ya",                 # Əliyə→Əli, Hacıya→Hacı
    "ğa", "gə",                 # Hacığa→Hacı
    # Simple vowel suffixes (strip last)
    "ə", "a", "e",
]

# Words that look like they have suffixes but shouldn't be stripped
_AZ_NAME_STOP_WORDS: set[str] = {
    # Very short words — stripping would destroy them.
    # These are too short for safe suffix removal (the length guard
    # requires at least 2 chars remaining after stripping).
    # "əliyə" / "eliye" are NOT stop words — stripping "yə"→"Əli" is valid.
    "eli", "haci",
    "əli", "hacı",
}


def strip_az_suffixes(name: Optional[str]) -> str:
    """Remove common Azerbaijani dative/possessive suffixes from a name.

    Examples:
        Nərminə  → Nərmin
        Elminə   → Elmin
        Fəridə   → Fərid
        Hacıya   → Hacı
        Hacığa   → Hacı
        Əliyə    → Əli

    Does NOT over-strip: requires at least 2 characters remaining after
    the suffix is removed, and skips stop-words.
    """
    if not name:
        return ""

    cleaned = " ".join(str(name).split()).strip(" .,:;-")
    lowered = cleaned.lower()

    if lowered in _AZ_NAME_STOP_WORDS:
        return cleaned

    for suffix in _AZ_NAME_SUFFIXES:
        if lowered.endswith(suffix) and len(cleaned) > len(suffix) + 2:
            return cleaned[: -len(suffix)].strip(" .,:;-")

    return cleaned


# ---------------------------------------------------------------------------
# Fuzzy matching
# ---------------------------------------------------------------------------

_FUZZY_THRESHOLD: float = 0.82
_FUZZY_GAP_MIN: float = 0.08  # second-best must be at least this much lower


def fuzzy_match(
    query: str,
    candidates: list[str],
    *,
    threshold: float = _FUZZY_THRESHOLD,
    gap: float = _FUZZY_GAP_MIN,
) -> dict[str, Any]:
    """Multi-tier matching of *query* against a list of *candidates*.

    Matching tiers (first win):
    1. Exact raw match
    2. Exact normalized match (after ``normalize_az_text``)
    3. Startswith normalized match
    4. Contains normalized match
    5. Fuzzy similarity via ``difflib.SequenceMatcher``
       (only accepted if score ≥ *threshold* and second-best is
       clearly lower — gap ≥ *gap*)

    Returns
    -------
    dict with keys:
        result   — "single", "ambiguous", or "none"
        match    — the winning candidate (only for "single")
        score    — float 0.0–1.0 (only for "single" with fuzzy tier)
        matches  — list of candidate strings (only for "ambiguous")
    """
    if not query or not candidates:
        return {"result": "none"}

    query_stripped = query.strip()
    norm_query = normalize_az_text(query_stripped)

    # Tier 1 — exact raw
    exact_matches = [c for c in candidates if c.strip() == query_stripped]
    if len(exact_matches) == 1:
        return {"result": "single", "match": exact_matches[0]}
    if len(exact_matches) > 1:
        return {"result": "ambiguous", "matches": exact_matches}

    # Tier 2 — exact normalized
    norm_candidates = [normalize_az_text(c) for c in candidates]
    exact_norm_matches = [
        candidates[i] for i, nc in enumerate(norm_candidates) if nc == norm_query
    ]
    if len(exact_norm_matches) == 1:
        return {"result": "single", "match": exact_norm_matches[0]}
    if len(exact_norm_matches) > 1:
        return {"result": "ambiguous", "matches": exact_norm_matches}

    # Tier 3 — startswith normalized
    startswith_matches = [
        candidates[i]
        for i, nc in enumerate(norm_candidates)
        if nc.startswith(norm_query)
    ]
    if len(startswith_matches) == 1:
        return {"result": "single", "match": startswith_matches[0]}
    if len(startswith_matches) > 1:
        return {"result": "ambiguous", "matches": startswith_matches}

    # Tier 4 — contains normalized
    contains_matches = [
        candidates[i]
        for i, nc in enumerate(norm_candidates)
        if norm_query in nc
    ]
    if len(contains_matches) == 1:
        return {"result": "single", "match": contains_matches[0]}
    if len(contains_matches) > 1:
        return {"result": "ambiguous", "matches": contains_matches}

    # Tier 5 — fuzzy similarity
    if len(norm_query) < 2:
        return {"result": "none"}

    scored: list[tuple[float, str]] = []
    for i, nc in enumerate(norm_candidates):
        score = SequenceMatcher(None, norm_query, nc).ratio()
        scored.append((score, candidates[i]))

    scored.sort(key=lambda x: x[0], reverse=True)
    best_score, best_candidate = scored[0]

    if best_score < threshold:
        return {"result": "none"}

    # Check gap to second-best
    if len(scored) > 1:
        second_score = scored[1][0]
        if (best_score - second_score) < gap:
            # Collect all candidates within threshold range
            close = [c for s, c in scored if s >= threshold]
            return {"result": "ambiguous", "matches": close}

    return {"result": "single", "match": best_candidate, "score": best_score}


# ---------------------------------------------------------------------------
# User resolution
# ---------------------------------------------------------------------------

def _serialize_user(user: models.User) -> dict:
    return {
        "id": str(user.id),
        "full_name": user.full_name,
        "email": user.email,
        "role_id": user.role_id,
        "is_active": user.is_active,
    }


def resolve_user(
    db: Session,
    current_user: models.User,
    query: Optional[str] = None,
    *,
    user_id: Optional[str] = None,
) -> Any:
    """Resolve a user by ID, full name, first name, email, or dative name.

    Uses the existing RBAC / visible-user logic when applicable:
    non-admin/manager users can only resolve users they share tasks with.
    But for AI tool usage the full user list is available (admin/manager
    guard is at the tool level, not here).

    Supports dative Azerbaijani names:
        Nərminə → Nərmin, Elminə → Elmin, Fəridə → Fərid

    Returns
    -------
    models.User | dict | None
        Single user object, ambiguous dict, or None.
    """
    # ID-based lookup first
    if user_id:
        try:
            uid = UUID(user_id)
        except (ValueError, TypeError):
            pass
        else:
            user = db.query(models.User).filter(models.User.id == uid).first()
            if user is None:
                return None
            return user

    query_text = (query or "").strip()
    if not query_text:
        return None

    # Try raw query first (via ILIKE on full_name and email)
    raw_matches = (
        db.query(models.User)
        .filter(
            or_(
                models.User.full_name.ilike(f"%{query_text}%"),
                models.User.email.ilike(f"%{query_text}%"),
            )
        )
        .limit(6)
        .all()
    )

    if len(raw_matches) == 1:
        return raw_matches[0]

    if len(raw_matches) > 1:
        return {
            "ambiguous": True,
            "users": [_serialize_user(u) for u in raw_matches],
        }

    # Strip Azerbaijani suffixes and try again
    stripped = strip_az_suffixes(query_text)
    stripped_norm = normalize_az_text(stripped)

    if stripped != query_text or stripped_norm != query_text.lower():
        stripped_matches = (
            db.query(models.User)
            .filter(
                or_(
                    models.User.full_name.ilike(f"%{stripped}%"),
                    models.User.email.ilike(f"%{stripped}%"),
                )
            )
            .limit(6)
            .all()
        )

        if len(stripped_matches) == 1:
            return stripped_matches[0]
        if len(stripped_matches) > 1:
            return {
                "ambiguous": True,
                "users": [_serialize_user(u) for u in stripped_matches],
            }

    # Normalised fallback — scan all users (capped)
    all_users = db.query(models.User).limit(300).all()
    user_names = [u.full_name for u in all_users]
    user_emails = [u.email or "" for u in all_users]

    # Try fuzzy matching first against the original query
    norm_query = normalize_az_text(query_text)

    # Build combined label list for fuzzy_match
    labels = [f"{u.full_name} | {u.email}" for u in all_users]

    fm = fuzzy_match(query_text, user_names)
    if fm["result"] == "single":
        idx = user_names.index(fm["match"])
        return all_users[idx]
    if fm["result"] == "ambiguous":
        matched_users = [
            all_users[user_names.index(name)] for name in fm["matches"]
        ]
        return {
            "ambiguous": True,
            "users": [_serialize_user(u) for u in matched_users],
        }

    # Try with stripped query
    if stripped != query_text:
        fm_stripped = fuzzy_match(stripped, user_names)
        if fm_stripped["result"] == "single":
            idx = user_names.index(fm_stripped["match"])
            return all_users[idx]
        if fm_stripped["result"] == "ambiguous":
            matched_users = [
                all_users[user_names.index(name)]
                for name in fm_stripped["matches"]
            ]
            return {
                "ambiguous": True,
                "users": [_serialize_user(u) for u in matched_users],
            }

    # Email match as last resort for normalized
    email_matches = [
        u for i, u in enumerate(all_users)
        if norm_query in normalize_az_text(user_emails[i])
    ]
    if len(email_matches) == 1:
        return email_matches[0]
    if len(email_matches) > 1:
        return {
            "ambiguous": True,
            "users": [_serialize_user(u) for u in email_matches[:6]],
        }

    return None


# ---------------------------------------------------------------------------
# Project resolution
# ---------------------------------------------------------------------------


def _serialize_project(project: models.Project) -> dict:
    return {
        "id": str(project.id),
        "code": project.code,
        "name": project.name,
        "direction_program": project.direction_program,
        "owner": project.owner,
        "approach": project.approach,
        "description": project.description,
        "is_active": project.is_active,
    }


def _get_visible_project_query(db: Session, current_user: models.User):
    query = db.query(models.Project)
    if not is_admin_or_manager(current_user):
        task_subquery = (
            db.query(models.Task.project_id)
            .filter(models.Task.assigned_to == current_user.id)
            .filter(models.Task.project_id.isnot(None))
            .distinct()
            .subquery()
        )
        query = query.filter(models.Project.id.in_(task_subquery))
    return query


def resolve_project(
    db: Session,
    current_user: models.User,
    query: Optional[str] = None,
    *,
    project_id: Optional[str] = None,
) -> Any:
    """Resolve a project by ID, code, or name.

    Rules:
    * ID-based lookup is exact.
    * Code exact match (e.g. ``L026``) wins immediately — no fuzzy.
    * Name is fuzzy-allowed.
    * Broad queries that match many projects return ambiguous;
      never silently guess.

    Returns
    -------
    models.Project | dict | None
    """
    base_query = _get_visible_project_query(db, current_user)

    # ID-based lookup
    if project_id:
        try:
            pid = UUID(project_id)
        except (ValueError, TypeError):
            pass
        else:
            project = base_query.filter(models.Project.id == pid).first()
            return project

    query_text = (query or "").strip()
    if not query_text:
        return None

    # Code exact match (case-insensitive) — wins immediately
    code_match = (
        base_query.filter(models.Project.code.ilike(query_text)).limit(2).all()
    )
    if len(code_match) == 1:
        return code_match[0]
    if len(code_match) > 1:
        return {
            "ambiguous": True,
            "projects": [_serialize_project(p) for p in code_match],
        }

    # Name + code search via ILIKE
    matches = (
        base_query.filter(
            or_(
                models.Project.name.ilike(f"%{query_text}%"),
                models.Project.code.ilike(f"%{query_text}%"),
            )
        )
        .limit(6)
        .all()
    )

    if len(matches) == 1:
        return matches[0]

    if len(matches) > 1:
        return {
            "ambiguous": True,
            "projects": [_serialize_project(p) for p in matches],
        }

    # Fuzzy name match
    project_names = [p.name for p in base_query.limit(100).all()]
    all_projects = base_query.limit(100).all()

    fm = fuzzy_match(query_text, project_names)
    if fm["result"] == "single":
        idx = project_names.index(fm["match"])
        return all_projects[idx]
    if fm["result"] == "ambiguous":
        matched = [all_projects[project_names.index(n)] for n in fm["matches"]]
        return {
            "ambiguous": True,
            "projects": [_serialize_project(p) for p in matched],
        }

    return None


# ---------------------------------------------------------------------------
# Priority resolution
# ---------------------------------------------------------------------------

_PRIORITY_MAP: dict[str, str] = {
    # Azerbaijani
    "yüksək": "high", "yuksek": "high", "yüksək prioritet": "high",
    "yuksek prioritet": "high",
    "vacib": "high", "təcili": "high", "tecili": "high",
    # English
    "high": "high", "critical": "high", "urgent": "high",
    # Azerbaijani — medium
    "orta": "medium",
    # English
    "medium": "medium", "normal": "medium",
    # Azerbaijani — low
    "aşağı": "low", "asagi": "low", "zəif": "low", "zeif": "low",
    # English
    "low": "low",
}


def resolve_priority(text: Optional[str]) -> Optional[str]:
    """Map an Azerbaijani or English priority label to ``low`` / ``medium`` / ``high``.

    Returns None for unrecognised input.
    """
    if not text:
        return None
    normalized = normalize_az_text(text).replace("-", "_")
    return _PRIORITY_MAP.get(normalized)


# ---------------------------------------------------------------------------
# Task-type resolution
# ---------------------------------------------------------------------------

_TASK_TYPE_MAP: dict[str, str] = {
    # Azerbaijani
    "ümumi": "general", "umumi": "general",
    "birdəfəlik": "one_time", "birdefelik": "one_time",
    "təkrarlanan": "recurring", "tekrarlanan": "recurring",
    "layihə": "project", "layihe": "project",
    # English
    "general": "general",
    "one_time": "one_time", "one time": "one_time", "one-time": "one_time",
    "birdəfəlik tapşırıq": "one_time", "tek tapşırıq": "one_time",
    "recurring": "recurring",
    "project": "project",
}


def resolve_task_type(text: Optional[str]) -> Optional[str]:
    """Map an Azerbaijani or English task-type label to
    ``general`` / ``one_time`` / ``recurring`` / ``project``.

    Returns None for unrecognised input.
    """
    if not text:
        return None
    normalized = normalize_az_text(text)
    return _TASK_TYPE_MAP.get(normalized)


# ---------------------------------------------------------------------------
# Operational-status resolution
# ---------------------------------------------------------------------------

_OP_STATUS_MAP: dict[str, str] = {
    # Azerbaijani
    "yeni": "new",
    "gözləmədə": "waiting", "gözlemede": "waiting", "gozlemede": "waiting",
    "müddətində": "within_deadline",
    "icra müddətində": "within_deadline",
    "icra müddətinə var": "within_deadline",
    "icra muddetinde": "within_deadline",
    "icradadır": "active", "icradadir": "active",
    "aktiv": "active",
    "gecikmə": "overdue", "gecikme": "overdue",
    "gecikib": "overdue",
    "son tarixi keçən": "overdue",
    "tam icra": "completed", "tamamlanıb": "completed",
    "tamamlanib": "completed",
    # English / canonical
    "new": "new",
    "waiting": "waiting",
    "within_deadline": "within_deadline",
    "active": "active",
    "overdue": "overdue",
    "completed": "completed",
    # Kanban aliases (used in read/filter tools)
    "todo": "waiting",
    "in_progress": "active",
    "done": "completed",
}


def resolve_operational_status(text: Optional[str]) -> Optional[str]:
    """Map an Azerbaijani or English operational-status label to one of:
    ``new`` / ``waiting`` / ``within_deadline`` / ``active`` /
    ``overdue`` / ``completed``.

    Returns None for unrecognised input.
    """
    if not text:
        return None
    normalized = normalize_az_text(text)
    return _OP_STATUS_MAP.get(normalized)


# ---------------------------------------------------------------------------
# Deadline resolution
# ---------------------------------------------------------------------------

_AZ_DAY_RELATIVE: dict[str, str] = {
    "sabah": "tomorrow",
    "bugün": "today",
    "bugun": "today",            # normalized (ü→u)
    "dünən": "yesterday",
    "dunen": "yesterday",        # normalized
    "birisigün": "day_after_tomorrow",
    "birisigun": "day_after_tomorrow",  # normalized
    "bu həftə": "this_week",
    "bu hefte": "this_week",     # normalized
    "həftə sonu": "weekend",
    "hefte sonu": "weekend",     # normalized
    "bu həftə sonu": "weekend",
    "bu hefte sonu": "weekend",  # normalized
    "gələn həftə": "next_week",
    "gelen hefte": "next_week",  # normalized
    "gələn ay": "next_month",
    "gelen ay": "next_month",    # normalized
}

_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_AZ_DATE_RE = re.compile(
    r"^(\d{1,2})\s*(yanvar|fevral|mart|aprel|may|iyun|iyul|avqust|sentyabr|oktyabr|noyabr|dekabr)\s*(\d{4})?$",
    re.IGNORECASE,
)


def resolve_deadline(
    text: Optional[str],
    current_datetime: Optional[datetime] = None,
) -> Any:
    """Parse a natural-language or ISO deadline into a ``datetime``.

    Supports:
    * ``sabah``, ``bugün``
    * ``bu həftə`` / ``həftə sonu`` (next Saturday)
    * ISO date: ``YYYY-MM-DD``
    * Azerbaijani dates: ``1 iyul``, ``1 iyul 2026``

    Returns
    -------
    datetime | dict | None
        * datetime — successfully parsed (timezone-naive, in app TZ)
        * ``{"needs_more_info": True, ...}`` — ambiguous
        * None — empty/invalid input
    """
    if not text or not str(text).strip():
        return None

    value = str(text).strip()
    normalized = normalize_az_text(value)

    # Relative days
    if normalized in _AZ_DAY_RELATIVE:
        tz = timezone(timedelta(hours=4))
        now = current_datetime or datetime.now(tz)

        mapping = _AZ_DAY_RELATIVE[normalized]
        if mapping == "tomorrow":
            return (now + timedelta(days=1)).replace(
                hour=23, minute=59, second=59, microsecond=0
            )
        elif mapping == "today":
            return now.replace(hour=23, minute=59, second=59, microsecond=0)
        elif mapping == "yesterday":
            return (now - timedelta(days=1)).replace(
                hour=23, minute=59, second=59, microsecond=0
            )
        elif mapping == "day_after_tomorrow":
            return (now + timedelta(days=2)).replace(
                hour=23, minute=59, second=59, microsecond=0
            )
        elif mapping in ("this_week", "weekend"):
            # Next Saturday at EOD
            days_until_saturday = (5 - now.weekday()) % 7
            if days_until_saturday == 0:
                days_until_saturday = 7  # today *is* Saturday → next
            return (now + timedelta(days=days_until_saturday)).replace(
                hour=23, minute=59, second=59, microsecond=0
            )
        elif mapping == "next_week":
            # Next Monday at EOD
            days_until_monday = (7 - now.weekday()) % 7
            if days_until_monday == 0:
                days_until_monday = 7
            return (now + timedelta(days=days_until_monday)).replace(
                hour=23, minute=59, second=59, microsecond=0
            )
        elif mapping == "next_month":
            # First day of next month at EOD
            if now.month == 12:
                next_month = now.replace(year=now.year + 1, month=1, day=1)
            else:
                next_month = now.replace(month=now.month + 1, day=1)
            return next_month.replace(
                hour=23, minute=59, second=59, microsecond=0
            )

    # ISO date
    if _ISO_DATE_RE.match(value):
        try:
            parsed = datetime.strptime(value, "%Y-%m-%d")
            return parsed.replace(hour=23, minute=59, second=59, microsecond=0)
        except ValueError:
            return None

    # Azerbaijani date: "1 iyul" or "1 iyul 2026"
    az_match = _AZ_DATE_RE.match(normalized)
    if az_match:
        day_str, month_name, year_str = az_match.groups()
        day = int(day_str)
        month = _AZ_MONTHS[month_name.lower()]
        year = int(year_str) if year_str else datetime.now().year

        try:
            return datetime(year, month, day, 23, 59, 59)
        except ValueError:
            return None

    # Try ISO datetime with time component
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(
            tzinfo=None
        )
    except (ValueError, TypeError):
        pass

    # Cannot parse
    return {"needs_more_info": True, "query": value}


# ---------------------------------------------------------------------------
# Convenience: resolve all create_task fields
# ---------------------------------------------------------------------------


def resolve_create_task_fields(
    db: Session,
    current_user: models.User,
    data: dict[str, Any],
) -> dict[str, Any]:
    """Resolve every entity field for create_task in one pass.

    Returns a dict suitable for merging into the final create_task payload.
    Keys present: ``project``, ``assignee``, ``priority``, ``task_type``,
    ``status``, ``deadline``.

    Each entity value is either the resolved object, a scalar, or None
    (unresolved).  Write-action callers should check for ambiguous dicts
    and ask the user to clarify.
    """
    result: dict[str, Any] = {}

    # Project
    result["project"] = resolve_project(
        db=db,
        current_user=current_user,
        query=data.get("project_query"),
        project_id=data.get("project_id"),
    )

    # Assignee
    result["assignee"] = resolve_user(
        db=db,
        current_user=current_user,
        query=data.get("assignee_query"),
        user_id=data.get("assigned_to"),
    )

    # Priority
    result["priority"] = resolve_priority(
        data.get("priority")
    ) or "medium"

    # Task type
    result["task_type"] = resolve_task_type(
        data.get("task_type")
    ) or "general"

    # Status (kanban status for create)
    status_raw = data.get("status")
    if status_raw:
        op_status = resolve_operational_status(status_raw)
        # Map operational status back to kanban status
        if op_status == "waiting":
            result["status"] = "todo"
        elif op_status == "active":
            result["status"] = "in_progress"
        elif op_status == "completed":
            result["status"] = "done"
        else:
            result["status"] = "todo"
    else:
        result["status"] = "todo"

    # Deadline
    tz = timezone(timedelta(hours=4))
    result["deadline"] = resolve_deadline(
        data.get("deadline"),
        current_datetime=datetime.now(tz),
    )

    return result
