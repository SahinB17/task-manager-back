from datetime import datetime, timedelta, timezone
import os

from dotenv import load_dotenv
from fastapi import Depends, HTTPException
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from app.database import get_db
from app import models


load_dotenv()

 
SECRET_KEY = os.getenv("SECRET_KEY")
ALGORITHM = os.getenv("ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "60"))
ADMIN_MANAGER_ROLE_IDS = {1, 2}
ADMIN_MANAGER_ROLE_NAMES = {"admin", "manager"}
ADMIN_ROLE_IDS = {1}
ADMIN_ROLE_NAMES = {"admin"}

if not SECRET_KEY:
    raise RuntimeError("SECRET_KEY environment variable is required.")

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")



def hash_password(password: str):
    return pwd_context.hash(password)

def verify_password(plain_password: str, hashed_password: str):
    return pwd_context.verify(plain_password, hashed_password)


def create_access_token(data: dict):
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})

    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id: str = payload.get("sub")

        if user_id is None:
            raise HTTPException(status_code=401, detail="Autentifikasiya tokeni yanlışdır")
    except JWTError:
        raise HTTPException(status_code=401, detail="Autentifikasiya tokeni yanlışdır")

    user = db.query(models.User).filter(models.User.id == user_id).first()

    if user is None:
        raise HTTPException(status_code=401, detail="İstifadəçi tapılmadı")

    if not user.is_active:
        raise HTTPException(status_code=403, detail="İstifadəçi deaktivdir")

    return user


def is_admin_or_manager(user: models.User):
    role_name = user.role.name.lower() if user.role and user.role.name else None
    return user.role_id in ADMIN_MANAGER_ROLE_IDS or role_name in ADMIN_MANAGER_ROLE_NAMES


def is_admin(user: models.User):
    role_name = user.role.name.lower() if user.role and user.role.name else None
    return user.role_id in ADMIN_ROLE_IDS or role_name in ADMIN_ROLE_NAMES


def require_admin(current_user: models.User = Depends(get_current_user)):
    if not is_admin(current_user):
        raise HTTPException(
            status_code=403,
            detail="Bu əməliyyatı icra etmək üçün admin icazəsi lazımdır"
        )

    return current_user


def require_admin_or_manager(current_user: models.User = Depends(get_current_user)):
    if not is_admin_or_manager(current_user):
        raise HTTPException(
            status_code=403,
            detail="Bu əməliyyatı icra etmək üçün icazəniz yoxdur"
        )

    return current_user
