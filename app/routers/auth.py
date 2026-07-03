from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app import models, schemas
from app.auth import verify_password, hash_password, create_access_token, get_current_user
from fastapi.security import OAuth2PasswordRequestForm


router = APIRouter(
    prefix="/auth",
    tags=["Auth"]
)


@router.post("/login", response_model=schemas.TokenResponse)
def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db)
):
    user = db.query(models.User).filter(
        models.User.email == form_data.username
    ).first()

    if user is None:
        raise HTTPException(
            status_code=401,
            detail="Email və ya password yanlışdır"
        )

    if not verify_password(form_data.password, user.password_hash):
        raise HTTPException(
            status_code=401,
            detail="Email və ya password yanlışdır"
        )

    if not user.is_active:
        raise HTTPException(
            status_code=403,
            detail="İstifadəçi deaktivdir"
        )

    token = create_access_token(
        data={
            "sub": str(user.id),
            "email": user.email,
            "role_id": user.role_id
        }
    )

    return {
        "access_token": token,
        "token_type": "bearer",
        "user": user,
    }

@router.post("/change-password")
def change_password(
    payload: schemas.ChangePasswordRequest,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    if not verify_password(payload.current_password, current_user.password_hash):
        raise HTTPException(status_code=400, detail="Cari şifrə yanlışdır")

    if verify_password(payload.new_password, current_user.password_hash):
        raise HTTPException(status_code=400, detail="Yeni şifrə cari şifrədən fərqli olmalıdır")

    current_user.password_hash = hash_password(payload.new_password)
    current_user.must_change_password = False
    db.commit()
    db.refresh(current_user)
    return {"message": "Şifrə uğurla yeniləndi"}


@router.get("/me", response_model=schemas.UserResponse)
def get_me(
    current_user: models.User = Depends(get_current_user)
):
    return current_user
