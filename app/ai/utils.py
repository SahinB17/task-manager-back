from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import HTTPException
from jose import JWTError, jwt

from app import models
from app.auth import ALGORITHM, SECRET_KEY

CONFIRMATION_EXPIRE_MINUTES = 10

WRITE_TOOLS = {
    "create_task",
    "change_task_status",
    "add_task_comment",
    "create_customer",
    "update_customer",
    "create_user",
    "update_user",
}

TOOL_ARGUMENT_KEYS = {
    "create_task": {
        "title",
        "description",
        "task_type",
        "status",
        "priority",
        "deadline",
        "client_id",
        "client_query",
        "assigned_to",
        "assignee_query",
    },
    "change_task_status": {
        "task_id",
        "task_query",
        "status",
    },
    "add_task_comment": {
        "task_id",
        "task_query",
        "comment",
    },
    "create_customer": {
        "company_name",
        "contact_person",
        "phone",
        "email",
        "address",
        "notes",
    },
    "update_customer": {
        "client_id",
        "client_query",
        "company_name",
        "contact_person",
        "phone",
        "email",
        "address",
        "notes",
    },
    "create_user": {
        "full_name",
        "email",
        "password",
        "role_id",
        "is_active",
    },
    "update_user": {
        "user_id",
        "user_query",
        "full_name",
        "email",
        "password",
        "role_id",
        "is_active",
    },
}


def sanitize_tool_arguments(tool_name: str, arguments: dict[str, Any]):
    allowed_keys = TOOL_ARGUMENT_KEYS.get(tool_name)

    if not allowed_keys:
        return arguments.copy()

    return {
        key: value
        for key, value in arguments.items()
        if key in allowed_keys
    }


def mask_sensitive_arguments(arguments: dict[str, Any]):
    masked = arguments.copy()

    if masked.get("password"):
        masked["password"] = "********"

    return masked


def make_confirmation_token(
    *,
    tool_name: str,
    arguments: dict[str, Any],
    current_user: models.User,
):
    expires_at = datetime.now(timezone.utc) + timedelta(
        minutes=CONFIRMATION_EXPIRE_MINUTES
    )

    payload = {
        "sub": str(current_user.id),
        "tool_name": tool_name,
        "arguments": sanitize_tool_arguments(tool_name, arguments),
        "exp": expires_at,
    }

    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_confirmation_token(
    token: str,
    current_user: models.User,
):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        raise HTTPException(status_code=400, detail="Təsdiq tokeni yanlışdır")

    if payload.get("sub") != str(current_user.id):
        raise HTTPException(status_code=403, detail="Təsdiq tokeni bu istifadəçiyə aid deyil")

    tool_name = payload.get("tool_name")
    arguments = payload.get("arguments")

    if tool_name not in WRITE_TOOLS:
        raise HTTPException(status_code=400, detail="Təsdiq tool-u yanlışdır")

    if not isinstance(arguments, dict):
        raise HTTPException(status_code=400, detail="Təsdiq arqumentləri yanlışdır")

    return tool_name, sanitize_tool_arguments(tool_name, arguments)
