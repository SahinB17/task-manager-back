import secrets

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from uuid import UUID

from app.database import get_db
from app import models, schemas
from app.auth import hash_password, require_admin, require_admin_or_manager
from app.utils import create_activity_log


router = APIRouter(
    prefix="/users",
    tags=["Users"]
)


ADMIN_ROLE_ID = 1
MANAGER_ROLE_ID = 2
EMPLOYEE_ROLE_ID = 3
VALID_ROLE_IDS = {ADMIN_ROLE_ID, MANAGER_ROLE_ID, EMPLOYEE_ROLE_ID}


def validate_role_id(role_id: int):
    if role_id not in VALID_ROLE_IDS:
        raise HTTPException(
            status_code=400,
            detail="role_id yanlışdır. İcazəli dəyərlər: 1=Admin, 2=Manager, 3=Employee"
        )


def ensure_can_assign_role(current_user: models.User, requested_role_id: int):
    validate_role_id(requested_role_id)

    if current_user.role_id == MANAGER_ROLE_ID and requested_role_id != EMPLOYEE_ROLE_ID:
        raise HTTPException(
            status_code=403,
            detail="Manager yalnız employee rolunu təyin edə bilər"
        )

    if current_user.role_id != ADMIN_ROLE_ID and requested_role_id in {ADMIN_ROLE_ID, MANAGER_ROLE_ID}:
        raise HTTPException(
            status_code=403,
            detail="Admin və ya manager rolunu yalnız admin təyin edə bilər"
        )


@router.post("/", response_model=schemas.UserResponse)
def create_user(
    user: schemas.UserCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_admin_or_manager)
):
    existing_user = db.query(models.User).filter(models.User.email == user.email).first()

    if existing_user:
        raise HTTPException(status_code=400, detail="Bu email artıq qeydiyyatdan keçib")

    requested_role_id = user.role_id or EMPLOYEE_ROLE_ID
    ensure_can_assign_role(current_user, requested_role_id)

    new_user = models.User(
        full_name=user.full_name,
        email=user.email,
        password_hash=hash_password(user.password),
        role_id=requested_role_id,
        is_active=user.is_active
    )

    db.add(new_user)
    db.flush()

    create_activity_log(
        db=db,
        user_id=current_user.id,
        action="USER_CREATED",
        entity_type="user",
        entity_id=new_user.id,
        old_data=None,
        new_data={
            "full_name": new_user.full_name,
            "email": new_user.email,
            "role_id": new_user.role_id,
            "is_active": new_user.is_active
        }
    )

    db.commit()
    db.refresh(new_user)

    return new_user


@router.get("/", response_model=list[schemas.UserResponse])
def get_users(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_admin_or_manager)
):
    return db.query(models.User).all()


@router.get("/{user_id}", response_model=schemas.UserResponse)
def get_user(
    user_id: UUID,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_admin_or_manager)
):
    user = db.query(models.User).filter(models.User.id == user_id).first()

    if user is None:
        raise HTTPException(status_code=404, detail="İstifadəçi tapılmadı")

    return user


@router.post("/{user_id}/reset-password", response_model=schemas.ResetPasswordResponse)
def reset_user_password(
    user_id: UUID,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_admin)
):
    user = db.query(models.User).filter(models.User.id == user_id).first()

    if user is None:
        raise HTTPException(status_code=404, detail="İstifadəçi tapılmadı")

    if user.id == current_user.id:
        raise HTTPException(
            status_code=400,
            detail="Öz şifrənizi bu əməliyyatla sıfırlaya bilməzsiniz"
        )

    temporary_password = secrets.token_urlsafe(18)
    user.password_hash = hash_password(temporary_password)
    user.is_active = True
    user.must_change_password = True

    create_activity_log(
        db=db,
        user_id=current_user.id,
        action="USER_PASSWORD_RESET",
        entity_type="user",
        entity_id=user.id,
        old_data=None,
        new_data={
            "is_active": user.is_active,
            "must_change_password": user.must_change_password
        }
    )

    db.commit()
    db.refresh(user)

    return {
        "message": "İstifadəçi aktiv edildi və müvəqqəti şifrə yaradıldı",
        "temporary_password": temporary_password,
        "user": user,
    }


@router.put("/{user_id}", response_model=schemas.UserResponse)
def update_user(
    user_id: UUID,
    updated_user: schemas.UserUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_admin_or_manager)
):
    user = db.query(models.User).filter(models.User.id == user_id).first()

    if user is None:
        raise HTTPException(status_code=404, detail="İstifadəçi tapılmadı")

    old_data = {
        "full_name": user.full_name,
        "email": user.email,
        "role_id": user.role_id,
        "is_active": user.is_active
    }

    update_data = updated_user.model_dump(exclude_unset=True)

    if "password" in update_data:
        user.password_hash = hash_password(update_data["password"])
        update_data.pop("password")

    if "role_id" in update_data:
        ensure_can_assign_role(current_user, update_data["role_id"])

    for key, value in update_data.items():
        setattr(user, key, value)

    create_activity_log(
        db=db,
        user_id=current_user.id,
        action="USER_UPDATED",
        entity_type="user",
        entity_id=user.id,
        old_data=old_data,
        new_data=update_data
    )

    db.commit()
    db.refresh(user)

    return user


@router.delete("/{user_id}")
def delete_user(
    user_id: UUID,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_admin_or_manager)
):
    user = db.query(models.User).filter(models.User.id == user_id).first()

    if user is None:
        raise HTTPException(status_code=404, detail="İstifadəçi tapılmadı")

    if user.id == current_user.id:
        raise HTTPException(
            status_code=400,
            detail="Öz hesabınızı silə bilməzsiniz"
        )

    has_tasks = db.query(models.Task).filter(
        (models.Task.assigned_to == user_id) | (models.Task.created_by == user_id)
    ).first()
    if has_tasks:
        raise HTTPException(
            status_code=400,
            detail="Bu istifadəçiyə bağlı tasklar var və silinə bilməz"
        )

    old_data = {
        "full_name": user.full_name,
        "email": user.email,
        "role_id": user.role_id,
        "is_active": user.is_active
    }

    create_activity_log(
        db=db,
        user_id=current_user.id,
        action="USER_DELETED",
        entity_type="user",
        entity_id=user.id,
        old_data=old_data,
        new_data=None
    )

    db.delete(user)
    db.commit()

    return {
        "message": "İstifadəçi silindi"
    }
