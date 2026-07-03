from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.orm import Session
from uuid import UUID
from app.utils import create_activity_log
from app.database import get_db
from app import models, schemas
from app.auth import get_current_user, is_admin_or_manager, require_admin_or_manager
import shutil
from fastapi.encoders import jsonable_encoder
from app.upload_security import (
    UPLOAD_DB_PREFIX,
    build_task_upload_dir,
    build_unique_upload_path,
    resolve_attachment_path,
    validate_upload_file,
)


def create_notification(
    db: Session,
    *,
    user_id,
    actor_id,
    task_id,
    type: str,
    title: str,
    message: str,
    metadata: dict | None = None,
):
    if actor_id and user_id == actor_id:
        return None

    notification = models.Notification(
        user_id=user_id,
        actor_id=actor_id,
        task_id=task_id,
        type=type,
        title=title,
        message=message,
        metadata_=metadata or {},
    )
    db.add(notification)
    return notification


def get_related_task_notification_user_ids(task: models.Task) -> list:
    recipient_ids = []

    for user_id in (task.created_by, task.assigned_to):
        if user_id and user_id not in recipient_ids:
            recipient_ids.append(user_id)

    return recipient_ids


def is_task_completed(task: models.Task) -> bool:
    return task.status == "done" or task.operational_status == "completed"


def notify_task_assigned(db: Session, task: models.Task, actor_id):
    if task.assigned_to and task.assigned_to != actor_id:
        create_notification(
            db,
            user_id=task.assigned_to,
            actor_id=actor_id,
            task_id=task.id,
            type="task_assigned",
            title="Yeni tapşırıq",
            message=f"Sizə yeni tapşırıq təyin edildi: {task.title}",
        )


def build_notification_changes(old_data: dict, task: models.Task, fields: set[str]):
    changes = []
    has_operational_status_change = (
        "operational_status" in fields
        and str(old_data.get("operational_status")) != str(getattr(task, "operational_status"))
    )

    for field in ("status", "operational_status", "priority", "assigned_to"):
        if field not in fields:
            continue

        # When both status and operational_status changed, skip canonical status
        # so the user sees only the business (operational) status change.
        if field == "status" and has_operational_status_change:
            continue

        old_value = old_data.get(field)
        new_value = getattr(task, field)
        old_text = str(old_value) if old_value is not None else None
        new_text = str(new_value) if new_value is not None else None

        if old_text != new_text:
            changes.append({
                "field": field,
                "old": old_text,
                "new": new_text,
            })

    return {"changes": changes} if changes else {}


def notify_task_updated(db: Session, task: models.Task, actor_id, metadata: dict | None = None):
    for user_id in get_related_task_notification_user_ids(task):
        if user_id == actor_id:
            continue

        create_notification(
            db,
            user_id=user_id,
            actor_id=actor_id,
            task_id=task.id,
            type="task_updated",
            title="Tapşırıq yeniləndi",
            message=f"{task.title} tapşırığında dəyişiklik edildi",
            metadata=metadata,
        )


def notify_task_completed(db: Session, task: models.Task, actor_id, metadata: dict | None = None):
    for user_id in get_related_task_notification_user_ids(task):
        if user_id == actor_id:
            continue

        create_notification(
            db,
            user_id=user_id,
            actor_id=actor_id,
            task_id=task.id,
            type="task_completed",
            title="Tapşırıq tamamlandı",
            message=f"{task.title} tamamlandı",
            metadata=metadata,
        )


router = APIRouter(
    prefix="/tasks",
    tags=["Tasks"]
)

OPERATIONAL_STATUS_TO_CANONICAL = {
    "new": "todo",
    "waiting": "todo",
    "within_deadline": "todo",
    "active": "in_progress",
    "overdue": "in_progress",
    "completed": "done",
}

CANONICAL_STATUS_TO_DEFAULT_OPERATIONAL = {
    "todo": "waiting",
    "in_progress": "active",
    "done": "completed",
}


