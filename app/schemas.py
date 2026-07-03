from pydantic import BaseModel, EmailStr, ConfigDict, Field, field_validator
from typing import Optional, Literal, Any
from uuid import UUID
from datetime import date, datetime


class ClientBase(BaseModel):
    company_name: str
    contact_person: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    address: Optional[str] = None
    notes: Optional[str] = None


class ClientCreate(ClientBase):
    pass


class ClientResponse(ClientBase):
    id: UUID
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)

class UserBase(BaseModel):
    full_name: str
    email: str
    role_id: Optional[int] = None
    is_active: Optional[bool] = True


class UserCreate(UserBase):
    password: str = Field(min_length=6)


class UserResponse(UserBase):
    id: UUID
    must_change_password: bool = False
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class UserSummary(BaseModel):
    id: UUID
    full_name: str
    email: str
    role_id: Optional[int] = None

    model_config = ConfigDict(from_attributes=True)


class ClientSummary(BaseModel):
    id: UUID
    company_name: str

    model_config = ConfigDict(from_attributes=True)


class ProjectBase(BaseModel):
    code: Optional[str] = None
    name: str
    direction_program: Optional[str] = None
    owner: Optional[str] = None
    approach: Optional[str] = None
    description: Optional[str] = None
    is_active: bool = True


class ProjectCreate(ProjectBase):
    pass


class ProjectUpdate(BaseModel):
    code: Optional[str] = None
    name: Optional[str] = None
    direction_program: Optional[str] = None
    owner: Optional[str] = None
    approach: Optional[str] = None
    description: Optional[str] = None
    is_active: Optional[bool] = None


class ProjectResponse(ProjectBase):
    id: UUID
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ProjectSummary(BaseModel):
    id: UUID
    code: Optional[str] = None
    name: str

    model_config = ConfigDict(from_attributes=True)


class ProjectStageBase(BaseModel):
    project_id: UUID
    name: str
    weight_percent: Optional[float] = Field(default=None, ge=0, le=100)
    planned_start: Optional[date] = None
    planned_end: Optional[date] = None
    actual_end: Optional[date] = None
    status: Optional[str] = None
    delay_reason: Optional[str] = None
    completion_percent: Optional[float] = Field(default=None, ge=0, le=100)
    sort_order: int = Field(default=0, ge=0)


class ProjectStageCreate(ProjectStageBase):
    pass


class ProjectStageUpdate(BaseModel):
    project_id: Optional[UUID] = None
    name: Optional[str] = None
    weight_percent: Optional[float] = Field(default=None, ge=0, le=100)
    planned_start: Optional[date] = None
    planned_end: Optional[date] = None
    actual_end: Optional[date] = None
    status: Optional[str] = None
    delay_reason: Optional[str] = None
    completion_percent: Optional[float] = Field(default=None, ge=0, le=100)
    sort_order: Optional[int] = Field(default=None, ge=0)


class ProjectStageResponse(ProjectStageBase):
    id: UUID
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


TaskTypeValue = Literal[
    "general",
    "one_time",
    "recurring",
    "project",
]
OperationalStatusValue = Literal[
    "new",
    "waiting",
    "within_deadline",
    "active",
    "overdue",
    "completed",
]
FrequencyValue = Literal["none", "monthly", "quarterly", "semiannual", "yearly"]
RiskStatusValue = Literal["low", "medium", "high"]


class TaskBase(BaseModel):
    title: str
    description: Optional[str] = None
    task_type: Optional[TaskTypeValue] = "one_time"
    status: Optional[Literal["todo", "in_progress", "done"]] = "todo"
    operational_status: Optional[OperationalStatusValue] = None
    priority: Optional[Literal["low", "medium", "high"]] = "medium"
    kanban_enabled: bool = True
    frequency: Optional[FrequencyValue] = None
    risk_status: Optional[RiskStatusValue] = None
    reporting_required: Optional[bool] = None
    category: Optional[str] = None
    direction_program: Optional[str] = None  # Legacy — prefer category.
    external_id: Optional[str] = None
    start_date: Optional[date] = None
    initial_deadline: Optional[date] = None
    current_deadline: Optional[date] = None
    deadline: Optional[datetime] = None
    client_id: Optional[UUID] = None
    project_id: Optional[UUID] = None
    created_by: Optional[UUID] = None
    assigned_to: Optional[UUID] = None
    custom_fields: Optional[dict] = None


