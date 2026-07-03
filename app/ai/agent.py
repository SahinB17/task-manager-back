from datetime import datetime, timedelta, timezone
import json
import os
from types import SimpleNamespace
from typing import Any, Optional

from fastapi import HTTPException
from openai import OpenAI, OpenAIError
from sqlalchemy.orm import Session

from app import models
from app.ai.tools import run_tool
from app.ai.utils import (
    WRITE_TOOLS,
    decode_confirmation_token,
    make_confirmation_token,
    mask_sensitive_arguments,
    sanitize_tool_arguments,
)

# --- Model routing configuration ---
# Primary model for read/search/dashboard operations.
# Set to "deepseek-v4-flash" to route through DeepSeek Chat Completions API.
READ_MODEL = os.getenv("AI_READ_MODEL", "gpt-5.4-mini")

# Fallback model when primary model fails (network error, rate limit, etc.)
FALLBACK_MODEL = os.getenv("AI_FALLBACK_MODEL", "gpt-5.4-mini")

# Legacy model override — takes precedence in non-routed code paths
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5.4-mini")

# DeepSeek configuration (only needed when READ_MODEL starts with "deepseek")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")

# Write tools that require admin or manager role (role_id 1 or 2).
# These mirror the is_admin_or_manager() checks in tools.py.
# Employees (role_id 3) see a descriptive message instead of a confirmation prompt.
ADMIN_ONLY_TOOLS = {
    "create_task",
    "create_customer",
    "update_customer",
    "create_user",
    "update_user",
}


def get_openai_client():
    if not os.getenv("OPENAI_API_KEY"):
        raise HTTPException(
            status_code=503,
            detail="OPENAI_API_KEY is not configured"
        )

    return OpenAI()


def _is_deepseek_model(model: str) -> bool:
    """Check if the model name indicates a DeepSeek provider."""
    return model.startswith("deepseek")


def _get_deepseek_client() -> OpenAI:
    """Create an OpenAI-compatible client pointed at DeepSeek's API."""
    api_key = os.getenv("DEEPSEEK_API_KEY")
    base_url = DEEPSEEK_BASE_URL
    if not api_key:
        raise HTTPException(
            status_code=503,
            detail="DEEPSEEK_API_KEY is not configured"
        )
    return OpenAI(api_key=api_key, base_url=base_url)


def convert_responses_tools_to_chat_tools(tools: list[dict]) -> list[dict]:
    """Convert OpenAI Responses API tool format to Chat Completions tool format.

    Responses API format:
        {"type": "function", "name": "...", "description": "...", "parameters": {...}}

    Chat Completions format:
        {"type": "function", "function": {"name": "...", "description": "...", "parameters": {...}}}
    """
    chat_tools = []
    for tool in tools:
        if tool.get("type") != "function":
            chat_tools.append(tool)
            continue

        chat_tool = {"type": "function"}
        chat_tool["function"] = {
            "name": tool["name"],
            "description": tool.get("description", ""),
            "parameters": tool.get("parameters", {}),
        }
        chat_tools.append(chat_tool)

    return chat_tools


