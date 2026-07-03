from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.utils import create_activity_log
from app import models
from app.auth import hash_password, is_admin_or_manager
from app.routers.users import EMPLOYEE_ROLE_ID, ensure_can_assign_role
from app.ai.resolvers import (
    normalize_az_text,
    strip_az_suffixes,
    fuzzy_match,
    resolve_user as _resolve_user,
    resolve_project as _resolve_project,
    resolve_priority,
    resolve_task_type as _resolve_task_type,
    resolve_operational_status,
    resolve_deadline,
    resolve_create_task_fields,
)


ALLOWED_STATUSES = {"todo", "in_progress", "done"}
ALLOWED_PRIORITIES = {"low", "medium", "high"}

ALLOWED_TASK_TYPES = {
    "general", "one_time", "recurring", "project",
}


STATUS_ALIASES = {
    "todo": "todo",
    "pending": "todo",
    "gozleme": "todo",
    "gozlemede": "todo",
    "gözləmə": "todo",
    "gözləmədə": "todo",

    "in_progress": "in_progress",
    "progress": "in_progress",
    "icrada": "in_progress",
    "icradadir": "in_progress",
    "icradadır": "in_progress",
    "davam": "in_progress",

    "done": "done",
    "completed": "done",
    "tamamlandi": "done",
    "tamamlandı": "done",
    "bitib": "done",
}


PRIORITY_ALIASES = {
    "low": "low",
    "asagi": "low",
    "aşağı": "low",
    "zeif": "low",
    "zəif": "low",

    "medium": "medium",
    "orta": "medium",
    "normal": "medium",

    "high": "high",
    "yuksek": "high",
    "yüksək": "high",
    "tecili": "high",
    "təcili": "high",
    "vacib": "high",
}


ADMIN_ROLE_ID = 1
MANAGER_ROLE_ID = 2

ROLE_ALIASES = {
    "admin": 1,
    "administrator": 1,
    "manager": 2,
    "menecer": 2,
    "employee": 3,
    "isci": 3,
    "işçi": 3,
    "istifadeci": 3,
    "istifadəçi": 3,
}


def parse_role_id(value):
    if value in (None, ""):
        return None

    normalized = str(value).strip().lower()

    if normalized in ROLE_ALIASES:
        return ROLE_ALIASES[normalized]

    try:
        return int(value)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="role_id yanlışdır")


def parse_is_active(value):
    if value in (None, ""):
        return None

    if isinstance(value, bool):
        return value

    normalized = str(value).strip().lower()

    if normalized in {"true", "active", "aktiv", "1", "yes", "beli", "bəli"}:
        return True

    if normalized in {"false", "inactive", "deaktiv", "0", "no", "xeyr"}:
        return False

    raise HTTPException(status_code=400, detail="is_active yanlışdır")


def ensure_can_modify_user(current_user: models.User, target_user: models.User):
    if current_user.role_id == ADMIN_ROLE_ID:
        return

    if current_user.role_id == MANAGER_ROLE_ID and target_user.role_id == EMPLOYEE_ROLE_ID:
        return

    raise HTTPException(
        status_code=403,
        detail="Manager yalnız employee istifadəçiləri yeniləyə bilər"
    )



def parse_uuid(value, field_name: str):
    if not value:
        return None

    try:
        return UUID(str(value))
    except ValueError:
        raise HTTPException(status_code=400, detail=f"{field_name} yanlışdır")


def normalize_status(value):
    if value in (None, ""):
        return None

    normalized = str(value).strip().lower().replace("-", "_")
    return STATUS_ALIASES.get(normalized)


def normalize_priority(value):
    """Map Azerbaijani/English priority labels to low/medium/high.

    Delegates to the centralized resolver so mappings stay in one place.
    """
    return resolve_priority(value)


def normalize_task_type(value):
    """Map Azerbaijani/English task-type labels to
    general/one_time/recurring/project.

    Delegates to the centralized resolver so mappings stay in one place.
    Returns None for unrecognised input.
    """
    if value in (None, ""):
        return "general"
    return _resolve_task_type(value)

def parse_datetime(value: Optional[str]):
    if not value:
        return None

    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None

    if parsed.tzinfo is not None:
        return parsed.astimezone(timezone.utc).replace(tzinfo=None)

    return parsed


