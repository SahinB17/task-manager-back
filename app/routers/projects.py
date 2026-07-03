from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import models, schemas
from app.auth import require_admin_or_manager
from app.database import get_db
from app.utils import create_activity_log


router = APIRouter(prefix="/projects", tags=["Projects"])


def get_project_or_404(project_id: UUID, db: Session):
    project = db.query(models.Project).filter(models.Project.id == project_id).first()
    if project is None:
        raise HTTPException(status_code=404, detail="Project tapılmadı")
    return project


def validate_project_uniqueness(
    db: Session,
    name: Optional[str] = None,
    code: Optional[str] = None,
    exclude_project_id: Optional[UUID] = None
):
    if name is not None:
        query = db.query(models.Project).filter(models.Project.name == name)
        if exclude_project_id is not None:
            query = query.filter(models.Project.id != exclude_project_id)
        if query.first():
            raise HTTPException(status_code=400, detail="Bu adda project artıq mövcuddur")

    if code is not None:
        query = db.query(models.Project).filter(models.Project.code == code)
        if exclude_project_id is not None:
            query = query.filter(models.Project.id != exclude_project_id)
        if query.first():
            raise HTTPException(status_code=400, detail="Bu kodda project artıq mövcuddur")


@router.post("/", response_model=schemas.ProjectResponse)
def create_project(
    project: schemas.ProjectCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_admin_or_manager)
):
    validate_project_uniqueness(db, name=project.name, code=project.code)
    new_project = models.Project(**project.model_dump())
    db.add(new_project)
    db.flush()

    create_activity_log(
        db=db,
        user_id=current_user.id,
        action="PROJECT_CREATED",
        entity_type="project",
        entity_id=new_project.id,
        old_data=None,
        new_data=project.model_dump()
    )
    db.commit()
    db.refresh(new_project)
    return new_project


@router.get("/", response_model=list[schemas.ProjectResponse])
def get_projects(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_admin_or_manager)
):
    return db.query(models.Project).all()


@router.get("/{project_id}", response_model=schemas.ProjectResponse)
def get_project(
    project_id: UUID,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_admin_or_manager)
):
    return get_project_or_404(project_id, db)


@router.put("/{project_id}", response_model=schemas.ProjectResponse)
def update_project(
    project_id: UUID,
    updated_project: schemas.ProjectUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_admin_or_manager)
):
    project = get_project_or_404(project_id, db)
    update_data = updated_project.model_dump(exclude_unset=True)
    if "name" in update_data and update_data["name"] is None:
        raise HTTPException(status_code=400, detail="Project adı tələb olunur")

    validate_project_uniqueness(
        db,
        name=update_data.get("name"),
        code=update_data.get("code"),
        exclude_project_id=project.id
    )
    old_data = {
        "code": project.code,
        "name": project.name,
        "direction_program": project.direction_program,
        "owner": project.owner,
        "approach": project.approach,
        "description": project.description,
        "is_active": project.is_active
    }
    for key, value in update_data.items():
        setattr(project, key, value)

    create_activity_log(
        db=db,
        user_id=current_user.id,
        action="PROJECT_UPDATED",
        entity_type="project",
        entity_id=project.id,
        old_data=old_data,
        new_data=update_data
    )
    db.commit()
    db.refresh(project)
    return project


@router.delete("/{project_id}")
def delete_project(
    project_id: UUID,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_admin_or_manager)
):
    project = get_project_or_404(project_id, db)
    linked_task = db.query(models.Task).filter(models.Task.project_id == project.id).first()
    if linked_task:
        raise HTTPException(
            status_code=400,
            detail="Bu projectə bağlı tasklar var və project silinə bilməz"
        )

    linked_stage = db.query(models.ProjectStage).filter(
        models.ProjectStage.project_id == project.id
    ).first()
    if linked_stage:
        raise HTTPException(
            status_code=400,
            detail="Bu projectə bağlı mərhələlər var və project silinə bilməz"
        )

    old_data = {
        "code": project.code,
        "name": project.name,
        "direction_program": project.direction_program,
        "owner": project.owner,
        "approach": project.approach,
        "description": project.description,
        "is_active": project.is_active
    }
    create_activity_log(
        db=db,
        user_id=current_user.id,
        action="PROJECT_DELETED",
        entity_type="project",
        entity_id=project.id,
        old_data=old_data,
        new_data=None
    )
    db.delete(project)
    db.commit()
    return {"message": "Project silindi"}