def get_ai_tools():
    return [
        {
            "type": "function",
            "name": "read_tasks",
            "description": (
                    "Taskları oxumaq və filterləmək üçün istifadə edilir. "
                    "Bütün tasklar, gecikən tasklar, statusa görə tasklar, icraçıya görə tasklar, "
                    "icraçıya görə tasklar və deadline aralığı üçün bu tool çağırılmalıdır. "
                    "User 'Şahinə aid olanları göstər', 'Turanın tasklarını göstər', "
                    "'Əliyə verilən tapşırıqlar', 'filankəsə aid tasklar' yazarsa assignee_query istifadə et. "
                    "Belə hallarda query sahəsinə şəxsin adını yazma."
                ),
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "query": {
                        "type": ["string", "null"],
                        "description": "Task başlığı və ya açıqlamasında axtarış mətni. Bütün tasklar istənirsə null olmalıdır."
                    },
                    "status": {
                        "type": ["string", "null"],
                        "enum": ["todo", "in_progress", "done", None],
                        "description": "Task status filteri."
                    },
                    "priority": {
                        "type": ["string", "null"],
                        "enum": ["low", "medium", "high", None],
                        "description": "Task prioritet filteri."
                    },
                    "overdue": {
                        "type": ["boolean", "null"],
                        "description": "Gecikmiş/vaxtı keçmiş tasklar üçün true."
                    },
                    "client_query": {
                        "type": ["string", "null"],
                        "description": "Müştəri adı, şirkət adı, email və ya telefon üzrə filter."
                    },
                    "assignee_query": {
                        "type": ["string", "null"],
                        "description": (
                            "İcraçı adı və ya email üzrə filter. "
                            "Məsələn: 'Şahinə aid olanları göstər' → assignee_query='Şahin'. "
                            "'Turanın taskları' → assignee_query='Turan'."
                        )
                    },
                    "deadline_before": {
                        "type": ["string", "null"],
                        "description": "Bu tarixdən əvvəl deadline olan tasklar. ISO 8601 format."
                    },
                    "deadline_after": {
                        "type": ["string", "null"],
                        "description": "Bu tarixdən sonra deadline olan tasklar. ISO 8601 format."
                    },
                    "limit": {
                        "type": ["integer", "null"],
                        "description": "Göstəriləcək maksimum nəticə sayı. Default 20."
                    },
                    "exclude_completed": {
                        "type": ["boolean", "null"],
                        "description": "Tamamlanmış taskları çıxarmaq üçün true. Aktiv/yaxın deadline sorğularında true istifadə et."
                    },
                },
            },
        },
        {
            "type": "function",
            "name": "read_customers",
            "description": (
                "Müştəriləri oxumaq və axtarmaq üçün istifadə edilir. "
                "Müştəri siyahısı, şirkət adı, əlaqədar şəxs, email və telefon axtarışı üçün bu tool çağırılmalıdır."
            ),
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "query": {
                        "type": ["string", "null"],
                        "description": "Müştəri adı, əlaqədar şəxs, email və ya telefon üzrə axtarış."
                    },
                    "limit": {
                        "type": ["integer", "null"],
                        "description": "Göstəriləcək maksimum nəticə sayı. Default 20."
                    },
                },
            },
        },
        {
            "type": "function",
            "name": "read_users",
            "description": (
                "İstifadəçiləri oxumaq və axtarmaq üçün istifadə edilir. "
                "İşçilər, menecerlər, adminlər və aktiv/deaktiv istifadəçilər üçün bu tool çağırılmalıdır."
            ),
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "query": {
                        "type": ["string", "null"],
                        "description": "İstifadəçi adı və ya email üzrə axtarış."
                    },
                    "role_id": {
                        "type": ["integer", "null"],
                        "enum": [1, 2, 3, None],
                        "description": "1 admin, 2 manager, 3 employee."
                    },
                    "is_active": {
                        "type": ["boolean", "null"],
                        "description": "Aktiv istifadəçilər üçün true, deaktiv üçün false."
                    },
                    "limit": {
                        "type": ["integer", "null"],
                        "description": "Göstəriləcək maksimum nəticə sayı. Default 20."
                    },
                },
            },
        },
        {
            "type": "function",
            "name": "get_dashboard_summary",
            "description": (
                "Dashboard vəziyyəti, ümumi task sayı, icrada olanlar, Gözləmədə olanlar, tamamlananlar, "
                "rəsmi Gecikmə statusu, son tarixi keçmiş tapşırıqlar və ümumi gecikmə riski üçün istifadə edilir."
            ),
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {},
            },
            
        },
                {
            "type": "function",
            "name": "create_task",
            "description": (
                "Yeni tapşırıq (task) yaratmaq üçün istifadə edilir. "
                "Başlıq, icraçı, prioritet mütləq lazımdır. Deadline, layihə, növ istəyə bağlıdır."
            ),
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "title": {
                        "type": ["string", "null"],
                        "description": "Task başlığı."
                    },
                    "description": {
                        "type": ["string", "null"],
                        "description": "Task açıqlaması."
                    },
                    "task_type": {
                        "type": ["string", "null"],
                        "enum": [
                            "one_time",
                            "recurring",
                            "project",
                            "general",
                            None,
                        ],
                        "description": "Task növü: one_time (birdəfəlik), recurring (təkrarlanan), project (layihə)."
                    },
                    "status": {
                        "type": ["string", "null"],
                        "enum": ["todo", "in_progress", "done", None],
                        "description": "Yeni task statusu. Default todo."
                    },
                    "priority": {
                        "type": ["string", "null"],
                        "enum": ["low", "medium", "high", None],
                        "description": "Task prioriteti."
                    },
                    "deadline": {
                        "type": ["string", "null"],
                        "description": "Deadline ISO 8601 formatında."
                    },
                    "client_id": {
                        "type": ["string", "null"],
                        "description": "Müştəri ID-si. Yalnız user açıq ID veribsə istifadə et."
                    },
                    "client_query": {
                        "type": ["string", "null"],
                        "description": "Müştəri adı və ya şirkət adı."
                    },
                    "assigned_to": {
                        "type": ["string", "null"],
                        "description": "İcraçı user ID-si. Yalnız user açıq ID veribsə istifadə et."
                    },
                    "assignee_query": {
                        "type": ["string", "null"],
                        "description": "İcraçı adı və ya email."
                    },
                    "project_id": {
                        "type": ["string", "null"],
                        "description": "Layihə ID-si. Yalnız user açıq ID veribsə istifadə et."
                    },
                    "project_query": {
                        "type": ["string", "null"],
                        "description": "Layihə adı və ya kodu ilə axtarış."
                    },
                    "description": {
                        "type": ["string", "null"],
                        "description": "Tapşırıq açıqlaması."
                    },
                },
            },
        },
        {
            "type": "function",
            "name": "change_task_status",
            "description": (
                "Mövcud taskın statusunu dəyişmək üçün istifadə edilir. "
                "Məsələn: tamamlandı et, icraya al, gözləməyə qaytar."
            ),
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "task_id": {
                        "type": ["string", "null"],
                        "description": "Task ID-si. Yalnız user açıq ID veribsə istifadə et."
                    },
                    "task_query": {
                        "type": ["string", "null"],
                        "description": "Task başlığı və ya taskı tapmaq üçün mətn."
                    },
                    "status": {
                        "type": ["string", "null"],
                        "enum": ["todo", "in_progress", "done", None],
                        "description": "Yeni status."
                    },
                },
            },
        },
        {
            "type": "function",
            "name": "add_task_comment",
            "description": (
                "Taska comment əlavə etmək üçün istifadə edilir."
            ),
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "task_id": {
                        "type": ["string", "null"],
                        "description": "Task ID-si. Yalnız user açıq ID veribsə istifadə et."
                    },
                    "task_query": {
                        "type": ["string", "null"],
                        "description": "Task başlığı və ya taskı tapmaq üçün mətn."
                    },
                    "comment": {
                        "type": ["string", "null"],
                        "description": "Əlavə ediləcək comment mətni."
                    },
                },
            },
        },
                {
            "type": "function",
            "name": "create_customer",
            "description": (
                "Yeni müştəri yaratmaq üçün istifadə edilir. "
                "Şirkət adı, əlaqədar şəxs, telefon, email, ünvan və qeydlər bu tool-a verilə bilər."
            ),
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "company_name": {
                        "type": ["string", "null"],
                        "description": "Müştərinin şirkət adı."
                    },
                    "contact_person": {
                        "type": ["string", "null"],
                        "description": "Əlaqədar şəxs."
                    },
                    "phone": {
                        "type": ["string", "null"],
                        "description": "Telefon nömrəsi."
                    },
                    "email": {
                        "type": ["string", "null"],
                        "description": "Email ünvanı."
                    },
                    "address": {
                        "type": ["string", "null"],
                        "description": "Ünvan."
                    },
                    "notes": {
                        "type": ["string", "null"],
                        "description": "Əlavə qeydlər."
                    },
                },
            },
        },
        {
            "type": "function",
            "name": "update_customer",
            "description": (
                "Mövcud müştərinin məlumatlarını yeniləmək üçün istifadə edilir. "
                "Telefon, email, ünvan, əlaqədar şəxs, şirkət adı və qeydlər dəyişdirilə bilər."
            ),
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "client_id": {
                        "type": ["string", "null"],
                        "description": "Müştəri ID-si. Yalnız user açıq ID veribsə istifadə et."
                    },
                    "client_query": {
                        "type": ["string", "null"],
                        "description": "Tapılacaq müştərinin adı, şirkət adı, emaili və ya telefonu."
                    },
                    "company_name": {
                        "type": ["string", "null"],
                        "description": "Yeni şirkət adı."
                    },
                    "contact_person": {
                        "type": ["string", "null"],
                        "description": "Yeni əlaqədar şəxs."
                    },
                    "phone": {
                        "type": ["string", "null"],
                        "description": "Yeni telefon nömrəsi."
                    },
                    "email": {
                        "type": ["string", "null"],
                        "description": "Yeni email ünvanı."
                    },
                    "address": {
                        "type": ["string", "null"],
                        "description": "Yeni ünvan."
                    },
                    "notes": {
                        "type": ["string", "null"],
                        "description": "Yeni qeydlər."
                    },
                },
            },
        },
                {
            "type": "function",
            "name": "create_user",
            "description": (
                "Yeni istifadəçi yaratmaq üçün istifadə edilir. "
                "Ad soyad, email, password, rol və aktivlik statusu bu tool-a verilə bilər."
            ),
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "full_name": {
                        "type": ["string", "null"],
                        "description": "İstifadəçinin ad soyadı."
                    },
                    "email": {
                        "type": ["string", "null"],
                        "description": "İstifadəçinin email ünvanı."
                    },
                    "password": {
                        "type": ["string", "null"],
                        "description": "İstifadəçinin password-u. Yalnız user açıq yazıbsa istifadə et."
                    },
                    "role_id": {
                        "type": ["integer", "string", "null"],
                        "description": "1 admin, 2 manager, 3 employee."
                    },
                    "is_active": {
                        "type": ["boolean", "string", "null"],
                        "description": "Aktiv istifadəçi üçün true, deaktiv üçün false."
                    },
                },
            },
        },
        {
            "type": "function",
            "name": "update_user",
            "description": (
                "Mövcud istifadəçinin məlumatlarını yeniləmək üçün istifadə edilir. "
                "Ad soyad, email, password, rol və aktivlik statusu dəyişdirilə bilər."
            ),
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "user_id": {
                        "type": ["string", "null"],
                        "description": "İstifadəçi ID-si. Yalnız user açıq ID veribsə istifadə et."
                    },
                    "user_query": {
                        "type": ["string", "null"],
                        "description": "Tapılacaq istifadəçinin adı və ya emaili."
                    },
                    "full_name": {
                        "type": ["string", "null"],
                        "description": "Yeni ad soyad."
                    },
                    "email": {
                        "type": ["string", "null"],
                        "description": "Yeni email ünvanı."
                    },
                    "password": {
                        "type": ["string", "null"],
                        "description": "Yeni password. Yalnız user açıq yazıbsa istifadə et."
                    },
                    "role_id": {
                        "type": ["integer", "string", "null"],
                        "description": "Yeni rol: 1 admin, 2 manager, 3 employee."
                    },
                    "is_active": {
                        "type": ["boolean", "string", "null"],
                        "description": "Aktiv etmək üçün true, deaktiv etmək üçün false."
                    },
                },
            },
        },
        {
            "type": "function",
            "name": "read_projects",
            "description": (
                "Layihələri oxumaq və axtarmaq üçün istifadə edilir. "
                "Layihə portfeli, layihə sahibləri (owner), direction/program, "
                "aktiv layihələr, layihə kodu və adı üzrə axtarış üçün bu tool çağırılmalıdır. "
                "include_task_counts=true göndərsəniz hər layihə üçün ümumi/aktiv/gecikən/tamamlanan task sayı da qaytarır. "
                "Layihə adı, kodu, direction_program, owner, approach, is_active kimi field-lər qayıdır."
            ),
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "query": {
                        "type": ["string", "null"],
                        "description": "Layihə adı, kodu və ya açıqlamasında axtarış mətni."
                    },
                    "direction_program": {
                        "type": ["string", "null"],
                        "description": "Direction/program üzrə filter. Məsələn: 'Elektron transformasiya', 'Data analitika'."
                    },
                    "owner": {
                        "type": ["string", "null"],
                        "description": "Layihə sahibi/sahib üzrə filter."
                    },
                    "is_active": {
                        "type": ["boolean", "null"],
                        "description": "Aktiv layihələr üçün true, deaktiv üçün false."
                    },
                    "include_task_counts": {
                        "type": ["boolean", "null"],
                        "description": "Hər layihə üçün task sayı statistikasını (ümumi, aktiv, gecikən, tamamlanan) əlavə etmək üçün true."
                    },
                    "limit": {
                        "type": ["integer", "null"],
                        "description": "Göstəriləcək maksimum nəticə sayı. Default 50."
                    },
                },
            },
        },
        {
            "type": "function",
            "name": "read_project_stages",
            "description": (
                "Layihə mərhələlərini (mərhələlər) oxumaq üçün istifadə edilir. "
                "Plan/fakt tarixləri, gecikmə səbəbləri, "
                "mərhələ statusu, icra faizi (completion_percent), çəki faizi (weight_percent) haqqında məlumat qaytarır. "
                "project_id və ya project_query ilə konkret layihənin mərhələlərini göstərmək olar. "
                "Heç bir filter verilməzsə bütün görünən layihələrin mərhələlərini qaytarır."
            ),
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "project_id": {
                        "type": ["string", "null"],
                        "description": "Layihə ID-si. Yalnız user açıq ID veribsə istifadə et."
                    },
                    "project_query": {
                        "type": ["string", "null"],
                        "description": "Layihə adı və ya kodu ilə axtarış."
                    },
                    "limit": {
                        "type": ["integer", "null"],
                        "description": "Göstəriləcək maksimum nəticə sayı. Default 50."
                    },
                },
            },
        },
        {
            "type": "function",
            "name": "get_project_monitoring_summary",
            "description": (
                "Layihə monitorinq xülasəsi, rəhbərlik üçün icmal, "
                "ümumi layihə sayı, aktiv layihələr, gecikən taskı olan layihələr, "
                "ən çox açıq taskı olan layihələr, ən çox gecikən taskı olan layihələr, "
                "mərhələ status paylanması və gecikmə səbəbləri haqqında məlumat üçün bu tool çağırılmalıdır. "
                "Hər bir layihə üçün ÜMUMİ task sayı (total_tasks), AÇIQ task sayı (open_tasks), "
                "GECİKƏN task sayı (overdue_tasks) və TAMAMLANMIŞ task sayı (completed_tasks) qaytarır. "
                "Hesabatda hər layihə üçün ümumi, açıq və gecikən task sayını birlikdə göstər. "
                "User 'layihə riskləri', 'gecikən layihələr', 'portfolio icmalı', "
                "'rəhbərlik üçün xülasə', 'monitorinq hesabatı' istədikdə bu tool-u çağır."
            ),
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {},
            },
        },
        {
            "type": "function",
            "name": "get_assignee_workload_summary",
            "description": (
                "İcraçı yükü analizi, işçi yükü, task paylanması, "
                "kimdə neçə task olduğunu öyrənmək üçün bu tool çağırılmalıdır. "
                "Hər bir icraçı üçün ümumi task sayı, açıq task, gecikən task, tamamlanan task, "
                "yüksək prioritetli task sayı və bu həftə bitməli task sayı qaytarır. "
                "Həmçinin icraçısız (təyin edilməmiş) tasklar haqqında da məlumat qaytarır. "
                "hər bir icraçı üçün workload_level (low/normal/high/critical) qaytarır. "
                "User 'icraçı yükü', 'işçi yükü', 'assignee workload', "
                "'kimdə neçə task var', 'task paylanması' istədikdə bu tool-u çağır."
            ),
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {},
            },
        },
    ]


