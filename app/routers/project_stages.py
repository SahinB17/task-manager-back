from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.encoders import jsonable_encoder
from sqlalchemy.orm import Session

from app import models, schemas
from app.auth import require_admin_or_manager
from app.database import get_db
from app.utils import create_activity_log


router = APIRouter(prefix="/project-stages", tags=["Project Stages"])


def get_stage_or_404(stage_id: UUID, db: Session):
    stage = db.query(models.ProjectStage).filter(models.ProjectStage.id == stage_id).first()
    if stage is None:
        raise HTTPException(status_code=404, detail="Layihə mərhələsi tapılmadı")
    return stage


def validate_project(project_id: UUID, db: Session):
    project = db.query(models.Project).filter(models.Project.id == project_id).first()
    if project is None:
        raise HTTPException(status_code=404, detail="Project tapılmadı")


@router.post("/", response_model=schemas.ProjectStageResponse)
def create_project_stage(
    stage: schemas.ProjectStageCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_admin_or_manager)
):
    validate_project(stage.project_id, db)
    new_stage = models.ProjectStage(**stage.model_dump())
    db.add(new_stage)
    db.flush()

    create_activity_log(
        db=db,
        user_id=current_user.id,
        action="PROJECT_STAGE_CREATED",
        entity_type="project_stage",
        entity_id=new_stage.id,
        old_data=None,
        new_data=jsonable_encoder(stage.model_dump())
    )
    db.commit()
    db.refresh(new_stage)
    return new_stage


@router.get("/", response_model=list[schemas.ProjectStageResponse])
def get_project_stages(
    project_id: Optional[UUID] = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_admin_or_manager)
):
    query = db.query(models.ProjectStage)
    if project_id is not None:
        query = query.filter(models.ProjectStage.project_id == project_id)
    return query.order_by(models.ProjectStage.sort_order, models.ProjectStage.created_at).all()


@router.get("/{stage_id}", response_model=schemas.ProjectStageResponse)
def get_project_stage(
    stage_id: UUID,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_admin_or_manager)
):
    return get_stage_or_404(stage_id, db)


@router.put("/{stage_id}", response_model=schemas.ProjectStageResponse)
def update_project_stage(
    stage_id: UUID,
    updated_stage: schemas.ProjectStageUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_admin_or_manager)
):
    stage = get_stage_or_404(stage_id, db)
    update_data = updated_stage.model_dump(exclude_unset=True)

    if "project_id" in update_data:
        if update_data["project_id"] is None:
            raise HTTPException(status_code=400, detail="Project tələb olunur")
        validate_project(update_data["project_id"], db)
    if "name" in update_data and update_data["name"] is None:
        raise HTTPException(status_code=400, detail="Mərhələ adı tələb olunur")

    old_data = jsonable_encoder({
        "project_id": stage.project_id,
        "name": stage.name,
        "weight_percent": stage.weight_percent,
        "planned_start": stage.planned_start,
        "planned_end": stage.planned_end,
        "actual_end": stage.actual_end,
        "status": stage.status,
        "delay_reason": stage.delay_reason,
        "completion_percent": stage.completion_percent,
        "sort_order": stage.sort_order,
    })
    for key, value in update_data.items():
        setattr(stage, key, value)

    create_activity_log(
        db=db,
        user_id=current_user.id,
        action="PROJECT_STAGE_UPDATED",
        entity_type="project_stage",
        entity_id=stage.id,
        old_data=old_data,
        new_data=jsonable_encoder(update_data)
    )
    db.commit()
    db.refresh(stage)
    return stage


@router.delete("/{stage_id}")
def delete_project_stage(
    stage_id: UUID,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_admin_or_manager)
):
    stage = get_stage_or_404(stage_id, db)
    old_data = jsonable_encoder({
        "project_id": stage.project_id,
        "name": stage.name,
        "weight_percent": stage.weight_percent,
        "planned_start": stage.planned_start,
        "planned_end": stage.planned_end,
        "actual_end": stage.actual_end,
        "status": stage.status,
        "delay_reason": stage.delay_reason,
        "completion_percent": stage.completion_percent,
        "sort_order": stage.sort_order,
    })
    create_activity_log(
        db=db,
        user_id=current_user.id,
        action="PROJECT_STAGE_DELETED",
        entity_type="project_stage",
        entity_id=stage.id,
        old_data=old_data,
        new_data=None
    )
    db.delete(stage)
    db.commit()
    return {"message": "Layihə mərhələsi silindi"}
