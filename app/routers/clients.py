from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from uuid import UUID

from app.database import get_db
from app import models, schemas
from app.auth import require_admin_or_manager
from app.utils import create_activity_log


router = APIRouter(
    prefix="/clients",
    tags=["Clients"]
)


@router.post("/", response_model=schemas.ClientResponse)
def create_client(
    client: schemas.ClientCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_admin_or_manager)
):
    new_client = models.Client(
        company_name=client.company_name,
        contact_person=client.contact_person,
        phone=client.phone,
        email=client.email,
        address=client.address,
        notes=client.notes
    )

    db.add(new_client)
    db.flush()

    create_activity_log(
        db=db,
        user_id=current_user.id,
        action="CLIENT_CREATED",
        entity_type="client",
        entity_id=new_client.id,
        old_data=None,
        new_data={
            "company_name": new_client.company_name,
            "contact_person": new_client.contact_person,
            "phone": new_client.phone,
            "email": new_client.email,
            "address": new_client.address,
            "notes": new_client.notes
        }
    )

    db.commit()
    db.refresh(new_client)
    return new_client


@router.get("/", response_model=list[schemas.ClientResponse])
def get_clients(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_admin_or_manager)
):
    return db.query(models.Client).all()


@router.get("/{client_id}", response_model=schemas.ClientResponse)
def get_client(
    client_id: UUID,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_admin_or_manager)
):
    client = db.query(models.Client).filter(models.Client.id == client_id).first()
    if client is None:
        raise HTTPException(status_code=404, detail="Müştəri tapılmadı")
    return client


@router.get("/{client_id}/tasks", response_model=list[schemas.TaskResponse])
def get_client_tasks(
    client_id: UUID,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_admin_or_manager)
):
    client = db.query(models.Client).filter(models.Client.id == client_id).first()
    if client is None:
        raise HTTPException(status_code=404, detail="Müştəri tapılmadı")

    return db.query(models.Task).filter(models.Task.client_id == client_id).all()


@router.put("/{client_id}", response_model=schemas.ClientResponse)
def update_client(
    client_id: UUID,
    updated_client: schemas.ClientUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_admin_or_manager)
):
    client = db.query(models.Client).filter(models.Client.id == client_id).first()
    if client is None:
        raise HTTPException(status_code=404, detail="Müştəri tapılmadı")

    old_data = {
        "company_name": client.company_name,
        "contact_person": client.contact_person,
        "phone": client.phone,
        "email": client.email,
        "address": client.address,
        "notes": client.notes
    }

    update_data = updated_client.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(client, key, value)

    create_activity_log(
        db=db,
        user_id=current_user.id,
        action="CLIENT_UPDATED",
        entity_type="client",
        entity_id=client.id,
        old_data=old_data,
        new_data=update_data
    )

    db.commit()
    db.refresh(client)
    return client


@router.delete("/{client_id}")
def delete_client(
    client_id: UUID,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_admin_or_manager)
):
    client = db.query(models.Client).filter(models.Client.id == client_id).first()
    if client is None:
        raise HTTPException(status_code=404, detail="Müştəri tapılmadı")

    has_tasks = db.query(models.Task).filter(models.Task.client_id == client_id).first()
    if has_tasks:
        raise HTTPException(
            status_code=400,
            detail="Bu müştəriyə bağlı tasklar var və silinə bilməz"
        )

    old_data = {
        "company_name": client.company_name,
        "contact_person": client.contact_person,
        "phone": client.phone,
        "email": client.email,
        "address": client.address,
        "notes": client.notes
    }

    create_activity_log(
        db=db,
        user_id=current_user.id,
        action="CLIENT_DELETED",
        entity_type="client",
        entity_id=client.id,
        old_data=old_data,
        new_data=None
    )

    db.delete(client)
    db.commit()
    return {"message": "Müştəri silindi"}