def build_system_prompt(current_user: models.User):
    app_timezone = timezone(timedelta(hours=4))
    current_datetime = datetime.now(app_timezone).isoformat()

    return f"""
Sən Sosial Xidmətlər Agentliyinin Strateji planlaşdırma istiqaməti üçün layihə, mərhələ və tapşırıq monitorinqi üzrə AI analitik köməkçisən.

Cavablarını Azərbaycan dilində, professional idarəetmə/reporting üslubunda ver.
Yalnız tool nəticələrinə əsaslan. Raw JSON göstərmə.
Məlumat sistemdə yoxdursa de: "Bu məlumat sistemdə görünmür."
Task, layihə, istifadəçi, rəqəm, deadline, status və gecikmə səbəbi uydurma.

Mapping:
- technical status Kanban statusudur: todo / in_progress / done.
- operational_status SXA icra statusudur və cavabda ona üstünlük ver.
- priority prioritet analizi üçündür.
- project fields: name, code, direction_program, owner, approach, is_active.
- stage fields: planned_start, planned_end, actual_end, status, delay_reason, completion_percent, weight_percent.
- status labels in reports (use Azerbaijani, NOT English): done → Tamamlanıb, in_progress → İcradadır, overdue → Gecikib, waiting → Gözləmədə, completed → Tamamlanıb, delayed → Gecikən, within_deadline → Müddətində, new → Başlanmayıb, partially_completed → Qismən icra

Tool usage:
- read_tasks: task statusu, gecikən tasklar, deadline, priority.
- get_assignee_workload_summary: icraçı yükü, işçi yükü, task paylanması, kimdə neçə task var.
- get_dashboard_summary: dashboard KPI və ümumi icra vəziyyəti.
- read_projects: layihə portfeli, aktiv layihələr, sahiblər, direction/program.
- read_project_stages: mərhələlər, plan/fakt tarixləri, gecikmə səbəbləri, icra faizi.
- get_project_monitoring_summary: rəhbərlik xülasəsi, portfolio riskləri, gecikən layihələr.

Dashboard hesabat qaydaları:
- Gözləmədə yalnız operational_status="waiting" / "Gözləmədə" olan tapşırıqlardır. Texniki todo/new/within_deadline dəyərlərini Gözləmədə kimi sayma.
- Gecikmə üçün get_dashboard_summary nəticəsindəki official_delayed_tasks, date_overdue_not_marked_tasks və total_delayed_tasks field-lərindən istifadə et.
- Gecikmə izahında ayrıca göstər: neçə tapşırıq rəsmi "Gecikmə" statusundadır, neçə tapşırığın son tarixi keçib amma statusu hələ "Gecikmə" deyil, ümumi gecikmə riski neçə unikal tapşırıqdır.
- Tövsiyədə əvvəl bu həftə bitməli tapşırıqların operativ yoxlanılmasını, sonra gecikmiş tapşırıqların layihə, icraçı və prioritet üzrə qruplaşdırılmasını, statusu yenilənməmiş gecikmələrin ayrıca nəzarətə götürülməsini və yüksək prioritetli gecikmələr üzrə resurs bölgüsünə yenidən baxılmasını qeyd et.

Analitik cavab formatı:
1. Qısa xülasə
2. Əsas müşahidələr
3. Risklər
4. Tövsiyə olunan addımlar

Hesabat, xülasə və ya analiz sorğularında həmişə təbii dildə tam cavab yaz. Tool nəticələrini olduğu kimi göstərmə, onları mətnə çevir. Cavab qısa olsa belə, mütləq izahlı cümlə yaz.

── Rəhbərlik hesabatı üçün xüsusi format ──
Əgər user "Rəhbərlik üçün qısa hesabat", "rəhbərlik hesabatı", "idarəetmə hesabatı" və ya oxşar ifadə işlədərsə, qısa xülasə yox, ətraflı strukturlaşdırılmış hesabat hazırla.
Vacib: Hesabat cədvəllərində yalnız Azərbaycan dilində status adları istifadə et. done, in_progress, overdue, waiting, completed, delayed kimi texniki adları GÖSTERMƏ. Bunun əvəzinə: Tamamlanıb, İcradadır, Gecikib, Gözləmədə, Müddətində, Başlanmayıb, Qismən icra işlət.

Rəhbərlik hesabatı formatı:
# Rəhbərlik Hesabatı
Tarix: ... | Hazırlayan: ...

1. Qısa xülasə — ümumi icra vəziyyəti, gecikən task/layihə sayı, aktiv layihələr.

2. Əsas müşahidələr
   2.1 Ümumi icra vəziyyəti (cədvəl): Ümumi tapşırıq, İcradadır, Gözləmədə, Tamamlanıb, Gecikən.
   2.2 Gecikən layihələr (cədvəl): Layihə, Kod, Sahib, Ümumi task, Açıq task, Gecikən task, Tamamlanan.
       get_project_monitoring_summary hər layihə üçün total_tasks, open_tasks, overdue_tasks, completed_tasks qaytarır.
   2.3 Mərhələ status bölgüsü — read_project_stages ilə status paylanması.
   2.4 Gecikmə səbəbləri — delay_reason_summary-dən əsas səbəblər.

3. Risklər və təsir təhlili — gecikən layihələrin potensial təsiri.

4. Tövsiyə olunan addımlar — konkret, icra edilə bilən tövsiyələr.

Rəhbərlik hesabatı üçün tool-lar: get_dashboard_summary, get_project_monitoring_summary, read_projects(include_task_counts=true), read_project_stages.

── İşçi hesabatı üçün xüsusi format ──
User "işçilərin qısa hesabatı", "istifadəçilərin qısa hesabatı" və ya "işçi hesabatı" istədikdə qısa işçi xülasəsi hazırla.
Ümumi KPI, dashboard və layihə məlumatlarını daxil etmə. Sadəcə işçi statistikasını göstər: ümumi işçi sayı, aktiv/deaktiv sayı, rollar üzrə bölgü (admin, menecer, işçi).

── İcraçı yükü analizi formatı ──
User "icraçı yükü", "işçi yükü", "kimdə neçə task var", "assignee workload", "task paylanması", "icraçı yükünü analiz et" istədikdə get_assignee_workload_summary tool-u çağır və təbii dildə analiz yaz.

Cavab formatı:
1. Qısa xülasə — ümumi task sayı, neçə icraçı olduğu, icraçısız task sayı.
2. Ən çox yüklənmiş icraçılar — ən çox açıq taskı olan icraçılar siyahısı.
3. Gecikmə riski olan icraçılar — gecikən taskı olan və workload_level high/critical olanlar.
4. Tövsiyə olunan addımlar — yükün yenidən bölüşdürülməsi və ya prioritetləşdirmə.

Hər icraçı üçün: adı, ümumi task sayı, açıq task, gecikən task, yüksək prioritetli task, bu həftə bitməli task və workload_level (low/normal/high/critical) göstər.

── Task yaratma qaydaları ──
User "yeni tapşırıq", "task yarat", "create task" və ya oxşar ifadə işlədərsə:

Lazımi məlumatlar (mütləq):
1. title — tapşırığın adı
2. assignee_query — icraçının adı (əvvəlcə read_users ilə tap)
3. priority — high, medium və ya low

İstəyə bağlı (əgər user verməyibsə null göndər):
- deadline — son tarix (ISO 8601)
- task_type — one_time (birdəfəlik, default), recurring (təkrarlanan), project (layihə)
- project_query — əlaqəli layihə
- client_query — yalnız user açıq şəkildə müştəri adı çəkərsə
- description — açıqlama
- status — default todo
Qeyd: client/müştəri mütləq deyil. User müştəri adı verməyibsə client_query null göndər.

Follow-up cavabları parse et:
User "1. test ai 2. Elmin 3. low 4. 2026-07-01 5. OMXÇ 6. birdəfəlik 7. test açıqlama" kimi formatda cavab verərsə:
1. → title (tapşırıq adı)
2. → assignee_query (icraçı adı, read_users ilə tap)
3. → priority (high / medium / low)
4. → deadline (ISO 8601 tarixi, məs: 2026-07-01)
5. → project_query (layihə adı və ya kodu)
6. → task_type (birdəfəlik/one_time, təkrarlanan/recurring, layihə/project, ümumi/general)
7. → description (açıqlama)
Sonra create_task çağır, çatışmayanları null göndęr.

Deadline parse etmə:
- "sabah" = bugünkü tarix + 1 gün
- "bu höftə" = bu höftənin cuma günü
- "2026-07-01" = ISO format olduğu kimi göndær
- "1 iyul 2026" kimi AZ formatında çevir
Anlaşılmaz deadline olarsa açıqlama istə.

Layihə adı verilərsə, ƙvvəlcə read_projects ilə tap. Bir neçə oxşar layihə tapılarsa açıqlama istə. Layihə tapılmazsa "Layihə tapılmadı" de və yenidən soruş.

Task növü mapping:
- ümumi, general → general
- birdəfəlik, one_time, one-time, tək tapşırıq → one_time
- təkrarlanan, recurring → recurring
- layihə, project → project

Task növü soruşulduqda yalnız bunları sadala:
- Ümumi (general)
- Birdəfəlik (one_time)
- Təkrarlanan (recurring)
- Layihə (project)

create_task confirmation tələb edir. Təsdiq olmadan task yaradılmaz.
create_task çağırıldıqdan sonra "Təsdiq üçün məlumatları hazırladım" de və gözlə. "yaradıldı" demə, confirmation token gələnə qədər gözlə.

Write əməliyyatlarında mövcud confirmation flow qalmalıdır.
Tool-lar backend RBAC ilə məhdudlaşdırılır; görünməyən data haqqında danışma.

── Mərkəzi həlledici (resolver) davranışı ──
Backend artıq Azərbaycan şəkilçilərini avtomatik normalizə edir:
- Adlar: Nərminə→Nərmin, Elminə→Elmin, Fəridə→Fərid, Hacıya→Hacı kimi tanınır.
- Layihə kodu: L026, PRJ-1 kimi dəqiq uyğunluqlar dərhal seçilir.
- Status/prioritet/növ: Azərbaycan dilində yazılmış dəyərlər avtomatik map edilir.
- Deadline: "sabah", "bu həftə", "1 iyul" kimi ifadələr avtomatik parse edilir.

Ambiguous (ikili) nəticələrdə:
- Tək dəqiq uyğunluq → confirmation-a keç.
- Bir neçə uyğunluq → istifadəçidən dəqiqləşdirmə istə.
- Uyğunluq yoxdursa → istifadəçidən yenidən soruş.
Heç vaxt bir neçə variant arasından səssizcə birini seçmə.

Multi-turn task yaratma:
- create_task əvvəlki mesajlarda verilmiş məlumatları da nəzərə al.
- İstifadəçi layihə/növ kimi çatışmayan məlumatı sonrakı mesajda verərsə, əvvəlki məlumatlarla birlikdə create_task çağır.
- "Layihə təsdiqləndi" dedikdə dayanma — çatışmayan məlumat tamamlanıbsa create_task çağır.

Current datetime UTC+04:00: {current_datetime}
Current user: id={current_user.id}, role_id={current_user.role_id}, name={current_user.full_name}
Backend icazələri ayrıca yoxlayır; icazəsiz məlumatı uydurma.
"""


