"""
Global Excel import configuration.

Task-first import: only title is required. No projects/stages/portfolio.
Column and value alias maps support neutral/global workbooks and legacy
headers that still need to roundtrip through the global import/export flow.
"""

GLOBAL_TASK_COLUMN_ALIASES = {
    "title": [
        "Tapşırıq",
        "Başlıq",
        "Task",
        "Task Title",
        "Title",
        "Mövzu",
        "İş",
        "İş adı",
    ],
    "description": [
        "Təsvir",
        "Açıqlama",
        "Description",
        "Qeyd",
        "Notes",
        "Note",
        "Comment",
        "Detail",
        "Details",
        "İzah",
        "Məlumat",
    ],
    "assignee": [
        "İcraçı",
        "Məsul şəxs",
        "Cavabdeh",
        "Assignee",
        "Assigned to",
        "Assigned",
        "Responsible",
        "Owner",
        "Sahib",
        "İcra edən",
        "Təyin edilən",
    ],
    "priority": [
        "Prioritet",
        "Priority",
        "Importance",
        "Önəm",
        "Önəmlilik",
        "Urgency",
    ],
    "operational_status": [
        "Status",
        "İcra statusu",
        "İcra vəziyyəti",
        "Operational status",
        "Task status",
        "Current status",
        "Vəziyyət",
    ],
    "deadline": [
        "Son tarix",
        "Deadline",
        "Due date",
        "Bitmə tarixi",
        "Cari son tarix",
        "End date",
        "Finish date",
    ],
    "start_date": [
        "Tarix",
        "Start date",
        "Begin date",
        "Başlama tarixi",
        "Başlanğıc tarixi",
        "Date",
    ],
    "category": [
        "Kateqoriya",
        "Category",
        "İstiqamət",
        "Direction",
        "Program",
        "Proqram",
        "Department",
        "Sector",
        "Sektor",
        "Şöbə",
        "Bölmə",
        "Team",
    ],
    "kanban": [
        "Kanban",
        "Kanban statusu",
        "Board",
        "Board status",
        "Lövhə",
        "Lövhə statusu",
    ],
    "external_id": [
        "ID",
        "Task ID",
        "External ID",
        "Xarici ID",
        "Tapşırıq ID",
        "Import ID",
    ],
}

GLOBAL_PRIORITY_MAP = {
    "high": "high",
    "yüksək": "high",
    "yuksek": "high",
    "critical": "high",
    "kritik": "high",
    "medium": "medium",
    "orta": "medium",
    "normal": "medium",
    "low": "low",
    "aşağı": "low",
    "asagi": "low",
}

GLOBAL_OPERATIONAL_STATUS_MAP = {
    "new": "new",
    "yeni": "new",
    "active": "active",
    "in progress": "active",
    "icradadır": "active",
    "icradadir": "active",
    "davam edir": "active",
    "gözləmədə": "waiting",
    "gozlemede": "waiting",
    "gözləmə": "waiting",
    "waiting": "waiting",
    "overdue": "overdue",
    "gecikmə": "overdue",
    "gecikme": "overdue",
    "gecikib": "overdue",
    "completed": "completed",
    "done": "completed",
    "tam icra": "completed",
    "tamamlandı": "completed",
    "tamamlanıb": "completed",
    "bitib": "completed",
    "within deadline": "within_deadline",
    "icra müddətində": "within_deadline",
    "vaxtında": "within_deadline",
}

GLOBAL_STATUS_FROM_OP_STATUS = {
    "new": "todo",
    "waiting": "todo",
    "within_deadline": "todo",
    "active": "in_progress",
    "overdue": "in_progress",
    "completed": "done",
}

GLOBAL_TASK_SHEET_ALIASES = [
    "Tasks",
    "Task",
    "Tapşırıqlar",
    "Tapşırıq",
    "İşlər",
]

GLOBAL_TITLE_RECOGNITION_LIST = (
    "Tapşırıq",
    "Başlıq",
    "Title",
    "Task",
    "Task Title",
    "Mövzu",
    "İş",
    "İş adı",
)
