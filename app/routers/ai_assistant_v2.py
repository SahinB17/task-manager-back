from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app import models
from app.ai.agent import confirm_agent_action, run_readonly_agent
from app.auth import get_current_user
from app.database import get_db


router = APIRouter(
    prefix="/ai/assistant-v2",
    tags=["AI Assistant V2"]
)

conversations_router = APIRouter(
    prefix="/ai/conversations",
    tags=["AI Conversations"],
)


class AssistantV2MessageRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    conversation_id: UUID | None = None


class AssistantV2ConfirmRequest(BaseModel):
    confirmation_token: str
    conversation_id: UUID | None = None


class CreateConversationRequest(BaseModel):
    title: str | None = Field(default=None, max_length=255)


def extract_action_type(response: dict[str, Any]):
    pending_tool = response.get("pending_tool")
    if isinstance(pending_tool, dict):
        tool_name = pending_tool.get("tool_name")
        if tool_name:
            return tool_name

    tool_results = response.get("tool_results") or []
    if tool_results and isinstance(tool_results[0], dict):
        return tool_results[0].get("tool")

    return None


def make_log_response(response: dict[str, Any]):
    return {
        "message": response.get("message"),
        "status": response.get("status"),
        "requires_confirmation": response.get("requires_confirmation") or False,
        "pending_tool": response.get("pending_tool"),
        "tool_results": response.get("tool_results") or [],
    }


def make_conversation_title(message: str):
    title = " ".join((message or "").split())
    if not title:
        return "Yeni söhbət"
    return title[:40]


def serialize_message(message: models.AIMessage):
    pending_tool = message.pending_tool or None

    return {
        "id": str(message.id),
        "conversation_id": str(message.conversation_id),
        "role": message.role,
        "content": message.content,
        "tool_results": message.tool_results or [],
        "pending_tool": pending_tool,
        "requires_confirmation": bool(pending_tool and pending_tool.get("confirmation_token")),
        "confirmation_token": pending_tool.get("confirmation_token") if pending_tool else None,
        "created_at": message.created_at,
    }


def conversation_has_pending(conversation: models.AIConversation):
    return any(
        bool(message.pending_tool and message.pending_tool.get("confirmation_token"))
        for message in conversation.messages
        if message.role == "assistant"
    )


def serialize_conversation(conversation: models.AIConversation):
    last_message = conversation.messages[-1] if conversation.messages else None

    return {
        "id": str(conversation.id),
        "title": conversation.title,
        "created_at": conversation.created_at,
        "updated_at": conversation.updated_at,
        "has_pending": conversation_has_pending(conversation),
        "last_message": last_message.content if last_message else None,
    }


def get_user_conversation(
    db: Session,
    current_user: models.User,
    conversation_id: UUID,
):
    conversation = db.query(models.AIConversation).filter(
        models.AIConversation.id == conversation_id,
        models.AIConversation.user_id == current_user.id,
    ).first()

    if not conversation:
        raise HTTPException(status_code=404, detail="AI söhbəti tapılmadı")

    return conversation


def get_or_create_conversation(
    db: Session,
    current_user: models.User,
    conversation_id: UUID | None,
    first_message: str,
):
    if conversation_id:
        return get_user_conversation(db, current_user, conversation_id)

    conversation = models.AIConversation(
        user_id=current_user.id,
        title=make_conversation_title(first_message),
    )
    db.add(conversation)
    db.flush()
    return conversation


def touch_conversation(conversation: models.AIConversation):
    conversation.updated_at = datetime.now()


def make_message_pending_tool(response: dict[str, Any]):
    pending_tool = response.get("pending_tool")
    if not isinstance(pending_tool, dict):
        return None

    stored_pending_tool = dict(pending_tool)
    confirmation_token = response.get("confirmation_token")
    if confirmation_token:
        stored_pending_tool["confirmation_token"] = confirmation_token

    return stored_pending_tool


def save_conversation_turn(
    db: Session,
    conversation: models.AIConversation,
    user_message: str | None,
    response: dict[str, Any],
):
    if user_message and user_message != "CONFIRM":
        db.add(
            models.AIMessage(
                conversation_id=conversation.id,
                role="user",
                content=user_message,
            )
        )

    db.add(
        models.AIMessage(
            conversation_id=conversation.id,
            role="assistant",
            content=response.get("message") or "Cavab alındı.",
            tool_results=response.get("tool_results") or [],
            pending_tool=make_message_pending_tool(response),
        )
    )
    touch_conversation(conversation)
    db.commit()
    db.refresh(conversation)


def clear_conversation_pending_token(
    conversation: models.AIConversation,
    confirmation_token: str,
):
    for message in conversation.messages:
        pending_tool = message.pending_tool
        if not isinstance(pending_tool, dict):
            continue
        if pending_tool.get("confirmation_token") != confirmation_token:
            continue

        updated_pending_tool = dict(pending_tool)
        updated_pending_tool.pop("confirmation_token", None)
        message.pending_tool = updated_pending_tool