def safe_json_loads(value: str):
    try:
        parsed = json.loads(value or "{}")
    except json.JSONDecodeError:
        return {}

    if not isinstance(parsed, dict):
        return {}

    return parsed

def extract_function_calls(response) -> list[Any]:
    output_items = getattr(response, "output", []) or []

    function_calls = []
    for item in output_items:
        if getattr(item, "type", None) == "function_call":
            function_calls.append(item)

    return function_calls


def get_response_text(response):
    output_text = getattr(response, "output_text", None)
    if output_text:
        return output_text

    output_items = getattr(response, "output", []) or []
    text_parts = []

    for item in output_items:
        content = getattr(item, "content", None)
        if not content:
            continue

        for content_item in content:
            text = getattr(content_item, "text", None)
            if text:
                text_parts.append(text)

    return "\n".join(text_parts).strip()


def _normalize_chat_completions_response(response) -> SimpleNamespace:
    """Wrap a ChatCompletions response so it quacks like a Responses API response.

    This adapter lets extract_function_calls() and get_response_text() work
    unchanged regardless of which API was called.
    """
    choice = response.choices[0] if response.choices else None
    msg = choice.message if choice else None

    output_items: list[SimpleNamespace] = []
    if msg and msg.tool_calls:
        for tc in msg.tool_calls:
            output_items.append(
                SimpleNamespace(
                    type="function_call",
                    name=tc.function.name,
                    call_id=tc.id,
                    arguments=tc.function.arguments,
                )
            )

    output_text = msg.content if msg and msg.content else ""

    return SimpleNamespace(
        id=response.id,
        output=output_items,
        output_text=output_text,
    )


