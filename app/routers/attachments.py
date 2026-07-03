from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from uuid import UUID

from app.database import get_db
from app import models
from app.auth import get_current_user
from app.utils import create_activity_log
from app.upload_security import resolve_attachment_path
from fastapi.responses import FileResponse

router = APIRouter(
    prefix="/attachments",
    tags=["Attachments"]
)

@router.delete("/{attachment_id}")
def delete_attachment(
    attachment_id: UUID,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    attachment = db.query(models.TaskAttachment).filter(
        models.TaskAttachment.id == attachment_id
    ).first()

    if attachment is None:
        raise HTTPException(status_code=404, detail="Fayl tapılmadı")

    task = db.query(models.Task).filter(
        models.Task.id == attachment.task_id
    ).first()

    if task is None:
        raise HTTPException(status_code=404, detail="Task tapılmadı")

    # Employees can delete only their own files on assigned tasks.
    if current_user.role_id == 3:
        if task.assigned_to != current_user.id:
            raise HTTPException(
                status_code=403,
                detail="Yalnız sizə təyin edilən tasklardan fayl silə bilərsiniz"
            )

        if attachment.uploaded_by != current_user.id:
            raise HTTPException(
                status_code=403,
                detail="Yalnız öz yüklədiyiniz faylları silə bilərsiniz"
            )

    old_data = {
        "task_id": str(attachment.task_id),
        "file_name": attachment.file_name,
        "file_path": attachment.file_path,
        "file_type": attachment.file_type
    }

    file_path = resolve_attachment_path(attachment.file_path)

    if file_path.exists():
        file_path.unlink()

    create_activity_log(
        db=db,
        user_id=current_user.id,
        action="FILE_DELETED",
        entity_type="task_attachment",
        entity_id=attachment.id,
        old_data=old_data,
        new_data=None
    )

    db.delete(attachment)
    db.commit()

    return {
        "message": "Fayl silindi"
    }

@router.get("/{attachment_id}/download")
def download_attachment(
    attachment_id: UUID,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    attachment = db.query(models.TaskAttachment).filter(
        models.TaskAttachment.id == attachment_id
    ).first()

    if attachment is None:
        raise HTTPException(status_code=404, detail="Fayl tapılmadı")

    task = db.query(models.Task).filter(
        models.Task.id == attachment.task_id
    ).first()

    if task is None:
        raise HTTPException(status_code=404, detail="Task tapılmadı")

    # Employees can download files only from assigned tasks.
    if current_user.role_id == 3 and task.assigned_to != current_user.id:
        raise HTTPException(
            status_code=403,
            detail="Yalnız sizə təyin edilən tasklardan fayl yükləyə bilərsiniz"
        )

    file_path = resolve_attachment_path(attachment.file_path)

    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Fayl serverdə tapılmadı")

    return FileResponse(
        path=file_path,
        filename=attachment.file_name,
        media_type=attachment.file_type
    )
