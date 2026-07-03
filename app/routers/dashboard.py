from collections import Counter
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app import models
from app.auth import get_current_user, is_admin_or_manager


router = APIRouter(
    prefix="/dashboard",
    tags=["Dashboard"]
)


TREND_DAYS = 7

VALID_OPERATIONAL_STATUSES = {
    "new",
    "waiting",
    "within_deadline",
    "active",
    "overdue",
    "completed",
}

OPERATIONAL_STATUS_ALIASES = {
    "yeni": "new",
    "gözləmədə": "waiting",
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
    "completed": "completed",
    "done": "completed",
    "in_progress": "active",
}


def build_day_series():
    today = datetime.now().date()
    return [today - timedelta(days=offset) for offset in range(TREND_DAYS - 1, -1, -1)]


def serialize_trend(day_series, counter):
    return [
        {
            "date": day.isoformat(),
            "count": counter.get(day, 0)
        }
        for day in day_series
    ]


def get_dashboard_operational_status(task: models.Task) -> str:
    raw_value = (task.operational_status or "").strip()
    if not raw_value:
        return "new"
    if raw_value in VALID_OPERATIONAL_STATUSES:
        return raw_value

    normalized = raw_value.lower()
    if normalized in OPERATIONAL_STATUS_ALIASES:
        return OPERATIONAL_STATUS_ALIASES[normalized]

    return "new"


def is_completed_for_dashboard(task: models.Task, operational_status: str) -> bool:
    return operational_status == "completed" or task.status == "done"


@router.get("/stats")
def get_dashboard_stats(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    # Admins and managers see all tasks.
    if is_admin_or_manager(current_user):
        task_query = db.query(models.Task)
    else:
        # Employees see only tasks assigned to them.
        task_query = db.query(models.Task).filter(
            models.Task.assigned_to == current_user.id
        )

    tasks = task_query.all()
    total_tasks = len(tasks)
    task_statuses = [
        (task, get_dashboard_operational_status(task))
        for task in tasks
    ]

    todo_tasks = sum(1 for _, status in task_statuses if status == "waiting")
    in_progress_tasks = sum(1 for _, status in task_statuses if status == "active")
    done_tasks = sum(1 for _, status in task_statuses if status == "completed")

    now = datetime.now()
    overdue_tasks = sum(
        1
        for task, operational_status in task_statuses
        if (
            task.deadline
            and task.deadline < now
            and not is_completed_for_dashboard(task, operational_status)
        )
    )

    day_series = build_day_series()
    day_set = set(day_series)

    created_counter = Counter()
    todo_counter = Counter()
    in_progress_counter = Counter()
    done_counter = Counter()
    overdue_counter = Counter()

    for task, operational_status in task_statuses:
        created_day = task.created_at.date() if task.created_at else None
        updated_day = task.updated_at.date() if task.updated_at else None
        deadline_day = task.deadline.date() if task.deadline else None

        if created_day in day_set:
            created_counter[created_day] += 1

            if operational_status == "waiting":
                todo_counter[created_day] += 1

            if operational_status == "active":
                in_progress_counter[created_day] += 1

        if operational_status == "completed" and updated_day in day_set:
            done_counter[updated_day] += 1

        if (
            not is_completed_for_dashboard(task, operational_status)
            and deadline_day in day_set
            and task.deadline
            and task.deadline < now
        ):
            overdue_counter[deadline_day] += 1

    # Client and user counts are meaningful only for admins/managers.
    if is_admin_or_manager(current_user):
        total_clients = db.query(models.Client).count()
        total_users = db.query(models.User).count()
    else:
        total_clients = None
        total_users = None

    return {
        "total_tasks": total_tasks,
        "todo_tasks": todo_tasks,
        "in_progress_tasks": in_progress_tasks,
        "done_tasks": done_tasks,
        "overdue_tasks": overdue_tasks,
        "total_clients": total_clients,
        "total_users": total_users,
        "task_trends": {
            "total": serialize_trend(day_series, created_counter),
            "todo": serialize_trend(day_series, todo_counter),
            "in_progress": serialize_trend(day_series, in_progress_counter),
            "done": serialize_trend(day_series, done_counter),
            "overdue": serialize_trend(day_series, overdue_counter),
        }
    }