def _build_full_chat_history(
    system_prompt: str,
    context_message: str,
    first_raw_chat_response,
    tool_outputs: list[dict],
) -> list[dict]:
    """Build a full Chat Completions message history from a first-call response + tool outputs."""
    messages: list[dict] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": context_message},
    ]

    if first_raw_chat_response and first_raw_chat_response.choices:
        first_msg = first_raw_chat_response.choices[0].message
        assistant_entry: dict[str, Any] = {"role": "assistant", "content": first_msg.content}
        if first_msg.tool_calls:
            assistant_entry["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in first_msg.tool_calls
            ]
        messages.append(assistant_entry)

    # Convert tool_outputs from Responses API format to Chat Completions format
    for to in tool_outputs:
        messages.append(
            {
                "role": "tool",
                "tool_call_id": to["call_id"],
                "content": to["output"],
            }
        )

    return messages


def _build_full_openai_input(
    system_prompt: str,
    context_message: str,
    first_raw_chat_response,
    tool_outputs: list[dict],
) -> list[dict]:
    """Build a full Responses API input array from DeepSeek response + tool outputs.

    Used when falling back from DeepSeek to OpenAI on the second LLM call
    where previous_response_id is not available across providers.
    """
    full_input: list[dict] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": context_message},
    ]

    if first_raw_chat_response and first_raw_chat_response.choices:
        first_msg = first_raw_chat_response.choices[0].message
        assistant_entry: dict[str, Any] = {"role": "assistant"}
        if first_msg.content:
            assistant_entry["content"] = first_msg.content
        else:
            assistant_entry["content"] = None
        if first_msg.tool_calls:
            assistant_entry["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in first_msg.tool_calls
            ]
        full_input.append(assistant_entry)

    # Tool outputs are already in Responses API format:
    #   {"type": "function_call_output", "call_id": "...", "output": "..."}
    full_input.extend(tool_outputs)

    return full_input


