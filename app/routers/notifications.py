from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_
from sqlalchemy.orm import Session
from sqlalchemy.orm import joinedload

from app import models, schemas
from app.auth import get_current_user
from app.database import get_db


router = APIRouter(
    prefix="/notifications",
    tags=["Notifications"],
)

DEADLINE_REMINDER_WINDOW_DAYS = 3
DEADLINE_REMINDER_TYPE = "task_deadline_reminder"
DEADLINE_OVERDUE_TYPE = "task_overdue"


def is_task_completed(task: models.Task) -> bool:
    return task.status == "done" or task.operational_status == "completed"


def build_deadline_notification_key(*, task_id, notification_type, deadline_value):
    return f"{task_id}:{notification_type}:{deadline_value}"


def sync_deadline_notifications(db: Session, current_user: models.User):
    now = datetime.now()
    now_date = now.date()
    relevant_tasks = db.query(models.Task).filter(
        or_(
            models.Task.assigned_to == current_user.id,
            models.Task.created_by == current_user.id,
        )
    ).all()

    if not relevant_tasks:
        return

    task_ids = [task.id for task in relevant_tasks]
    existing_notifications = db.query(models.Notification).filter(
        models.Notification.user_id == current_user.id,
        models.Notification.task_id.in_(task_ids),
        models.Notification.type.in_([DEADLINE_REMINDER_TYPE, DEADLINE_OVERDUE_TYPE]),
    ).all()
    existing_keys = {
        (
            item.metadata_ or {}
        ).get("dedupe_key")
        for item in existing_notifications
    }

    created_any = False

    for task in relevant_tasks:
        if not task.deadline or is_task_completed(task):
            continue

        deadline_date = task.deadline.date()
        days_left = (deadline_date - now_date).days

        if days_left < 0:
            notification_type = DEADLINE_OVERDUE_TYPE
            title = "Tapşırıq gecikib"
            message = f"{task.title} tapşırığının son tarixi keçib"
        elif days_left <= DEADLINE_REMINDER_WINDOW_DAYS:
            notification_type = DEADLINE_REMINDER_TYPE
            title = "Son tarix yaxınlaşır"
            if days_left == 0:
                message = f"{task.title} tapşırığının son tarixi bu gündür"
            else:
                message = f"{task.title} tapşırığının son tarixinə {days_left} gün qalıb"
        else:
            continue

        dedupe_key = build_deadline_notification_key(
            task_id=task.id,
            notification_type=notification_type,
            deadline_value=deadline_date.isoformat(),
        )
        if dedupe_key in existing_keys:
            continue

        db.add(
            models.Notification(
                user_id=current_user.id,
                actor_id=None,
                task_id=task.id,
                type=notification_type,
                title=title,
                message=message,
                metadata_={
                    "deadline": deadline_date.isoformat(),
                    "days_left": days_left,
                    "dedupe_key": dedupe_key,
                },
            )
        )
        existing_keys.add(dedupe_key)
        created_any = True

    if created_any:
        db.commit()


@router.get("/", response_model=schemas.NotificationListResponse)
def get_notifications(
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    unread_only: bool = False,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    sync_deadline_notifications(db, current_user)

    query = db.query(models.Notification).options(
        joinedload(models.Notification.actor),
        joinedload(models.Notification.task),
    ).filter(models.Notification.user_id == current_user.id)

    if unread_only:
        query = query.filter(models.Notification.is_read.is_(False))

    total = query.count()
    items = (
        query.order_by(models.Notification.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    unread_count = db.query(models.Notification).filter(
        models.Notification.user_id == current_user.id,
        models.Notification.is_read.is_(False),
    ).count()

    return {
        "items": items,
        "total": total,
        "unread_count": unread_count,
    }


@router.get("/unread-count", response_model=schemas.NotificationUnreadCountResponse)
def get_unread_count(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    sync_deadline_notifications(db, current_user)

    unread_count = db.query(models.Notification).filter(
        models.Notification.user_id == current_user.id,
        models.Notification.is_read.is_(False),
    ).count()

    return {"unread_count": unread_count}


@router.patch("/{notification_id}/read", response_model=schemas.NotificationRead)
def mark_notification_read(
    notification_id: UUID,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    notification = db.query(models.Notification).options(
        joinedload(models.Notification.actor),
        joinedload(models.Notification.task),
    ).filter(
        models.Notification.id == notification_id,
        models.Notification.user_id == current_user.id,
    ).first()

    if notification is None:
        raise HTTPException(status_code=404, detail="Bildiriş tapılmadı")

    if not notification.is_read:
        notification.is_read = True
        notification.read_at = datetime.now(UTC)
        db.commit()
        db.refresh(notification)

    return notification


@router.patch("/read-all")
def mark_all_notifications_read(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    updated_count = db.query(models.Notification).filter(
        models.Notification.user_id == current_user.id,
        models.Notification.is_read.is_(False),
    ).update(
        {
            "is_read": True,
            "read_at": datetime.now(UTC),
        },
        synchronize_session=False,
    )
    db.commit()

    return {
        "updated_count": updated_count,
    }