class TaskCreate(BaseModel):
    title: str
    description: Optional[str] = None
    task_type: Optional[TaskTypeValue] = "one_time"
    status: Optional[Literal["todo", "in_progress", "done"]] = "todo"
    operational_status: Optional[OperationalStatusValue] = None
    priority: Optional[Literal["low", "medium", "high"]] = "medium"
    kanban_enabled: bool = True
    frequency: Optional[FrequencyValue] = None
    risk_status: Optional[RiskStatusValue] = None
    reporting_required: Optional[bool] = None
    category: Optional[str] = None
    direction_program: Optional[str] = None  # Legacy — prefer category.
    deadline: Optional[datetime] = None
    client_id: Optional[UUID] = None
    project_id: Optional[UUID] = None
    assigned_to: Optional[UUID] = None
    external_id: Optional[str] = None
    start_date: Optional[date] = None
    custom_fields: Optional[dict] = None

    @field_validator("custom_fields")
    @classmethod
    def validate_custom_fields_dict(cls, v):
        if v is None:
            return None
        if not isinstance(v, dict):
            raise ValueError("custom_fields obyekt (dict) olmalıdır, siyahı/mətn/rəqəm qəbul edilmir")
        return v


class TaskResponse(TaskBase):
    id: UUID
    created_at: datetime
    updated_at: datetime
    client: Optional[ClientSummary] = None
    project: Optional[ProjectSummary] = None
    created_by_user: Optional[UserSummary] = None
    assigned_user: Optional[UserSummary] = None

    model_config = ConfigDict(from_attributes=True)


class TaskUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    task_type: Optional[TaskTypeValue] = None
    status: Optional[Literal["todo", "in_progress", "done"]] = None
    operational_status: Optional[OperationalStatusValue] = None
    priority: Optional[Literal["low", "medium", "high"]] = None
    kanban_enabled: Optional[bool] = None
    frequency: Optional[FrequencyValue] = None
    risk_status: Optional[RiskStatusValue] = None
    reporting_required: Optional[bool] = None
    category: Optional[str] = None
    direction_program: Optional[str] = None  # Legacy — prefer category.
    deadline: Optional[datetime] = None
    client_id: Optional[UUID] = None
    project_id: Optional[UUID] = None
    assigned_to: Optional[UUID] = None
    external_id: Optional[str] = None
    start_date: Optional[date] = None
    custom_fields: Optional[dict] = None

    @field_validator("custom_fields")
    @classmethod
    def validate_custom_fields_dict(cls, v):
        if v is None:
            return None
        if not isinstance(v, dict):
            raise ValueError("custom_fields obyekt (dict) olmalıdır, siyahı/mətn/rəqəm qəbul edilmir")
        return v


class LoginRequest(BaseModel):
    email: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str
    user: UserResponse

class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8)


class ResetPasswordResponse(BaseModel):
    message: str
    temporary_password: str
    user: UserResponse

class ClientUpdate(BaseModel):
    company_name: Optional[str] = None
    contact_person: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    address: Optional[str] = None
    notes: Optional[str] = None

class TaskCommentCreate(BaseModel):
    comment: str


class TaskCommentResponse(BaseModel):
    id: UUID
    task_id: UUID
    user_id: UUID
    comment: str
    created_at: datetime
    user: Optional[UserSummary] = None

    model_config = ConfigDict(from_attributes=True)


# ═══════════════════════════════════════════════════════════════════════════
# Category Options (Phase 6D)
# ═══════════════════════════════════════════════════════════════════════════


class CategoryOptionCreate(BaseModel):
    name: str


class CategoryOptionUpdate(BaseModel):
    name: Optional[str] = None
    is_active: Optional[bool] = None
    sort_order: Optional[int] = None


class CategoryOptionRead(BaseModel):
    id: UUID
    name: str
    is_active: bool
    sort_order: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)

class TaskCommentUpdate(BaseModel):
    comment: str

class UserUpdate(BaseModel):
    full_name: Optional[str] = None
    email: Optional[str] = None
    role_id: Optional[int] = None
    is_active: Optional[bool] = None
    password: Optional[str] = Field(default=None, min_length=6)

class TaskAttachmentResponse(BaseModel):
    id: UUID
    task_id: UUID
    uploaded_by: Optional[UUID] = None
    file_name: str
    file_path: str
    file_type: Optional[str] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class NotificationTaskSummary(BaseModel):
    id: UUID
    title: str
    status: Optional[str] = None
    operational_status: Optional[str] = None
    priority: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class NotificationRead(BaseModel):
    id: UUID
    user_id: UUID
    actor_id: Optional[UUID] = None
    task_id: Optional[UUID] = None
    type: str
    title: str
    message: str
    is_read: bool
    created_at: datetime
    read_at: Optional[datetime] = None
    metadata: dict[str, Any] = Field(default_factory=dict, validation_alias="metadata_")
    actor: Optional[UserSummary] = None
    task: Optional[NotificationTaskSummary] = None

    model_config = ConfigDict(from_attributes=True)


class NotificationListResponse(BaseModel):
    items: list[NotificationRead]
    total: int
    unread_count: int


class NotificationUnreadCountResponse(BaseModel):
    unread_count: int