def _call_provider(
    *,
    model: str,
    system_prompt: str,
    context_message: str,
    tools: list,
    tool_outputs: Optional[list[dict]] = None,
    previous_response_id: Optional[str] = None,
    first_raw_chat_response=None,
):
    """Call the LLM using the appropriate provider (DeepSeek or OpenAI).

    Returns:
        tuple[Any, Any]: (normalized_response, raw_chat_response)
            - normalized_response: works with extract_function_calls() / get_response_text()
            - raw_chat_response: the raw ChatCompletions response (for DeepSeek path, for
              second-call history); None for OpenAI Responses API path.
    """
    if _is_deepseek_model(model):
        # --- DeepSeek Chat Completions path ---
        client = _get_deepseek_client()

        if tool_outputs is None:
            # First call: plan which tool to call
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": context_message},
                ],
                tools=convert_responses_tools_to_chat_tools(tools),
            )
        else:
            # Second call: summarize read-tool results
            # Build full history (Chat Completions is stateless)
            messages = _build_full_chat_history(
                system_prompt, context_message,
                first_raw_chat_response, tool_outputs,
            )
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                tools=convert_responses_tools_to_chat_tools(tools),
            )

        normalized = _normalize_chat_completions_response(response)
        return normalized, response

    # --- OpenAI Responses API path ---
    client = get_openai_client()

    if tool_outputs is None:
        # First call
        response = client.responses.create(
            model=model,
            input=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": context_message},
            ],
            tools=tools,
        )
    else:
        # Second call
        response = client.responses.create(
            model=model,
            previous_response_id=previous_response_id,
            input=tool_outputs,
            tools=tools,
        )

    # Responses API responses work natively with extract_function_calls/get_response_text
    return response, None


