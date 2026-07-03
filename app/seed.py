import os

from sqlalchemy.orm import Session

from app import models
from app.auth import hash_password


DEFAULT_ROLES = {
    1: "admin",
    2: "manager",
    3: "employee",
}


def seed_roles(db: Session):
    for role_id, role_name in DEFAULT_ROLES.items():
        role = db.query(models.Role).filter(models.Role.id == role_id).first()
        if role is None:
            db.add(models.Role(id=role_id, name=role_name))
        elif role.name != role_name:
            role.name = role_name


def seed_initial_admin(db: Session):
    email = os.getenv("INITIAL_ADMIN_EMAIL")
    password = os.getenv("INITIAL_ADMIN_PASSWORD")
    full_name = os.getenv("INITIAL_ADMIN_FULL_NAME", "Local Admin")

    if not email or not password:
        return

    existing_admin = db.query(models.User).filter(models.User.email == email).first()
    if existing_admin is not None:
        return

    db.add(
        models.User(
            full_name=full_name,
            email=email,
            password_hash=hash_password(password),
            role_id=1,
            is_active=True
        )
    )


def seed_local_data(db: Session):
    seed_roles(db)
    seed_initial_admin(db)
    db.commit()
