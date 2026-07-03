import os
import re
import uuid
from pathlib import Path

from fastapi import HTTPException, UploadFile


ALLOWED_FILE_TYPES = {
    ".pdf": {"application/pdf"},
    ".doc": {"application/msword"},
    ".docx": {"application/vnd.openxmlformats-officedocument.wordprocessingml.document"},
    ".xls": {"application/vnd.ms-excel"},
    ".xlsx": {"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"},
    ".png": {"image/png"},
    ".jpg": {"image/jpeg"},
    ".jpeg": {"image/jpeg"},
    ".txt": {"text/plain"},
}

DEFAULT_MAX_UPLOAD_FILE_SIZE_BYTES = 10 * 1024 * 1024
PROJECT_ROOT = Path(__file__).resolve().parents[1]
UPLOAD_DIR_SETTING = os.getenv("UPLOAD_DIR", "uploads")
UPLOAD_ROOT = Path(UPLOAD_DIR_SETTING)
if not UPLOAD_ROOT.is_absolute():
    UPLOAD_ROOT = PROJECT_ROOT / UPLOAD_ROOT
UPLOAD_ROOT = UPLOAD_ROOT.resolve()
try:
    UPLOAD_DB_PREFIX = UPLOAD_ROOT.relative_to(PROJECT_ROOT)
except ValueError:
    UPLOAD_DB_PREFIX = Path("")


def get_max_upload_file_size() -> int:
    raw_value = os.getenv("MAX_UPLOAD_FILE_SIZE_BYTES")
    if not raw_value:
        return DEFAULT_MAX_UPLOAD_FILE_SIZE_BYTES

    try:
        parsed_value = int(raw_value)
    except ValueError as exc:
        raise RuntimeError("MAX_UPLOAD_FILE_SIZE_BYTES must be an integer.") from exc

    if parsed_value <= 0:
        raise RuntimeError("MAX_UPLOAD_FILE_SIZE_BYTES must be greater than zero.")

    return parsed_value


MAX_UPLOAD_FILE_SIZE_BYTES = get_max_upload_file_size()


def sanitize_filename(filename: str) -> str:
    normalized_name = (filename or "file").replace("\\", "/")
    safe_name = Path(normalized_name).name
    safe_name = re.sub(r"[\x00-\x1f\x7f]", "", safe_name)
    safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", safe_name).strip("._")
    return safe_name or "file"


def is_path_inside(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def resolve_attachment_path(stored_path: str) -> Path:
    file_path = Path(stored_path or "")

    if not stored_path or file_path.is_absolute() or ".." in file_path.parts:
        raise HTTPException(status_code=400, detail="Fayl yolu təhlükəsiz deyil")

    resolved_path = (PROJECT_ROOT / file_path).resolve()

    if is_path_inside(resolved_path, UPLOAD_ROOT):
        return resolved_path

    upload_relative_path = (UPLOAD_ROOT / file_path).resolve()

    if is_path_inside(upload_relative_path, UPLOAD_ROOT):
        return upload_relative_path

    raise HTTPException(status_code=400, detail="Fayl yolu təhlükəsiz deyil")


def build_task_upload_dir(task_id) -> Path:
    task_upload_dir = (UPLOAD_ROOT / "tasks" / str(task_id)).resolve()

    if not is_path_inside(task_upload_dir, UPLOAD_ROOT):
        raise HTTPException(status_code=400, detail="Fayl yolu təhlükəsiz deyil")

    task_upload_dir.mkdir(parents=True, exist_ok=True)
    return task_upload_dir


def build_unique_upload_path(upload_dir: Path, safe_file_name: str) -> tuple[str, Path]:
    for _ in range(10):
        unique_file_name = f"{uuid.uuid4().hex}_{safe_file_name}"
        file_location = (upload_dir / unique_file_name).resolve()

        if not is_path_inside(file_location, UPLOAD_ROOT):
            raise HTTPException(status_code=400, detail="Fayl yolu təhlükəsiz deyil")

        if not file_location.exists():
            return unique_file_name, file_location

    raise HTTPException(status_code=500, detail="Fayl üçün unikal ad yaradıla bilmədi")


def validate_file_signature(extension: str, header: bytes):
    if extension == ".pdf" and not header.startswith(b"%PDF-"):
        raise HTTPException(status_code=400, detail="Fayl məzmunu PDF formatına uyğun deyil")

    if extension in {".doc", ".xls"} and not header.startswith(
        b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
    ):
        raise HTTPException(status_code=400, detail="Fayl məzmunu Office formatına uyğun deyil")

    if extension in {".docx", ".xlsx"} and not header.startswith(
        (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")
    ):
        raise HTTPException(status_code=400, detail="Fayl məzmunu Office formatına uyğun deyil")

    if extension == ".png" and not header.startswith(b"\x89PNG\r\n\x1a\n"):
        raise HTTPException(status_code=400, detail="Fayl məzmunu PNG formatına uyğun deyil")

    if extension in {".jpg", ".jpeg"} and not header.startswith(b"\xff\xd8\xff"):
        raise HTTPException(status_code=400, detail="Fayl məzmunu JPEG formatına uyğun deyil")

    if extension == ".txt" and b"\x00" in header:
        raise HTTPException(status_code=400, detail="TXT fayl məzmunu təhlükəsiz deyil")


def validate_upload_file(file: UploadFile) -> str:
    safe_name = sanitize_filename(file.filename)
    extension = Path(safe_name).suffix.lower()
    allowed_mime_types = ALLOWED_FILE_TYPES.get(extension)

    if not allowed_mime_types or file.content_type not in allowed_mime_types:
        raise HTTPException(
            status_code=400,
            detail="Fayl tipi icazəli deyil. İcazəli tiplər: PDF, Word, Excel, PNG, JPG, JPEG, TXT",
        )

    file.file.seek(0, os.SEEK_END)
    file_size = file.file.tell()
    file.file.seek(0)

    if file_size > MAX_UPLOAD_FILE_SIZE_BYTES:
        max_size_mb = MAX_UPLOAD_FILE_SIZE_BYTES / (1024 * 1024)
        raise HTTPException(
            status_code=400,
            detail=f"Fayl ölçüsü çox böyükdür. Maksimum icazəli ölçü {max_size_mb:g} MB-dır",
        )

    header = file.file.read(16)
    file.file.seek(0)
    validate_file_signature(extension, header)

    return safe_name