def _call_llm_with_fallback(
    *,
    model: str,
    system_prompt: str,
    context_message: str,
    tools: list,
    tool_outputs: Optional[list[dict]] = None,
    previous_response_id: Optional[str] = None,
    first_raw_chat_response=None,
):
    """Call the LLM with automatic provider routing and fallback.

    Tries the primary model first. If it fails and a different FALLBACK_MODEL
    is configured, retries once with the fallback model via OpenAI Responses API.

    Returns:
        tuple[Any, Any]: (normalized_response, raw_chat_response)
    """
    last_error = None

    # --- Primary attempt ---
    try:
        return _call_provider(
            model=model,
            system_prompt=system_prompt,
            context_message=context_message,
            tools=tools,
            tool_outputs=tool_outputs,
            previous_response_id=previous_response_id,
            first_raw_chat_response=first_raw_chat_response,
        )
    except Exception as exc:
        last_error = exc

    # --- Fallback attempt (only if FALLBACK_MODEL differs from primary) ---
    fallback = FALLBACK_MODEL
    if fallback and fallback != model:
        try:
            is_deepseek_primary = _is_deepseek_model(model)
            if is_deepseek_primary and tool_outputs is not None:
                # DeepSeek first call succeeded, second call failed
                # Build full input for OpenAI (previous_response_id is provider-specific)
                full_input = _build_full_openai_input(
                    system_prompt, context_message,
                    first_raw_chat_response, tool_outputs,
                )
                client = get_openai_client()
                response = client.responses.create(
                    model=fallback,
                    input=full_input,
                    tools=tools,
                )
                return response, None
            else:
                # First call fallback: use normal OpenAI Responses API path
                return _call_provider(
                    model=fallback,
                    system_prompt=system_prompt,
                    context_message=context_message,
                    tools=tools,
                    tool_outputs=tool_outputs,
                    previous_response_id=None,
                    first_raw_chat_response=None,
                )
        except Exception:
            pass  # fall through to raise the original error

    raise HTTPException(
        status_code=502,
        detail=f"AI provider error: {last_error}",
    )