@router.post("/", response_model=schemas.TaskResponse)
def create_task(
    task: schemas.TaskCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_admin_or_manager)
):
    if task.client_id:
        client = db.query(models.Client).filter(models.Client.id == task.client_id).first()
        if client is None:
            raise HTTPException(status_code=404, detail="Müştəri tapılmadı")

    if task.project_id:
        project = db.query(models.Project).filter(models.Project.id == task.project_id).first()
        if project is None:
            raise HTTPException(status_code=404, detail="Project tapılmadı")

    if task.assigned_to:
        assigned_user = db.query(models.User).filter(models.User.id == task.assigned_to).first()
        if assigned_user is None:
            raise HTTPException(status_code=404, detail="Təyin edilən istifadəçi tapılmadı")

    canonical_status = OPERATIONAL_STATUS_TO_CANONICAL.get(
        task.operational_status,
        task.status
    )

    new_task = models.Task(
        title=task.title,
        description=task.description,
        task_type=task.task_type,
        status=canonical_status,
        operational_status=task.operational_status,
        priority=task.priority,
        kanban_enabled=task.kanban_enabled,
        frequency=task.frequency,
        risk_status=task.risk_status,
        reporting_required=task.reporting_required,
        direction_program=None,  # Phase 8E.1: direction_program is now read-only legacy fallback.
        category=task.category or task.direction_program,
        external_id=task.external_id,
        start_date=task.start_date,
        deadline=task.deadline,
        client_id=task.client_id,
        project_id=task.project_id,
        created_by=current_user.id,
        assigned_to=task.assigned_to,
        custom_fields=task.custom_fields if task.custom_fields else {}
    )

    db.add(new_task)
    db.flush()
    notify_task_assigned(db, new_task, current_user.id)

    create_activity_log(
        db=db,
        user_id=current_user.id,
        action="TASK_CREATED",
        entity_type="task",
        entity_id=new_task.id,
        old_data=None,
        new_data={
            "title": new_task.title,
            "description": new_task.description,
            "task_type": new_task.task_type,
            "status": new_task.status,
            "operational_status": new_task.operational_status,
            "priority": new_task.priority,
            "kanban_enabled": new_task.kanban_enabled,
            "frequency": new_task.frequency,
            "risk_status": new_task.risk_status,
            "reporting_required": new_task.reporting_required,
            "direction_program": new_task.direction_program,
            "category": new_task.category,
            "external_id": new_task.external_id,
            "start_date": str(new_task.start_date) if new_task.start_date else None,
            "deadline": str(new_task.deadline) if new_task.deadline else None,
            "client_id": str(new_task.client_id) if new_task.client_id else None,
            "project_id": str(new_task.project_id) if new_task.project_id else None,
            "assigned_to": str(new_task.assigned_to) if new_task.assigned_to else None,
            "created_by": str(new_task.created_by) if new_task.created_by else None,
            "custom_fields": new_task.custom_fields,
        }
    )

    db.commit()
    db.refresh(new_task)

    return new_task