def get_overdue_info(deadline, status: str):
    if not deadline or status == "done":
        return {
            "is_overdue": False,
            "overdue_minutes": 0,
            "overdue_text": None,
        }

    now = datetime.now(timezone.utc).replace(tzinfo=None)

    if deadline >= now:
        return {
            "is_overdue": False,
            "overdue_minutes": 0,
            "overdue_text": None,
        }

    diff = now - deadline
    total_minutes = int(diff.total_seconds() // 60)

    days = total_minutes // 1440
    hours = (total_minutes % 1440) // 60
    minutes = total_minutes % 60

    if days > 0:
        overdue_text = f"{days} gün {hours} saat gecikib"
    elif hours > 0:
        overdue_text = f"{hours} saat {minutes} dəqiqə gecikib"
    else:
        overdue_text = f"{minutes} dəqiqə gecikib"

    return {
        "is_overdue": True,
        "overdue_minutes": total_minutes,
        "overdue_text": overdue_text,
    }


def serialize_task(task: models.Task):
    overdue_info = get_overdue_info(task.deadline, task.status)

    return {
        "id": str(task.id),
        "title": task.title,
        "description": task.description,
        "task_type": task.task_type,
        "status": task.status,
        "priority": task.priority,
        "deadline": task.deadline.isoformat() if task.deadline else None,
        "client_id": str(task.client_id) if task.client_id else None,
        "client_name": task.client.company_name if task.client else None,
        "assigned_to": str(task.assigned_to) if task.assigned_to else None,
        "assigned_user": task.assigned_user.full_name if task.assigned_user else None,
        "is_overdue": overdue_info["is_overdue"],
        "overdue_minutes": overdue_info["overdue_minutes"],
        "overdue_text": overdue_info["overdue_text"],
    }


def serialize_client(client: models.Client):
    return {
        "id": str(client.id),
        "company_name": client.company_name,
        "contact_person": client.contact_person,
        "phone": client.phone,
        "email": client.email,
        "address": client.address,
        "notes": client.notes,
    }


def serialize_user(user: models.User):
    return {
        "id": str(user.id),
        "full_name": user.full_name,
        "email": user.email,
        "role_id": user.role_id,
        "is_active": user.is_active,
    }


def get_visible_task_query(db: Session, current_user: models.User):
    query = db.query(models.Task)

    if not is_admin_or_manager(current_user):
        query = query.filter(models.Task.assigned_to == current_user.id)

    return query

def resolve_client(db: Session, client_id=None, client_query: Optional[str] = None):
    parsed_client_id = parse_uuid(client_id, "client_id")

    if parsed_client_id:
        client = db.query(models.Client).filter(models.Client.id == parsed_client_id).first()
        if client is None:
            raise HTTPException(status_code=404, detail="Müştəri tapılmadı")
        return client

    query_text = (client_query or "").strip()
    if not query_text:
        return None

    matches = db.query(models.Client).filter(
        or_(
            models.Client.company_name.ilike(f"%{query_text}%"),
            models.Client.contact_person.ilike(f"%{query_text}%"),
            models.Client.email.ilike(f"%{query_text}%"),
            models.Client.phone.ilike(f"%{query_text}%"),
        )
    ).limit(6).all()

    if len(matches) == 1:
        return matches[0]

    if len(matches) > 1:
        return {
            "ambiguous": True,
            "customers": [serialize_client(client) for client in matches],
        }

    return None


def resolve_project(
    db: Session,
    current_user: models.User,
    project_id=None,
    project_query: Optional[str] = None,
):
    """Resolve a project by ID, code, or name.

    Delegates to the centralized resolver in app.ai.resolvers.
    Code exact match (e.g. L026) wins immediately.
    Backward-compatible signature.
    """
    return _resolve_project(
        db=db,
        current_user=current_user,
        query=project_query,
        project_id=project_id,
    )


# normalize_az_text, strip_az_suffixes, and fuzzy_match are now imported
# from app.ai.resolvers (centralized resolver layer).


def clean_person_query(value: str):
    """Alias for strip_az_suffixes — kept for backward compatibility."""
    return strip_az_suffixes(value)


def resolve_user(db: Session, current_user: models.User, user_id=None, user_query: Optional[str] = None):
    """Resolve a user by ID, full name, first name, email, or dative name.

    Delegates to the centralized resolver in app.ai.resolvers.
    Backward-compatible signature.
    """
    return _resolve_user(
        db=db,
        current_user=current_user,
        query=user_query,
        user_id=user_id,
    )


def resolve_task(
    db: Session,
    current_user: models.User,
    task_id=None,
    task_query: Optional[str] = None,
):
    parsed_task_id = parse_uuid(task_id, "task_id")

    query = get_visible_task_query(db, current_user)

    if parsed_task_id:
        task = query.filter(models.Task.id == parsed_task_id).first()
        if task is None:
            raise HTTPException(status_code=404, detail="Task tapılmadı")
        return task

    query_text = (task_query or "").strip()
    if not query_text:
        return None

    matches = query.filter(
        or_(
            models.Task.title.ilike(f"%{query_text}%"),
            models.Task.description.ilike(f"%{query_text}%"),
        )
    ).limit(6).all()

    if len(matches) == 1:
        return matches[0]

    if len(matches) > 1:
        return {
            "ambiguous": True,
            "tasks": [serialize_task(task) for task in matches],
        }

    return None

def normalize_limit(value, default: int = 20, maximum: int = 50):
    try:
        limit = int(value or default)
    except (TypeError, ValueError):
        limit = default

    if limit < 1:
        return default

    return min(limit, maximum)


def read_tasks(
    db: Session,
    current_user: models.User,
    filters: Optional[dict[str, Any]] = None,
):
    filters = filters or {}

    query_text = (filters.get("query") or "").strip()
    status = filters.get("status")
    priority = filters.get("priority")
    overdue = filters.get("overdue")
    client_query = (filters.get("client_query") or "").strip()
    assignee_query = (filters.get("assignee_query") or "").strip()
    deadline_before = parse_datetime(filters.get("deadline_before"))
    deadline_after = parse_datetime(filters.get("deadline_after"))
    exclude_completed = filters.get("exclude_completed")
    limit = normalize_limit(filters.get("limit"))
    query = get_visible_task_query(db, current_user)

    if query_text:
        query = query.filter(
            or_(
                models.Task.title.ilike(f"%{query_text}%"),
                models.Task.description.ilike(f"%{query_text}%"),
            )
        )

    if status in ALLOWED_STATUSES:
        query = query.filter(models.Task.status == status)

    if priority in ALLOWED_PRIORITIES:
        query = query.filter(models.Task.priority == priority)

    if overdue is True:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        query = query.filter(
            models.Task.deadline.isnot(None),
            models.Task.deadline < now,
            models.Task.status != "done",
        )

    if client_query:
        client_ids = db.query(models.Client.id).filter(
            or_(
                models.Client.company_name.ilike(f"%{client_query}%"),
                models.Client.contact_person.ilike(f"%{client_query}%"),
                models.Client.email.ilike(f"%{client_query}%"),
                models.Client.phone.ilike(f"%{client_query}%"),
            )
        )

        query = query.filter(models.Task.client_id.in_(client_ids))

    if assignee_query:
        assignee = resolve_user(
            db=db,
            current_user=current_user,
            user_query=assignee_query,
        )

        if assignee is None:
            return {
                "status": "completed",
                "tool": "read_tasks",
                "message": "İcraçıya aid tapşırıq tapılmadı.",
                "total_count": 0,
                "shown_count": 0,
                "tasks": [],
            }

        if isinstance(assignee, dict):
            return {
                "status": "needs_more_info",
                "tool": "read_tasks",
                "message": "Bir neçə oxşar istifadəçi tapıldı. Daha dəqiq icraçı adı yazın.",
                "data": assignee,
                "total_count": 0,
                "shown_count": 0,
                "tasks": [],
            }

        query = query.filter(models.Task.assigned_to == assignee.id)
        
    if deadline_before:
        query = query.filter(models.Task.deadline <= deadline_before)

    if deadline_after:
        query = query.filter(models.Task.deadline >= deadline_after)
    if exclude_completed is True:
        query = query.filter(models.Task.status != "done")
    total_count = query.count()
    if deadline_before or deadline_after:
        tasks = query.order_by(models.Task.deadline.asc()).limit(limit).all()
    else:
        tasks = query.order_by(models.Task.created_at.desc()).limit(limit).all()

    return {
        "status": "completed",
        "tool": "read_tasks",
        "message": (
            f"{total_count} tapşırıq tapıldı."
            if total_count <= len(tasks)
            else f"{total_count} tapşırıq tapıldı. İlk {len(tasks)} nəticə göstərilir."
        ),
        "total_count": total_count,
        "shown_count": len(tasks),
        "tasks": [serialize_task(task) for task in tasks],
    }


def read_customers(
    db: Session,
    current_user: models.User,
    filters: Optional[dict[str, Any]] = None,
):
    if not is_admin_or_manager(current_user):
        raise HTTPException(
            status_code=403,
            detail="Müştəriləri yalnız admin və manager görə bilər"
        )

    filters = filters or {}
    query_text = (filters.get("query") or "").strip()
    limit = normalize_limit(filters.get("limit"))

    query = db.query(models.Client)

    if query_text:
        query = query.filter(
            or_(
                models.Client.company_name.ilike(f"%{query_text}%"),
                models.Client.contact_person.ilike(f"%{query_text}%"),
                models.Client.email.ilike(f"%{query_text}%"),
                models.Client.phone.ilike(f"%{query_text}%"),
            )
        )

    total_count = query.count()
    customers = query.order_by(models.Client.created_at.desc()).limit(limit).all()

    return {
        "status": "completed",
        "tool": "read_customers",
        "message": (
            f"{total_count} müştəri tapıldı."
            if total_count <= len(customers)
            else f"{total_count} müştəri tapıldı. İlk {len(customers)} nəticə göstərilir."
        ),
        "total_count": total_count,
        "shown_count": len(customers),
        "customers": [serialize_client(customer) for customer in customers],
    }


def read_users(
    db: Session,
    current_user: models.User,
    filters: Optional[dict[str, Any]] = None,
):
    if not is_admin_or_manager(current_user):
        raise HTTPException(
            status_code=403,
            detail="İstifadəçiləri yalnız admin və manager görə bilər"
        )

    filters = filters or {}
    query_text = (filters.get("query") or "").strip()
    role_id = filters.get("role_id")
    is_active = filters.get("is_active")
    limit = normalize_limit(filters.get("limit"))

    query = db.query(models.User)

    if query_text:
        query = query.filter(
            or_(
                models.User.full_name.ilike(f"%{query_text}%"),
                models.User.email.ilike(f"%{query_text}%"),
            )
        )

    if role_id in {1, 2, 3}:
        query = query.filter(models.User.role_id == role_id)

    if isinstance(is_active, bool):
        query = query.filter(models.User.is_active == is_active)

    total_count = query.count()
    users = query.order_by(models.User.created_at.desc()).limit(limit).all()

    return {
        "status": "completed",
        "tool": "read_users",
        "message": (
            f"{total_count} istifadəçi tapıldı."
            if total_count <= len(users)
            else f"{total_count} istifadəçi tapıldı. İlk {len(users)} nəticə göstərilir."
        ),
        "total_count": total_count,
        "shown_count": len(users),
        "users": [serialize_user(user) for user in users],
    }

def create_task(
    db: Session,
    current_user: models.User,
    data: dict[str, Any],
):
    if not is_admin_or_manager(current_user):
        raise HTTPException(
            status_code=403,
            detail="Taskları yalnız admin və manager yarada bilər"
        )

    title = (data.get("title") or "").strip()
    if not title:
        return {
            "status": "needs_more_info",
            "tool": "create_task",
            "message": "Task yaratmaq üçün başlıq lazımdır.",
        }

    client = resolve_client(
        db=db,
        client_id=data.get("client_id"),
        client_query=data.get("client_query"),
    )

    if isinstance(client, dict):
        return {
            "status": "needs_more_info",
            "tool": "create_task",
            "message": "Bir neçə oxşar müştəri tapıldı. Daha dəqiq müştəri adı yazın.",
            "data": client,
        }

    project = resolve_project(
        db=db,
        current_user=current_user,
        project_id=data.get("project_id"),
        project_query=data.get("project_query"),
    )

    if isinstance(project, dict):
        return {
            "status": "needs_more_info",
            "tool": "create_task",
            "message": "Bir neçə oxşar layihə tapıldı. Daha dəqiq layihə adı yazın.",
            "data": project,
        }

    assignee = resolve_user(
        db=db,
        current_user=current_user,
        user_id=data.get("assigned_to"),
        user_query=data.get("assignee_query"),
    )

    if isinstance(assignee, dict):
        return {
            "status": "needs_more_info",
            "tool": "create_task",
            "message": "Bir neçə oxşar istifadəçi tapıldı. Daha dəqiq icraçı adı yazın.",
            "data": assignee,
        }
    if data.get("assigned_to") or data.get("assignee_query"):
        if assignee is None:
            return {
                "status": "needs_more_info",
                "tool": "create_task",
                "message": "İcraçı tapılmadı. Daha dəqiq istifadəçi adı və ya email yazın.",
            }
    task_type = normalize_task_type(data.get("task_type"))
    if task_type is None:
        raise HTTPException(status_code=400, detail="task_type yanlışdır")

    priority = normalize_priority(data.get("priority")) or "medium"
    if priority not in ALLOWED_PRIORITIES:
        raise HTTPException(status_code=400, detail="priority yanlışdır")

    status = normalize_status(data.get("status")) or "todo"
    if status not in ALLOWED_STATUSES:
        raise HTTPException(status_code=400, detail="status yanlışdır")

    # Resolve deadline: try natural-language parsing first, then ISO fallback
    deadline_raw = data.get("deadline")
    if deadline_raw:
        resolved_dl = resolve_deadline(deadline_raw)
        if isinstance(resolved_dl, dict):
            # "needs_more_info" from resolver — pass None, AI will ask user
            deadline = None
        elif resolved_dl is not None:
            deadline = resolved_dl
        else:
            deadline = parse_datetime(deadline_raw)
    else:
        deadline = None

    task = models.Task(
        title=title,
        description=data.get("description"),
        task_type=task_type,
        status=status,
        priority=priority,
        deadline=deadline,
        client_id=client.id if client else None,
        project_id=project.id if project else None,
        created_by=current_user.id,
        assigned_to=assignee.id if assignee else None,
    )

    db.add(task)
    db.flush()

    create_activity_log(
        db=db,
        user_id=current_user.id,
        action="TASK_CREATED",
        entity_type="task",
        entity_id=task.id,
        old_data=None,
        new_data=serialize_task(task),
    )

    db.commit()
    db.refresh(task)

    return {
        "status": "completed",
        "tool": "create_task",
        "message": "Task yaradıldı.",
        "task": serialize_task(task),
    }


def change_task_status(
    db: Session,
    current_user: models.User,
    data: dict[str, Any],
):
    task = resolve_task(
        db=db,
        current_user=current_user,
        task_id=data.get("task_id"),
        task_query=data.get("task_query"),
    )

    if task is None:
        return {
            "status": "needs_more_info",
            "tool": "change_task_status",
            "message": "Status dəyişmək üçün task ID-si və ya dəqiq task adı lazımdır.",
        }

    if isinstance(task, dict):
        return {
            "status": "needs_more_info",
            "tool": "change_task_status",
            "message": "Bir neçə oxşar task tapıldı. Daha dəqiq task adı yazın.",
            "data": task,
        }

    status = normalize_status(data.get("status"))
    if status not in ALLOWED_STATUSES:
        return {
            "status": "needs_more_info",
            "tool": "change_task_status",
            "message": "Status üçün todo, in_progress və ya done lazımdır.",
        }

    old_data = serialize_task(task)
    task.status = status

    create_activity_log(
        db=db,
        user_id=current_user.id,
        action="TASK_STATUS_UPDATED",
        entity_type="task",
        entity_id=task.id,
        old_data=old_data,
        new_data={"status": status},
    )

    db.commit()
    db.refresh(task)

    return {
        "status": "completed",
        "tool": "change_task_status",
        "message": "Task statusu yeniləndi.",
        "task": serialize_task(task),
    }


def add_task_comment(
    db: Session,
    current_user: models.User,
    data: dict[str, Any],
):
    comment_text = (data.get("comment") or "").strip()

    if not comment_text:
        return {
            "status": "needs_more_info",
            "tool": "add_task_comment",
            "message": "Comment əlavə etmək üçün mətn lazımdır.",
        }

    task = resolve_task(
        db=db,
        current_user=current_user,
        task_id=data.get("task_id"),
        task_query=data.get("task_query"),
    )

    if task is None:
        return {
            "status": "needs_more_info",
            "tool": "add_task_comment",
            "message": "Comment əlavə etmək üçün task ID-si və ya dəqiq task adı lazımdır.",
        }

    if isinstance(task, dict):
        return {
            "status": "needs_more_info",
            "tool": "add_task_comment",
            "message": "Bir neçə oxşar task tapıldı. Daha dəqiq task adı yazın.",
            "data": task,
        }

    task_comment = models.TaskComment(
        task_id=task.id,
        user_id=current_user.id,
        comment=comment_text,
    )

    db.add(task_comment)
    db.flush()

    create_activity_log(
        db=db,
        user_id=current_user.id,
        action="COMMENT_CREATED",
        entity_type="comment",
        entity_id=task_comment.id,
        old_data=None,
        new_data={
            "task_id": str(task.id),
            "comment": comment_text,
        },
    )

    db.commit()
    db.refresh(task_comment)

    return {
        "status": "completed",
        "tool": "add_task_comment",
        "message": "Comment əlavə edildi.",
        "comment": {
            "id": str(task_comment.id),
            "task_id": str(task_comment.task_id),
            "comment": task_comment.comment,
        },
    }

def create_customer(
    db: Session,
    current_user: models.User,
    data: dict[str, Any],
):
    if not is_admin_or_manager(current_user):
        raise HTTPException(
            status_code=403,
            detail="Müştərini yalnız admin və manager yarada bilər"
        )

    company_name = (data.get("company_name") or "").strip()

    if not company_name:
        return {
            "status": "needs_more_info",
            "tool": "create_customer",
            "message": "Müştəri yaratmaq üçün şirkət adı lazımdır.",
        }

    existing_customer = db.query(models.Client).filter(
        models.Client.company_name.ilike(company_name)
    ).first()

    if existing_customer:
        return {
            "status": "needs_more_info",
            "tool": "create_customer",
            "message": "Bu ada oxşar müştəri artıq mövcuddur.",
            "customer": serialize_client(existing_customer),
        }

    customer = models.Client(
        company_name=company_name,
        contact_person=data.get("contact_person"),
        phone=data.get("phone"),
        email=data.get("email"),
        address=data.get("address"),
        notes=data.get("notes"),
    )

    db.add(customer)
    db.flush()

    create_activity_log(
        db=db,
        user_id=current_user.id,
        action="CLIENT_CREATED",
        entity_type="client",
        entity_id=customer.id,
        old_data=None,
        new_data=serialize_client(customer),
    )

    db.commit()
    db.refresh(customer)

    return {
        "status": "completed",
        "tool": "create_customer",
        "message": "Müştəri yaradıldı.",
        "customer": serialize_client(customer),
    }


def update_customer(
    db: Session,
    current_user: models.User,
    data: dict[str, Any],
):
    if not is_admin_or_manager(current_user):
        raise HTTPException(
            status_code=403,
            detail="Müştərini yalnız admin və manager yeniləyə bilər"
        )

    customer = resolve_client(
        db=db,
        client_id=data.get("client_id"),
        client_query=data.get("client_query"),
    )

    if customer is None:
        return {
            "status": "needs_more_info",
            "tool": "update_customer",
            "message": "Müştərini yeniləmək üçün dəqiq müştəri adı və ya ID lazımdır.",
        }

    if isinstance(customer, dict):
        return {
            "status": "needs_more_info",
            "tool": "update_customer",
            "message": "Bir neçə oxşar müştəri tapıldı. Daha dəqiq müştəri adı yazın.",
            "data": customer,
        }

    update_data = {
        key: data.get(key)
        for key in [
            "company_name",
            "contact_person",
            "phone",
            "email",
            "address",
            "notes",
        ]
        if data.get(key) is not None and data.get(key) != ""
    }

    if not update_data:
        return {
            "status": "needs_more_info",
            "tool": "update_customer",
            "message": "Müştərini yeniləmək üçün dəyişəcək sahəni yazın.",
        }

    old_data = serialize_client(customer)

    for key, value in update_data.items():
        setattr(customer, key, value)

    create_activity_log(
        db=db,
        user_id=current_user.id,
        action="CLIENT_UPDATED",
        entity_type="client",
        entity_id=customer.id,
        old_data=old_data,
        new_data=update_data,
    )

    db.commit()
    db.refresh(customer)

    return {
        "status": "completed",
        "tool": "update_customer",
        "message": "Müştəri yeniləndi.",
        "customer": serialize_client(customer),
    }


def create_user(
    db: Session,
    current_user: models.User,
    data: dict[str, Any],
):
    if not is_admin_or_manager(current_user):
        raise HTTPException(
            status_code=403,
            detail="İstifadəçini yalnız admin və manager yarada bilər"
        )

    full_name = (data.get("full_name") or "").strip()
    email = (data.get("email") or "").strip()
    password = data.get("password")

    if not full_name or not email or not password:
        return {
            "status": "needs_more_info",
            "tool": "create_user",
            "message": "İstifadəçi yaratmaq üçün ad soyad, email və password lazımdır.",
        }

    if len(str(password)) < 6:
        raise HTTPException(
            status_code=400,
            detail="Password ən azı 6 simvol olmalıdır"
        )

    existing_user = db.query(models.User).filter(
        models.User.email == email
    ).first()

    if existing_user:
        raise HTTPException(
            status_code=400,
            detail="Bu email artıq qeydiyyatdan keçib"
        )

    requested_role_id = parse_role_id(data.get("role_id")) or EMPLOYEE_ROLE_ID
    ensure_can_assign_role(current_user, requested_role_id)

    is_active = parse_is_active(data.get("is_active"))

    user = models.User(
        full_name=full_name,
        email=email,
        password_hash=hash_password(str(password)),
        role_id=requested_role_id,
        is_active=True if is_active is None else is_active,
    )

    db.add(user)
    db.flush()

    create_activity_log(
        db=db,
        user_id=current_user.id,
        action="USER_CREATED",
        entity_type="user",
        entity_id=user.id,
        old_data=None,
        new_data=serialize_user(user),
    )

    db.commit()
    db.refresh(user)

    return {
        "status": "completed",
        "tool": "create_user",
        "message": "İstifadəçi yaradıldı.",
        "user": serialize_user(user),
    }


def update_user(
    db: Session,
    current_user: models.User,
    data: dict[str, Any],
):
    if not is_admin_or_manager(current_user):
        raise HTTPException(
            status_code=403,
            detail="İstifadəçini yalnız admin və manager yeniləyə bilər"
        )

    user = resolve_user(
        db=db,
        current_user=current_user,
        user_id=data.get("user_id"),
        user_query=data.get("user_query"),
    )

    if user is None:
        return {
            "status": "needs_more_info",
            "tool": "update_user",
            "message": "İstifadəçini yeniləmək üçün dəqiq ad, email və ya ID lazımdır.",
        }

    if isinstance(user, dict):
        return {
            "status": "needs_more_info",
            "tool": "update_user",
            "message": "Bir neçə oxşar istifadəçi tapıldı. Daha dəqiq istifadəçi adı yazın.",
            "data": user,
        }

    ensure_can_modify_user(current_user, user)

    update_data = {
        key: data.get(key)
        for key in ["full_name", "email"]
        if data.get(key) is not None and data.get(key) != ""
    }

    role_id = parse_role_id(data.get("role_id"))
    if role_id is not None:
        ensure_can_assign_role(current_user, role_id)
        update_data["role_id"] = role_id

    is_active = parse_is_active(data.get("is_active"))
    if is_active is not None:
        update_data["is_active"] = is_active

    password = data.get("password")
    password_changed = False

    if password:
        if len(str(password)) < 6:
            raise HTTPException(
                status_code=400,
                detail="Password ən azı 6 simvol olmalıdır"
            )

        user.password_hash = hash_password(str(password))
        password_changed = True

    if not update_data and not password_changed:
        return {
            "status": "needs_more_info",
            "tool": "update_user",
            "message": "İstifadəçini yeniləmək üçün dəyişəcək sahəni yazın.",
        }

    if "email" in update_data:
        existing_user = db.query(models.User).filter(
            models.User.email == update_data["email"],
            models.User.id != user.id,
        ).first()

        if existing_user:
            raise HTTPException(
                status_code=400,
                detail="Bu email artıq qeydiyyatdan keçib"
            )

    old_data = serialize_user(user)

    for key, value in update_data.items():
        setattr(user, key, value)

    log_new_data = update_data.copy()

    if password_changed:
        log_new_data["password_changed"] = True

    create_activity_log(
        db=db,
        user_id=current_user.id,
        action="USER_UPDATED",
        entity_type="user",
        entity_id=user.id,
        old_data=old_data,
        new_data=log_new_data,
    )

    db.commit()
    db.refresh(user)

    return {
        "status": "completed",
        "tool": "update_user",
        "message": "İstifadəçi yeniləndi.",
        "user": serialize_user(user),
    }


def get_dashboard_summary(
    db: Session,
    current_user: models.User,
):
    """Return dashboard KPIs matching the real Dashboard page logic exactly.

    The dashboard page counts by SXA ``operational_status`` (not Kanban
    ``status``).  This function mirrors the frontend helpers:
      * getDashboardOperationalStatusKey()
      * isTaskOverdue() / dashboardOverdueTasks
      * isTaskDueThisWeek() / dueThisWeekTasks
      * hasTaskAssignee() / unassignedTasks
    """
    from datetime import timedelta

    tasks = get_visible_task_query(db, current_user).all()
    now = datetime.now()
    today_date = now.date()

    # ------------------------------------------------------------------
    # Mirrors frontend statusAliasMap inside getDashboardOperationalStatusKey()
    # ------------------------------------------------------------------
    _VALID_OP_KEYS = {
        "new", "waiting", "within_deadline", "active", "overdue", "completed",
    }

    _OP_ALIASES = {
        "yeni": "new",
        "gözləmədə": "waiting",
        "gözlemede": "waiting",
        "gozlemede": "waiting",
        "icra müddətində": "within_deadline",
        "icra müddətinə var": "within_deadline",
        "icra muddetinde": "within_deadline",
        "icradadır": "active",
        "icradadir": "active",
        "gecikmə": "overdue",
        "gecikme": "overdue",
        "tam icra": "completed",
        "tamamlanıb": "completed",
        "tamamlanib": "completed",
        "done": "completed",
        "completed": "completed",
        "in_progress": "active",
    }

    def _resolve_op_status(task: models.Task) -> str:
        """Return the dashboard-normalised operational_status key."""
        raw = task.operational_status
        if not raw:
            return "new"
        else:
            raw = raw.strip()

        # Already a known English key (fast path)
        if raw in _VALID_OP_KEYS:
            return raw

        # Try matching via the alias table (case-folded, AZ-normalised)
        normalized = raw.lower()
        if normalized in _OP_ALIASES:
            return _OP_ALIASES[normalized]

        # Try a second pass with Azerbaijani character folding
        for old, new in (
            ("ə", "e"), ("ı", "i"), ("ö", "o"), ("ü", "u"),
            ("ş", "s"), ("ç", "c"), ("ğ", "g"),
        ):
            normalized = normalized.replace(old, new)
        if normalized in _OP_ALIASES:
            return _OP_ALIASES[normalized]

        return "new"

    # Resolve every task's operational status once
    # Pair each task with its resolved op_status for efficient reuse
    _task_ops = [(t, _resolve_op_status(t)) for t in tasks]

    # Operational-status distribution
    new_count = sum(1 for _, s in _task_ops if s == "new")
    waiting_count = sum(1 for _, s in _task_ops if s == "waiting")
    within_deadline_count = sum(1 for _, s in _task_ops if s == "within_deadline")
    active_count = sum(1 for _, s in _task_ops if s == "active")
    completed_count = sum(1 for _, s in _task_ops if s == "completed")

    total = len(tasks)

    def _task_date_value(task: models.Task):
        value = task.current_deadline or task.deadline or getattr(task, "due_date", None)
        if value is None:
            return None
        if isinstance(value, datetime):
            return value.date()
        return value

    def _is_completed(task: models.Task, op_stat: str) -> bool:
        return op_stat == "completed" or task.status == "done"

    official_delayed_ids = set()
    date_overdue_ids = set()
    date_overdue_not_marked_ids = set()

    for task, op_stat in _task_ops:
        task_id = task.id
        is_official_delayed = op_stat == "overdue"
        deadline_date = _task_date_value(task)
        is_date_overdue = (
            deadline_date is not None
            and deadline_date < today_date
            and not _is_completed(task, op_stat)
        )

        if is_official_delayed:
            official_delayed_ids.add(task_id)
        if is_date_overdue:
            date_overdue_ids.add(task_id)
            if not is_official_delayed:
                date_overdue_not_marked_ids.add(task_id)

    official_delayed = len(official_delayed_ids)
    date_overdue = len(date_overdue_ids)
    date_overdue_not_marked = len(date_overdue_not_marked_ids)
    total_delayed_ids = official_delayed_ids | date_overdue_ids
    total_delayed = len(total_delayed_ids)

    # ------------------------------------------------------------------
    # "Bu həftə bitməli"
    # Mirrors frontend isTaskDueThisWeek() + dashboard filter
    # ------------------------------------------------------------------
    week_end = today_date + timedelta(days=7)
    due_this_week = 0
    for task, op_stat in _task_ops:
        deadline_date = _task_date_value(task)
        if not deadline_date:
            continue
        if _is_completed(task, op_stat):
            continue
        if deadline_date and today_date <= deadline_date <= week_end:
            due_this_week += 1

    # ------------------------------------------------------------------
    # "İcraçısız"
    # Mirrors frontend hasTaskAssignee() / unassignedTasks
    # ------------------------------------------------------------------
    unassigned = sum(1 for t, _ in _task_ops if t.assigned_to is None)

    # ------------------------------------------------------------------
    # Backward-compatible keys with corrected business meanings.
    # "Gözləmədə" is only operational_status=waiting, not technical todo/new.
    todo_compat = waiting_count
    in_progress_compat = active_count
    done_compat = completed_count

    return {
        "status": "completed",
        "tool": "get_dashboard_summary",
        "message": "Dashboard xülasəsi hazırlandı.",
        "summary": {
            # ---- Primary dashboard KPIs (Azerbaijani labels) ----
            "umumi_tapsiriqlar": total,
            "icradadir": active_count,
            "gozlemede": waiting_count,
            "son_tarixi_kecenler": total_delayed,
            "geciken": total_delayed,
            "official_delayed_tasks": official_delayed,
            "date_overdue_tasks": date_overdue,
            "date_overdue_not_marked_tasks": date_overdue_not_marked,
            "total_delayed_tasks": total_delayed,
            "bu_hefte_bitmeli": due_this_week,
            "icracisiz": unassigned,
            # ---- Operational-status breakdown (mirrors status chart) ----
            "status_bolgusu": {
                "yeni": new_count,
                "gozlemede": waiting_count,
                "icra_muddetinde": within_deadline_count,
                "icradadir": active_count,
                "gecikme_statusunda": official_delayed,
                "tam_icra": completed_count,
                "son_tarixi_kecenler": total_delayed,
            },
            # ---- Backward-compatible old keys ----
            "total_tasks": total,
            "todo_tasks": todo_compat,
            "in_progress_tasks": in_progress_compat,
            "done_tasks": done_compat,
            "overdue_tasks": total_delayed,
        },
    }


def serialize_project(project: models.Project):
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


def serialize_project_stage(stage: models.ProjectStage):
    return {
        "id": str(stage.id),
        "project_id": str(stage.project_id),
        "name": stage.name,
        "weight_percent": float(stage.weight_percent) if stage.weight_percent else None,
        "planned_start": stage.planned_start.isoformat() if stage.planned_start else None,
        "planned_end": stage.planned_end.isoformat() if stage.planned_end else None,
        "actual_end": stage.actual_end.isoformat() if stage.actual_end else None,
        "status": stage.status,
        "delay_reason": stage.delay_reason,
        "completion_percent": float(stage.completion_percent) if stage.completion_percent else None,
        "sort_order": stage.sort_order,
    }


def get_visible_project_query(db: Session, current_user: models.User):
    query = db.query(models.Project)

    if not is_admin_or_manager(current_user):
        # Employee: only projects connected to their visible tasks
        task_subquery = (
            db.query(models.Task.project_id)
            .filter(models.Task.assigned_to == current_user.id)
            .filter(models.Task.project_id.isnot(None))
            .distinct()
            .subquery()
        )
        query = query.filter(models.Project.id.in_(task_subquery))

    return query


def read_projects(
    db: Session,
    current_user: models.User,
    filters: Optional[dict[str, Any]] = None,
):
    filters = filters or {}
    query_text = (filters.get("query") or "").strip()
    direction_program = (filters.get("direction_program") or "").strip()
    owner = (filters.get("owner") or "").strip()
    is_active = filters.get("is_active")
    include_task_counts = filters.get("include_task_counts", False)
    limit = normalize_limit(filters.get("limit"), default=50, maximum=100)

    query = get_visible_project_query(db, current_user)

    if query_text:
        query = query.filter(
            or_(
                models.Project.name.ilike(f"%{query_text}%"),
                models.Project.code.ilike(f"%{query_text}%"),
                models.Project.description.ilike(f"%{query_text}%"),
            )
        )

    if direction_program:
        query = query.filter(
            models.Project.direction_program.ilike(f"%{direction_program}%")
        )

    if owner:
        query = query.filter(
            models.Project.owner.ilike(f"%{owner}%")
        )

    if isinstance(is_active, bool):
        query = query.filter(models.Project.is_active == is_active)

    total_count = query.count()
    projects = query.order_by(models.Project.name.asc()).limit(limit).all()

    now = datetime.now(timezone.utc).replace(tzinfo=None)

    result = []
    for project in projects:
        item = serialize_project(project)
        if include_task_counts:
            tasks = (
                db.query(models.Task)
                .filter(models.Task.project_id == project.id)
                .all()
            )
            total_tasks = len(tasks)
            active_tasks = sum(
                1 for t in tasks if t.status != "done"
            )
            overdue_tasks = sum(
                1 for t in tasks
                if t.deadline and t.deadline < now and t.status != "done"
            )
            done_tasks = sum(
                1 for t in tasks if t.status == "done"
            )
            item["task_counts"] = {
                "total_tasks": total_tasks,
                "active_tasks": active_tasks,
                "overdue_tasks": overdue_tasks,
                "completed_tasks": done_tasks,
            }
        result.append(item)

    return {
        "status": "completed",
        "tool": "read_projects",
        "message": (
            f"{total_count} layihə tapıldı."
            if total_count <= len(result)
            else f"{total_count} layihə tapıldı. İlk {len(result)} nəticə göstərilir."
        ),
        "total_count": total_count,
        "shown_count": len(result),
        "projects": result,
    }


def read_project_stages(
    db: Session,
    current_user: models.User,
    filters: Optional[dict[str, Any]] = None,
):
    filters = filters or {}
    project_query_text = (filters.get("project_query") or "").strip()
    project_id = parse_uuid(filters.get("project_id"), "project_id")
    limit = normalize_limit(filters.get("limit"), default=50, maximum=100)

    # First resolve the project, scoped by visibility
    project_query = get_visible_project_query(db, current_user)

    if project_id:
        project = project_query.filter(models.Project.id == project_id).first()
        if project is None:
            return {
                "status": "completed",
                "tool": "read_project_stages",
                "message": "Layihə tapılmadı və ya ona giriş icazəniz yoxdur.",
                "total_count": 0,
                "shown_count": 0,
                "stages": [],
            }
    elif project_query_text:
        projects = project_query.filter(
            or_(
                models.Project.name.ilike(f"%{project_query_text}%"),
                models.Project.code.ilike(f"%{project_query_text}%"),
            )
        ).limit(5).all()

        if len(projects) == 0:
            return {
                "status": "completed",
                "tool": "read_project_stages",
                "message": "Layihə tapılmadı.",
                "total_count": 0,
                "shown_count": 0,
                "stages": [],
            }
        if len(projects) > 1:
            return {
                "status": "needs_more_info",
                "tool": "read_project_stages",
                "message": "Bir neçə oxşar layihə tapıldı. Daha dəqiq layihə adı və ya ID yazın.",
                "projects": [serialize_project(p) for p in projects],
            }
        project = projects[0]
    else:
        # No filter — list all stages across visible projects
        visible_project_ids = [
            r[0] for r in project_query.with_entities(models.Project.id).all()
        ]
        if not visible_project_ids:
            return {
                "status": "completed",
                "tool": "read_project_stages",
                "message": "Mərhələ tapılmadı.",
                "total_count": 0,
                "shown_count": 0,
                "stages": [],
            }
        stages = (
            db.query(models.ProjectStage)
            .filter(models.ProjectStage.project_id.in_(visible_project_ids))
            .order_by(models.ProjectStage.project_id, models.ProjectStage.sort_order)
            .limit(limit)
            .all()
        )
        stage_list = [serialize_project_stage(s) for s in stages]
        # Attach project name for context
        project_map = {
            p.id: p
            for p in db.query(models.Project).filter(
                models.Project.id.in_(visible_project_ids)
            ).all()
        }
        for item in stage_list:
            pid = UUID(item["project_id"])
            item["project_name"] = project_map[pid].name if pid in project_map else None

        return {
            "status": "completed",
            "tool": "read_project_stages",
            "message": (
                f"{len(stage_list)} mərhələ tapıldı."
            ),
            "total_count": len(stage_list),
            "shown_count": len(stage_list),
            "stages": stage_list,
        }

    # Single project case
    stages = (
        db.query(models.ProjectStage)
        .filter(models.ProjectStage.project_id == project.id)
        .order_by(models.ProjectStage.sort_order)
        .all()
    )

    stage_list = [serialize_project_stage(s) for s in stages]
    for item in stage_list:
        item["project_name"] = project.name

    return {
        "status": "completed",
        "tool": "read_project_stages",
        "message": (
            f"'{project.name}' layihəsi üçün {len(stage_list)} mərhələ tapıldı."
        ),
        "total_count": len(stage_list),
        "shown_count": len(stage_list),
        "stages": stage_list,
    }


def get_project_monitoring_summary(
    db: Session,
    current_user: models.User,
):
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    # Projects scoped by visibility
    project_query = get_visible_project_query(db, current_user)
    all_projects = project_query.all()
    total_projects = len(all_projects)
    active_projects = sum(1 for p in all_projects if p.is_active)

    # Task stats scoped by visibility
    task_query = get_visible_task_query(db, current_user)
    all_tasks = task_query.all()

    project_ids = [p.id for p in all_projects]

    # Per-project task stats
    project_task_map: dict[UUID, list[models.Task]] = {}
    for task in all_tasks:
        if task.project_id and task.project_id in project_ids:
            project_task_map.setdefault(task.project_id, []).append(task)

    projects_with_overdue_tasks = 0
    project_details: list[dict[str, Any]] = []

    for project in all_projects:
        project_tasks = project_task_map.get(project.id, [])

        total = len(project_tasks)
        completed = sum(1 for t in project_tasks if t.status == "done")
        open_t = total - completed
        overdue = sum(
            1 for t in project_tasks
            if t.deadline and t.deadline < now and t.status != "done"
        )

        detail = {
            "project_name": project.name,
            "total_tasks": total,
            "open_tasks": open_t,
            "overdue_tasks": overdue,
            "completed_tasks": completed,
        }

        if overdue > 0:
            projects_with_overdue_tasks += 1

        if open_t > 0 or total > 0:
            project_details.append(detail)

    # Sort for top-N; projects without tasks sort to the end
    project_details.sort(
        key=lambda d: (d["overdue_tasks"], d["open_tasks"]),
        reverse=True,
    )

    completed_projects = sum(
        1 for p in all_projects
        if not project_task_map.get(p.id, [])
        or all(t.status == "done" for t in project_task_map[p.id])
    )

    # Stage statistics (if stages exist)
    stage_status_distribution: dict[str, int] = {}
    delay_reasons: dict[str, int] = {}

    if project_ids:
        visible_project_ids_set = set(project_ids)
        stages = (
            db.query(models.ProjectStage)
            .filter(models.ProjectStage.project_id.in_(project_ids))
            .all()
        )
        for stage in stages:
            if stage.status:
                stage_status_distribution[stage.status] = (
                    stage_status_distribution.get(stage.status, 0) + 1
                )
            if stage.delay_reason:
                trimmed = stage.delay_reason.strip()[:100]
                delay_reasons[trimmed] = delay_reasons.get(trimmed, 0) + 1

    summary = {
        "total_projects": total_projects,
        "active_projects": active_projects,
        "projects_with_overdue_tasks": projects_with_overdue_tasks,
        "completed_projects": completed_projects,
        "top_projects_by_open_tasks": [
            d for d in project_details if d["open_tasks"] > 0
        ][:5],
        "top_projects_by_overdue_tasks": [
            d for d in project_details if d["overdue_tasks"] > 0
        ][:5],
        "stage_status_distribution": stage_status_distribution,
        "delay_reason_summary": delay_reasons,
    }

    return {
        "status": "completed",
        "tool": "get_project_monitoring_summary",
        "message": "Layihə monitorinq xülasəsi hazırlandı.",
        "summary": summary,
    }


def get_assignee_workload_summary(
    db: Session,
    current_user: models.User,
):
    """Return per-assignee workload breakdown for the visible task set."""
    from datetime import timedelta

    tasks = get_visible_task_query(db, current_user).all()
    now = datetime.now()
    today_date = now.date()
    week_end = today_date + timedelta(days=7)

    # Group tasks by assignee
    assignee_map: dict[str, dict] = {}
    unassigned_tasks = []

    for task in tasks:
        if task.assigned_to is None:
            unassigned_tasks.append(task)
            continue

        key = str(task.assigned_to)
        if key not in assignee_map:
            assignee_map[key] = {
                "assignee_name": task.assigned_user.full_name if task.assigned_user else "Bilinməyən",
                "total_tasks": 0,
                "open_tasks": 0,
                "overdue_tasks": 0,
                "completed_tasks": 0,
                "high_priority_open_tasks": 0,
                "due_this_week_tasks": 0,
            }

        info = assignee_map[key]
        info["total_tasks"] += 1

        if task.status == "done":
            info["completed_tasks"] += 1
        else:
            info["open_tasks"] += 1

            if task.priority and task.priority in ("high", "yüksək", "yuksek", "vacib", "təcili"):
                info["high_priority_open_tasks"] += 1

            if task.deadline and task.deadline < now:
                info["overdue_tasks"] += 1

            if task.deadline:
                dl = task.deadline.date()
                if today_date <= dl <= week_end:
                    info["due_this_week_tasks"] += 1

    # Compute workload level
    def _workload_level(open_t: int) -> str:
        if open_t >= 20:
            return "critical"
        if open_t >= 10:
            return "high"
        if open_t >= 5:
            return "normal"
        return "low"

    assignee_list = []
    for info in assignee_map.values():
        info["workload_level"] = _workload_level(info["open_tasks"])
        assignee_list.append(info)

    # Sort by open_tasks descending
    assignee_list.sort(key=lambda a: a["open_tasks"], reverse=True)

    # Unassigned stats
    unassigned_total = len(unassigned_tasks)
    unassigned_overdue = sum(
        1 for t in unassigned_tasks
        if t.deadline and t.deadline < now and t.status != "done"
    )
    unassigned_open = sum(1 for t in unassigned_tasks if t.status != "done")

    return {
        "status": "completed",
        "tool": "get_assignee_workload_summary",
        "message": f"{len(assignee_list)} icraçı üzrə yük analizi hazırlandı.",
        "summary": {
            "total_assignees": len(assignee_list),
            "total_tasks": len(tasks),
            "assignee_workload": assignee_list,
            "unassigned": {
                "total_tasks": unassigned_total,
                "open_tasks": unassigned_open,
                "overdue_tasks": unassigned_overdue,
            },
        },
    }


def run_tool(
    tool_name: str,
    arguments: dict[str, Any],
    db: Session,
    current_user: models.User,
):
    if tool_name == "read_tasks":
        return read_tasks(
            db=db,
            current_user=current_user,
            filters=arguments,
        )

    if tool_name == "read_customers":
        return read_customers(
            db=db,
            current_user=current_user,
            filters=arguments,
        )

    if tool_name == "read_users":
        return read_users(
            db=db,
            current_user=current_user,
            filters=arguments,
        )

    if tool_name == "get_dashboard_summary":
        return get_dashboard_summary(
            db=db,
            current_user=current_user,
        )

    if tool_name == "create_task":
        return create_task(
            db=db,
            current_user=current_user,
            data=arguments,
        )

    if tool_name == "change_task_status":
        return change_task_status(
            db=db,
            current_user=current_user,
            data=arguments,
        )

    if tool_name == "add_task_comment":
        return add_task_comment(
            db=db,
            current_user=current_user,
            data=arguments,
        )
    if tool_name == "create_customer":
        return create_customer(
            db=db,
            current_user=current_user,
            data=arguments,
        )

    if tool_name == "update_customer":
        return update_customer(
            db=db,
            current_user=current_user,
            data=arguments,
        )
    
    if tool_name == "create_user":
        return create_user(
            db=db,
            current_user=current_user,
            data=arguments,
        )

    if tool_name == "update_user":
        return update_user(
            db=db,
            current_user=current_user,
            data=arguments,
        )

    if tool_name == "read_projects":
        return read_projects(
            db=db,
            current_user=current_user,
            filters=arguments,
        )

    if tool_name == "read_project_stages":
        return read_project_stages(
            db=db,
            current_user=current_user,
            filters=arguments,
        )

    if tool_name == "get_project_monitoring_summary":
        return get_project_monitoring_summary(
            db=db,
            current_user=current_user,
        )

    if tool_name == "get_assignee_workload_summary":
        return get_assignee_workload_summary(
            db=db,
            current_user=current_user,
        )

    raise HTTPException(
        status_code=400,
        detail=f"Naməlum AI tool: {tool_name}"
    )