def run_readonly_agent(
    *,
    message: str,
    db: Session,
    current_user: models.User,
    conversation_context: Optional[str] = None,
):
    system_prompt = build_system_prompt(current_user)

    context_message = (
        f"Son chat konteksti:\n{conversation_context}\n\n"
        f"İndiki user mesajı: {message}"
        if conversation_context
        else message
    )

    first_response, first_raw_chat = _call_llm_with_fallback(
        model=READ_MODEL,
        system_prompt=system_prompt,
        context_message=context_message,
        tools=get_ai_tools(),
    )

    function_calls = extract_function_calls(first_response)

    if not function_calls:
        final_text = get_response_text(first_response)
        return {
            "status": "completed",
            "message": final_text or "Sorğunu başa düşdüm, amma uyğun tool seçilmədi.",
            "tool_results": [],
        }

    tool_outputs = []
    tool_results = []

    for function_call in function_calls:
        tool_name = getattr(function_call, "name", None)
        call_id = getattr(function_call, "call_id", None)
        arguments = safe_json_loads(getattr(function_call, "arguments", "{}"))

        if not tool_name or not call_id:
            continue

        if tool_name in WRITE_TOOLS:
            # UX guard: reject admin-only tools early for non-privileged users.
            # Backend RBAC in tools.py would catch this anyway, but showing a
            # clear message upfront avoids a confusing "confirm → rejected" flow.
            if tool_name in ADMIN_ONLY_TOOLS and current_user.role_id not in (1, 2):
                return {
                    "status": "completed",
                    "message": "Bu əməliyyatı yalnız admin və ya menecer edə bilər.",
                    "tool_results": [],
                }

            confirmation_token = make_confirmation_token(
                tool_name=tool_name,
                arguments=arguments,
                current_user=current_user,
            )

            return {
                "status": "pending_confirmation",
                "message": "Bu əməliyyat təsdiq tələb edir.",
                "requires_confirmation": True,
                "confirmation_token": confirmation_token,
                "pending_tool": {
                "tool_name": tool_name,
                "arguments": mask_sensitive_arguments(
                    sanitize_tool_arguments(tool_name, arguments)
                ),
                },
                "tool_results": [],
            }

        tool_result = run_tool(
            tool_name=tool_name,
            arguments=arguments,
            db=db,
            current_user=current_user,
        )
        tool_results.append(tool_result)

        tool_outputs.append(
            {
                "type": "function_call_output",
                "call_id": call_id,
                "output": json.dumps(tool_result, ensure_ascii=False, default=str),
            }
        )

    if not tool_outputs:
        return {
            "status": "failed",
            "message": "AI tool çağırışı yarandı, amma icra edilə bilmədi.",
            "tool_results": tool_results,
        }

    final_response, _ = _call_llm_with_fallback(
        model=READ_MODEL,
        system_prompt=system_prompt,
        context_message=context_message,
        tools=get_ai_tools(),
        tool_outputs=tool_outputs,
        previous_response_id=first_response.id,
        first_raw_chat_response=first_raw_chat,
    )

    final_text = get_response_text(final_response)

    fallback_message = "Nəticə hazırdır."
    if tool_results and isinstance(tool_results[0], dict):
        fallback_message = tool_results[0].get("message") or fallback_message

    return {
        "status": "completed",
        "message": final_text or fallback_message,
        "tool_results": tool_results,
    }

def confirm_agent_action(
    *,
    confirmation_token: str,
    db: Session,
    current_user: models.User,
):
    tool_name, arguments = decode_confirmation_token(
        token=confirmation_token,
        current_user=current_user,
    )

    tool_result = run_tool(
        tool_name=tool_name,
        arguments=arguments,
        db=db,
        current_user=current_user,
    )

    return {
        "status": tool_result.get("status", "completed"),
        "message": tool_result.get("message", "Əməliyyat icra edildi."),
        "requires_confirmation": False,
        "tool_results": [tool_result],
    }