def build_recent_context(
    db: Session,
    current_user: models.User,
    limit: int = 3,
    conversation_id: UUID | None = None,
):
    if conversation_id:
        conversation = get_user_conversation(db, current_user, conversation_id)
        recent_messages = conversation.messages[-limit * 2 :]
        if recent_messages:
            lines = [
                f"{'User' if message.role == 'user' else 'Assistant'}: {message.content}"
                for message in recent_messages
            ]
            return "\n".join(lines)

    logs = db.query(models.AIAssistantLog).filter(
        models.AIAssistantLog.user_id == current_user.id,
    ).order_by(models.AIAssistantLog.created_at.desc()).limit(limit).all()

    if not logs:
        return None

    lines = []

    for log in reversed(logs):
        response = log.response or {}
        assistant_message = response.get("message")

        if log.message and log.message != "CONFIRM":
            lines.append(f"User: {log.message}")

        if assistant_message:
            lines.append(f"Assistant: {assistant_message}")

        pending_tool = response.get("pending_tool")
        if pending_tool:
            lines.append(f"Pending tool: {pending_tool}")

        tool_results = response.get("tool_results") or []
        if tool_results:
            first_tool = tool_results[0] if isinstance(tool_results[0], dict) else {}
            tool_name = first_tool.get("tool")
            total_count = first_tool.get("total_count")
            if tool_name:
                lines.append(f"Tool used: {tool_name}, total_count={total_count}")

    return "\n".join(lines)

def log_ai_message(
    db: Session,
    current_user: models.User,
    user_message: str,
    response: dict[str, Any],
):
    action_type = extract_action_type(response)

    ai_log = models.AIAssistantLog(
        user_id=current_user.id,
        message=user_message,
        intent=action_type,
        action_type=action_type,
        status=response.get("status") or "unknown",
        response=make_log_response(response),
    )

    db.add(ai_log)
    db.commit()


@router.post("/message")
def assistant_v2_message(
    request: AssistantV2MessageRequest,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    conversation = get_or_create_conversation(
        db=db,
        current_user=current_user,
        conversation_id=request.conversation_id,
        first_message=request.message,
    )
    conversation_context = (
        build_recent_context(
            db=db,
            current_user=current_user,
            conversation_id=conversation.id,
        )
        if request.conversation_id
        else None
    )

    result = run_readonly_agent(
        message=request.message,
        db=db,
        current_user=current_user,
        conversation_context=conversation_context,
    )

    log_ai_message(
        db=db,
        current_user=current_user,
        user_message=request.message,
        response=result,
    )

    save_conversation_turn(
        db=db,
        conversation=conversation,
        user_message=request.message,
        response=result,
    )

    result["conversation_id"] = str(conversation.id)
    return result


@router.post("/confirm")
def assistant_v2_confirm(
    request: AssistantV2ConfirmRequest,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    result = confirm_agent_action(
        confirmation_token=request.confirmation_token,
        db=db,
        current_user=current_user,
    )

    conversation = None
    if request.conversation_id:
        conversation = get_user_conversation(db, current_user, request.conversation_id)

    log_ai_message(
        db=db,
        current_user=current_user,
        user_message="CONFIRM",
        response=result,
    )

    if conversation:
        clear_conversation_pending_token(conversation, request.confirmation_token)
        save_conversation_turn(
            db=db,
            conversation=conversation,
            user_message="CONFIRM",
            response=result,
        )
        result["conversation_id"] = str(conversation.id)

    return result


@router.get("/history")
def get_assistant_v2_history(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    logs = db.query(models.AIAssistantLog).filter(
        models.AIAssistantLog.user_id == current_user.id,
    ).order_by(models.AIAssistantLog.created_at.desc()).limit(30).all()

    return [
        {
            "id": str(log.id),
            "created_at": log.created_at,
            "message": log.message,
            "intent": log.intent,
            "action_type": log.action_type,
            "status": log.status,
            "assistant_message": (log.response or {}).get("message"),
            "requires_confirmation": (log.response or {}).get("requires_confirmation") or False,
            "pending_tool": (log.response or {}).get("pending_tool"),
            "tool_results": (log.response or {}).get("tool_results") or [],
        }
        for log in logs
    ]


@conversations_router.get("")
def get_ai_conversations(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    conversations = db.query(models.AIConversation).filter(
        models.AIConversation.user_id == current_user.id,
    ).order_by(models.AIConversation.updated_at.desc()).limit(50).all()

    return [serialize_conversation(conversation) for conversation in conversations]


@conversations_router.post("")
def create_ai_conversation(
    request: CreateConversationRequest,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    conversation = models.AIConversation(
        user_id=current_user.id,
        title=request.title or "Yeni söhbət",
    )
    db.add(conversation)
    db.commit()
    db.refresh(conversation)

    return serialize_conversation(conversation)


@conversations_router.get("/{conversation_id}")
def get_ai_conversation(
    conversation_id: UUID,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    conversation = get_user_conversation(db, current_user, conversation_id)

    return {
        "conversation": serialize_conversation(conversation),
        "messages": [serialize_message(message) for message in conversation.messages],
    }


@conversations_router.delete("/{conversation_id}")
def delete_ai_conversation(
    conversation_id: UUID,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    conversation = get_user_conversation(db, current_user, conversation_id)
    db.delete(conversation)
    db.commit()

    return {"status": "deleted"}
