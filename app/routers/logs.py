from fastapi import APIRouter, Depends
from fastapi.encoders import jsonable_encoder
from sqlalchemy.orm import Session

from app.database import get_db
from app import models
from app.auth import require_admin_or_manager


router = APIRouter(
    prefix="/logs",
    tags=["Activity Logs"]
)


@router.get("/")
def get_activity_logs(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_admin_or_manager)
):
    log_rows = db.query(models.ActivityLog, models.User).outerjoin(
        models.User,
        models.ActivityLog.user_id == models.User.id,
    ).order_by(
        models.ActivityLog.created_at.desc()
    ).all()

    logs = []
    for log, actor in log_rows:
        item = jsonable_encoder(log)
        item["actor"] = (
            {
                "id": actor.id,
                "full_name": actor.full_name,
                "email": actor.email,
                "role_id": actor.role_id,
            }
            if actor
            else None
        )
        logs.append(item)

    return logs
