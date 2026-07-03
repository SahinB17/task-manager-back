from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import models, schemas
from app.auth import get_current_user
from app.database import get_db


router = APIRouter(prefix="/options", tags=["Options"])


def _get_unique_field_values(db, model, field):
    """Return sorted unique non-empty values for a given model field."""
    values = (
        db.query(field)
        .filter(field.isnot(None), field != "")
        .distinct()
        .all()
    )
    return sorted(v for (v,) in values if v)


@router.get("/direction-programs")
def get_direction_programs(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Return sorted unique non-empty direction_program/category values from tasks and projects."""

    task_categories = _get_unique_field_values(
        db, models.Task, models.Task.category
    )
    task_directions = _get_unique_field_values(
        db, models.Task, models.Task.direction_program
    )
    project_directions = _get_unique_field_values(
        db, models.Project, models.Project.direction_program
    )

    all_directions = sorted(set(task_categories) | set(task_directions) | set(project_directions))
    return all_directions


@router.get("/task-types")
def get_task_types(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Return sorted unique non-empty task_type values from tasks."""
    return _get_unique_field_values(db, models.Task, models.Task.task_type)


@router.get("/priorities")
def get_priorities(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Return sorted unique non-empty priority values from tasks."""
    return _get_unique_field_values(db, models.Task, models.Task.priority)


@router.get("/frequencies")
def get_frequencies(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Return sorted unique non-empty frequency values from tasks."""
    return _get_unique_field_values(db, models.Task, models.Task.frequency)


@router.get("/risk-statuses")
def get_risk_statuses(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Return sorted unique non-empty risk_status values from tasks."""
    return _get_unique_field_values(db, models.Task, models.Task.risk_status)


# ═══════════════════════════════════════════════════════════════════════════
# Managed Category Options — Phase 6D
# ═══════════════════════════════════════════════════════════════════════════

def _normalize_cat_name(name):
    return " ".join((name or "").strip().split()).lower()


@router.get("/categories")
def list_categories(db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    """Return active managed categories + legacy task category/direction_program values."""
    from app import schemas as _s
    from app.models import CategoryOption
    from sqlalchemy import func as _fn

    managed = (
        db.query(CategoryOption)
        .filter(CategoryOption.is_active == True)  # noqa: E712
        .order_by(CategoryOption.sort_order, CategoryOption.name)
        .all()
    )

    # Collect legacy values from both category and direction_program
    managed_norms = {_normalize_cat_name(c.name) for c in managed}
    legacy_categories = _get_unique_field_values(db, models.Task, models.Task.category)
    legacy_directions = _get_unique_field_values(db, models.Task, models.Task.direction_program)
    legacy_values = sorted(set(legacy_categories) | set(legacy_directions))

    result = []
    for c in managed:
        result.append({"value": c.name, "label": c.name, "source": "managed"})
    for val in legacy_values:
        norm = _normalize_cat_name(val)
        if norm not in managed_norms:
            result.append({"value": val, "label": val, "source": "legacy"})

    return result


@router.post("/categories")
def create_category(
    body: schemas.CategoryOptionCreate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Create a new category (admin/manager only). Returns {value, label, source}."""
    from app.auth import is_admin_or_manager
    from app.models import CategoryOption
    from sqlalchemy import func as _fn

    if not is_admin_or_manager(current_user):
        raise HTTPException(status_code=403, detail="Yalnız admin/manager kateqoriya yarada bilər")

    trimmed = (body.name or "").strip()
    if not trimmed:
        raise HTTPException(status_code=400, detail="Kateqoriya adı boş ola bilməz")

    normalized = _normalize_cat_name(trimmed)
    existing = (
        db.query(CategoryOption)
        .filter(_fn.lower(CategoryOption.normalized_name) == normalized)
        .first()
    )
    if existing:
        if not existing.is_active:
            existing.is_active = True
            existing.name = trimmed
            existing.normalized_name = normalized
            db.commit()
        else:
            raise HTTPException(status_code=409, detail="Bu kateqoriya artıq mövcuddur")
    else:
        option = CategoryOption(name=trimmed, normalized_name=normalized, is_active=True, sort_order=0)
        db.add(option)
        db.commit()

    return {"value": trimmed, "label": trimmed, "source": "managed"}  # Return immediately usable shape


@router.put("/categories/{category_id}")
def deactivate_category(
    category_id: UUID,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Deactivate a category (admin/manager only)."""
    from app.auth import is_admin_or_manager
    from app.models import CategoryOption

    if not is_admin_or_manager(current_user):
        raise HTTPException(status_code=403, detail="Yalnız admin/manager kateqoriyanı dəyişə bilər")

    cat = db.query(CategoryOption).filter(CategoryOption.id == category_id).first()
    if not cat:
        raise HTTPException(status_code=404, detail="Kateqoriya tapılmadı")
    cat.is_active = False
    db.commit()
    return {"detail": "Kateqoriya deaktiv edildi"}


@router.put("/categories")
def upsert_category_by_name(
    body: schemas.CategoryOptionCreate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Upsert a category by name — used by Excel import and task-form inline add.
    If the category exists, reactivates it. If not, creates it.
    Returns {value, label, source}.
    """
    from app.auth import is_admin_or_manager
    from app.models import CategoryOption
    from sqlalchemy import func as _fn

    trimmed = (body.name or "").strip()
    if not trimmed:
        raise HTTPException(status_code=400, detail="Kateqoriya adı boş ola bilməz")

    normalized = _normalize_cat_name(trimmed)
    existing = (
        db.query(CategoryOption)
        .filter(_fn.lower(CategoryOption.normalized_name) == normalized)
        .first()
    )
    if existing:
        if not existing.is_active:
            existing.is_active = True
            existing.name = trimmed
            existing.normalized_name = normalized
        db.commit()
        return {"value": existing.name, "label": existing.name, "source": "managed"}

    # Auto-create for admin/manager only
    if not is_admin_or_manager(current_user):
        raise HTTPException(status_code=403, detail="Yalnız admin/manager kateqoriya yarada bilər")

    option = CategoryOption(name=trimmed, normalized_name=normalized, is_active=True, sort_order=0)
    db.add(option)
    db.commit()
    return {"value": trimmed, "label": trimmed, "source": "managed"}