@router.get("/", response_model=list[schemas.TaskResponse])
def get_tasks(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    if is_admin_or_manager(current_user):
        tasks = db.query(models.Task).all()
    else:
        tasks = db.query(models.Task).filter(
            models.Task.assigned_to == current_user.id
        ).all()

    return tasks

@router.get("/my", response_model=list[schemas.TaskResponse])
def get_my_tasks(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    tasks = db.query(models.Task).filter(
        models.Task.assigned_to == current_user.id
    ).all()

    return tasks

@router.get("/{task_id}", response_model=schemas.TaskResponse)
def get_task(
    task_id: UUID,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    task = db.query(models.Task).filter(models.Task.id == task_id).first()

    if task is None:
        raise HTTPException(status_code=404, detail="Task tapılmadı")

    if not is_admin_or_manager(current_user) and task.assigned_to != current_user.id:
        raise HTTPException(
            status_code=403,
            detail="Yalnız sizə təyin edilən taskları görə bilərsiniz"
        )

    return task

@router.put("/{task_id}", response_model=schemas.TaskResponse)
def update_task(
    task_id: UUID,
    updated_task: schemas.TaskUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    task = db.query(models.Task).filter(models.Task.id == task_id).first()

    if task is None:
        raise HTTPException(status_code=404, detail="Task tapılmadı")

    update_data = updated_task.model_dump(exclude_unset=True)
    requested_fields = set(update_data.keys())

    if update_data.get("operational_status"):
        update_data["status"] = OPERATIONAL_STATUS_TO_CANONICAL[
            update_data["operational_status"]
        ]
    elif update_data.get("status"):
        update_data["operational_status"] = CANONICAL_STATUS_TO_DEFAULT_OPERATIONAL[
            update_data["status"]
        ]

    # Phase 8E.1: Map legacy direction_program into category; never write direction_program.
    if "direction_program" in update_data and "category" not in update_data:
        update_data["category"] = update_data["direction_program"]
    update_data.pop("direction_program", None)

    log_new_data = jsonable_encoder(update_data)
    old_data = {
        "title": task.title,
        "description": task.description,
        "task_type": task.task_type,
        "status": task.status,
        "operational_status": task.operational_status,
        "priority": task.priority,
        "kanban_enabled": task.kanban_enabled,
        "frequency": task.frequency,
        "risk_status": task.risk_status,
        "reporting_required": task.reporting_required,
        "direction_program": task.direction_program,
        "category": task.category,
        "start_date": str(task.start_date) if task.start_date else None,
        "deadline": str(task.deadline) if task.deadline else None,
        "client_id": str(task.client_id) if task.client_id else None,
        "project_id": str(task.project_id) if task.project_id else None,
        "assigned_to": str(task.assigned_to) if task.assigned_to else None,
        "custom_fields": task.custom_fields or {},
    }
    old_assigned_to = task.assigned_to
    was_completed = is_task_completed(task)

    if is_admin_or_manager(current_user):
        if "client_id" in update_data:
            if update_data["client_id"] is None:
                raise HTTPException(status_code=400, detail="Müştəri tələb olunur")

            client = db.query(models.Client).filter(
                models.Client.id == update_data["client_id"]
            ).first()
            if client is None:
                raise HTTPException(status_code=404, detail="Müştəri tapılmadı")

        if update_data.get("project_id"):
            project = db.query(models.Project).filter(
                models.Project.id == update_data["project_id"]
            ).first()
            if project is None:
                raise HTTPException(status_code=404, detail="Project tapılmadı")

        if update_data.get("assigned_to"):
            assigned_user = db.query(models.User).filter(
                models.User.id == update_data["assigned_to"]
            ).first()
            if assigned_user is None:
                raise HTTPException(status_code=404, detail="Təyin edilən istifadəçi tapılmadı")

        for key, value in update_data.items():
            setattr(task, key, value)

        notification_metadata = build_notification_changes(old_data, task, set(update_data.keys()))
        completion_transition = not was_completed and is_task_completed(task)
        has_notification_changes = bool(notification_metadata.get("changes"))

        if "assigned_to" in update_data and task.assigned_to != old_assigned_to:
            notify_task_assigned(db, task, current_user.id)
        elif has_notification_changes and not completion_transition:
            notify_task_updated(db, task, current_user.id, notification_metadata)

        if completion_transition:
            notify_task_completed(db, task, current_user.id, notification_metadata)

        create_activity_log(
            db=db,
            user_id=current_user.id,
            action="TASK_UPDATED",
            entity_type="task",
            entity_id=task.id,
            old_data=old_data,
            new_data=log_new_data
        )

        db.commit()
        db.refresh(task)

        return task

    if task.assigned_to != current_user.id:
        raise HTTPException(
            status_code=403,
            detail="Yalnız sizə təyin edilən taskları yeniləyə bilərsiniz"
        )

    allowed_fields = {"status", "operational_status"}

    if not requested_fields.issubset(allowed_fields):
        raise HTTPException(
            status_code=403,
            detail="Employee yalnız task statusunu yeniləyə bilər"
        )

    for key, value in update_data.items():
        setattr(task, key, value)

    notification_metadata = build_notification_changes(old_data, task, set(update_data.keys()))
    completion_transition = not was_completed and is_task_completed(task)
    has_notification_changes = bool(notification_metadata.get("changes"))

    if completion_transition:
        notify_task_completed(db, task, current_user.id, notification_metadata)
    elif has_notification_changes:
        notify_task_updated(db, task, current_user.id, notification_metadata)

    create_activity_log(
        db=db,
        user_id=current_user.id,
        action="TASK_STATUS_UPDATED",
        entity_type="task",
        entity_id=task.id,
        old_data=old_data,
        new_data=log_new_data
    )

    db.commit()
    db.refresh(task)

    return task

@router.delete("/{task_id}")
def delete_task(
    task_id: UUID,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_admin_or_manager)
):
    task = db.query(models.Task).filter(models.Task.id == task_id).first()

    if task is None:
        raise HTTPException(status_code=404, detail="Task tapılmadı")

    old_data = {
        "title": task.title,
        "description": task.description,
        "status": task.status,
        "operational_status": task.operational_status,
        "priority": task.priority,
        "kanban_enabled": task.kanban_enabled,
        "frequency": task.frequency,
        "risk_status": task.risk_status,
        "reporting_required": task.reporting_required,
        "direction_program": task.direction_program,
        "category": task.category,
        "deadline": str(task.deadline) if task.deadline else None,
        "client_id": str(task.client_id) if task.client_id else None,
        "project_id": str(task.project_id) if task.project_id else None,
        "assigned_to": str(task.assigned_to) if task.assigned_to else None,
        "created_by": str(task.created_by) if task.created_by else None,
        "custom_fields": task.custom_fields or {},
    }

    create_activity_log(
        db=db,
        user_id=current_user.id,
        action="TASK_DELETED",
        entity_type="task",
        entity_id=task.id,
        old_data=old_data,
        new_data=None
    )

    attachments = db.query(models.TaskAttachment).filter(
        models.TaskAttachment.task_id == task.id
    ).all()
    attachment_paths = [resolve_attachment_path(attachment.file_path) for attachment in attachments]

    db.delete(task)
    db.commit()

    for file_path in attachment_paths:
        if file_path.exists():
            file_path.unlink()

    return {
        "message": "Task silindi"
    }
@router.post("/{task_id}/comments", response_model=schemas.TaskCommentResponse)
def create_task_comment(
    task_id: UUID,
    comment_data: schemas.TaskCommentCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    task = db.query(models.Task).filter(models.Task.id == task_id).first()

    if task is None:
        raise HTTPException(status_code=404, detail="Task tapılmadı")

    # Employees can comment only on tasks assigned to them.
    if current_user.role_id == 3 and task.assigned_to != current_user.id:
        raise HTTPException(
            status_code=403,
            detail="Yalnız sizə təyin edilən tasklara comment yaza bilərsiniz"
        )

    new_comment = models.TaskComment(
        task_id=task_id,
        user_id=current_user.id,
        comment=comment_data.comment
    )

    db.add(new_comment)
    if task.assigned_to and task.assigned_to != current_user.id:
        create_notification(
            db,
            user_id=task.assigned_to,
            actor_id=current_user.id,
            task_id=task.id,
            type="comment_added",
            title="Yeni şərh",
            message=f"{task.title} tapşırığına yeni şərh əlavə edildi",
        )
    db.commit()
    db.refresh(new_comment)

    return new_comment


@router.get("/{task_id}/comments", response_model=list[schemas.TaskCommentResponse])
def get_task_comments(
    task_id: UUID,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    task = db.query(models.Task).filter(models.Task.id == task_id).first()

    if task is None:
        raise HTTPException(status_code=404, detail="Task tapılmadı")

    # Employees can view comments only on tasks assigned to them.
    if current_user.role_id == 3 and task.assigned_to != current_user.id:
        raise HTTPException(
            status_code=403,
            detail="Yalnız sizə təyin edilən taskların commentlərini görə bilərsiniz"
        )

    comments = db.query(models.TaskComment).filter(
        models.TaskComment.task_id == task_id
    ).all()

    return comments

@router.put("/comments/{comment_id}", response_model=schemas.TaskCommentResponse)
def update_task_comment(
    comment_id: UUID,
    comment_data: schemas.TaskCommentUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    comment = db.query(models.TaskComment).filter(
        models.TaskComment.id == comment_id
    ).first()

    if comment is None:
        raise HTTPException(status_code=404, detail="Comment tapılmadı")

    task = db.query(models.Task).filter(
        models.Task.id == comment.task_id
    ).first()

    if task is None:
        raise HTTPException(status_code=404, detail="Task tapılmadı")

    # Employees can update only their own comments on assigned tasks.
    if current_user.role_id == 3:
        if comment.user_id != current_user.id:
            raise HTTPException(
                status_code=403,
                detail="Yalnız öz commentlərinizi yeniləyə bilərsiniz"
            )

        if task.assigned_to != current_user.id:
            raise HTTPException(
                status_code=403,
                detail="Yalnız sizə təyin edilən tasklardakı commentləri yeniləyə bilərsiniz"
            )

    comment.comment = comment_data.comment

    db.commit()
    db.refresh(comment)

    return comment

@router.delete("/comments/{comment_id}")
def delete_task_comment(
    comment_id: UUID,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    comment = db.query(models.TaskComment).filter(
        models.TaskComment.id == comment_id
    ).first()

    if comment is None:
        raise HTTPException(status_code=404, detail="Comment tapılmadı")

    task = db.query(models.Task).filter(
        models.Task.id == comment.task_id
    ).first()

    if task is None:
        raise HTTPException(status_code=404, detail="Task tapılmadı")

    # Employees can delete only their own comments on assigned tasks.
    if current_user.role_id == 3:
        if comment.user_id != current_user.id:
            raise HTTPException(
                status_code=403,
                detail="Yalnız öz commentlərinizi silə bilərsiniz"
            )

        if task.assigned_to != current_user.id:
            raise HTTPException(
                status_code=403,
                detail="Yalnız sizə təyin edilən tasklardakı commentləri silə bilərsiniz"
            )

    db.delete(comment)
    db.commit()

    return {
        "message": "Comment silindi"
    }


@router.post("/{task_id}/attachments", response_model=schemas.TaskAttachmentResponse)
def upload_task_attachment(
    task_id: UUID,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    task = db.query(models.Task).filter(models.Task.id == task_id).first()

    if task is None:
        raise HTTPException(status_code=404, detail="Task tapılmadı")

    # Employees can upload files only to tasks assigned to them.
    if current_user.role_id == 3 and task.assigned_to != current_user.id:
        raise HTTPException(
            status_code=403,
            detail="Yalnız sizə təyin edilən tasklara fayl yükləyə bilərsiniz"
        )
    safe_file_name = validate_upload_file(file)

    task_upload_dir = build_task_upload_dir(task_id)
    unique_file_name, file_location = build_unique_upload_path(task_upload_dir, safe_file_name)
    relative_file_path = UPLOAD_DB_PREFIX / "tasks" / str(task_id) / unique_file_name

    with open(file_location, "xb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    new_attachment = models.TaskAttachment(
        task_id=task_id,
        uploaded_by=current_user.id,
        file_name=safe_file_name,
        file_path=relative_file_path.as_posix(),
        file_type=file.content_type
    )

    db.add(new_attachment)
    db.flush()

    create_activity_log(
        db=db,
        user_id=current_user.id,
        action="FILE_UPLOADED",
        entity_type="task_attachment",
        entity_id=new_attachment.id,
        old_data=None,
        new_data={
            "task_id": str(task_id),
            "file_name": new_attachment.file_name,
            "file_path": new_attachment.file_path,
            "file_type": new_attachment.file_type
        }
    )

    db.commit()
    db.refresh(new_attachment)

    return new_attachment


@router.get("/{task_id}/attachments", response_model=list[schemas.TaskAttachmentResponse])
def get_task_attachments(
    task_id: UUID,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    task = db.query(models.Task).filter(models.Task.id == task_id).first()

    if task is None:
        raise HTTPException(status_code=404, detail="Task tapılmadı")

    # Employees can view files only for tasks assigned to them.
    if current_user.role_id == 3 and task.assigned_to != current_user.id:
        raise HTTPException(
            status_code=403,
            detail="Yalnız sizə təyin edilən taskların fayllarını görə bilərsiniz"
        )

    attachments = db.query(models.TaskAttachment).filter(
        models.TaskAttachment.task_id == task_id
    ).all()

    return attachments
