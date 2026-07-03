# Task Manager Backend

FastAPI ilə yazılmış lokal task management backend layihəsi.

Bu backend fərqli komandalar və şirkətlər üçün task, müştəri, istifadəçi, comment, fayl attachment, activity log və dashboard statistikalarını idarə etmək üçündür. Layihənin əsas istifadəsi lokal development və lokal demo üçündür.

---

## Features

- JWT authentication
- Role-based permissions
- User management
- Client management
- Task management
- Task comments
- Task file attachments
- Activity logs
- Dashboard statistics
- PostgreSQL database
- File upload and download
- Status, priority and task type validation
- Local frontend üçün CORS ayarları

---

## User Roles

Role-lar `role_id` ilə işləyir:

```text
1 = Admin
2 = Manager
3 = Employee
```

### Admin / Manager

Admin və manager istifadəçiləri bunları edə bilir:

- User yaratmaq, görmək, update etmək, silmək
- Client yaratmaq, görmək, update etmək, silmək
- Task yaratmaq, bütün taskları görmək, update etmək, silmək
- Activity logs görmək
- Dashboard statistics görmək
- Task faylları upload/download/delete etmək

Qeyd: Manager yeni admin/manager yaratmaq üçün deyil, əsasən employee yaratmaq üçündür.

### Employee

Employee istifadəçiləri bunları edə bilir:

- Yalnız özünə assigned edilən taskları görmək
- Yalnız task statusunu update etmək
- Özünə assigned tasklara comment yazmaq
- Öz commentlərini update/delete etmək
- Özünə assigned tasklara fayl yükləmək
- Özünə assigned taskların fayllarını görmək/download etmək

Employee bunları edə bilmir:

- User yaratmaq və bütün user-ları görmək
- Client yaratmaq/update/delete etmək
- Task yaratmaq/delete etmək
- Activity logs görmək

---

## Technologies Used

- Python
- FastAPI
- PostgreSQL
- SQLAlchemy
- Pydantic
- JWT Authentication
- Passlib / bcrypt
- Uvicorn

---

## Project Structure

```text
task-manager-backend/
|-- app/
|   |-- routers/
|   |   |-- auth.py
|   |   |-- users.py
|   |   |-- clients.py
|   |   |-- tasks.py
|   |   |-- logs.py
|   |   |-- dashboard.py
|   |   `-- attachments.py
|   |-- auth.py
|   |-- database.py
|   |-- main.py
|   |-- models.py
|   |-- schemas.py
|   `-- utils.py
|-- uploads/
|-- .env.example
|-- .gitignore
|-- requirements.txt
`-- README.md
```

---

## Local Setup

### 1. Virtual environment yarat

```bash
python -m venv venv
```

### 2. Windows-da aktiv et

```bash
venv\Scripts\activate
```

### 3. Dependencies install et

```bash
pip install -r requirements.txt
```

### 4. PostgreSQL database yarat

Lokal PostgreSQL-de database yarad:

```text
task_manager_db
```

Database adını dəyişsən, `.env` içindəki `DATABASE_URL`-i də uyğun yenilə.

---

## Database Schema

Alembic database schema creation və migration üçün source of truth-dur.

Existing DB statusunu yoxla:

```bash
python -m alembic current
```

Fresh DB üçün migration-ları işə sal:

```bash
python -m alembic upgrade head
```

Existing pre-Alembic DB üçün schema uyğundursa baseline-i stamp et:

```bash
python -m alembic stamp head
```

---

## Environment Variables

Project root-da `.env` faylı yarat. `.env.example`-i əsas götürə bilərsən:

```env
DATABASE_URL=postgresql://postgres:YOUR_PASSWORD@localhost:5432/task_manager_db
SECRET_KEY=change_this_to_a_long_random_secret
ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=60
UPLOAD_DIR=uploads
MAX_UPLOAD_FILE_SIZE_BYTES=10485760
BACKEND_CORS_ORIGINS=http://localhost:5173,http://127.0.0.1:5173
ENABLE_LOCAL_SEED=false
INITIAL_ADMIN_EMAIL=admin@example.com
INITIAL_ADMIN_PASSWORD=admin123
INITIAL_ADMIN_FULL_NAME=Local Admin
```

Qeydlər:

- `YOUR_PASSWORD` yerində lokal PostgreSQL password yazılmalıdır.
- `SECRET_KEY` lokal üçün istədiyin uzun random string ola bilər.
- `UPLOAD_DIR=uploads` yüklənən faylları project içindəki `uploads/` folderində saxlayır.
- `MAX_UPLOAD_FILE_SIZE_BYTES` attachment upload üçün maksimum fayl ölçüsüdür. Default `10485760` byte, yəni 10 MB-dır.
- `BACKEND_CORS_ORIGINS` vergüllə ayrılmış icazəli frontend origin-ləridir. Lokal üçün `http://localhost:5173` və `http://127.0.0.1:5173` yazıla bilər.
- Production üçün `BACKEND_CORS_ORIGINS` yalnız real frontend domain-lərini saxlamalıdır, məsələn `https://app.example.com`.
- `allow_credentials=True` olduğu üçün CORS origin kimi `*` istifadə etməyin.
- `ENABLE_LOCAL_SEED=true` yalnız lokal/demo bootstrap üçündür. Production üçün `false` saxlanmalıdır.
- `INITIAL_ADMIN_EMAIL` və `INITIAL_ADMIN_PASSWORD` yalnız `ENABLE_LOCAL_SEED=true` olduqda lokal bootstrap üçün istifadə edilməlidir.

---

## Run Locally

```bash
uvicorn app.main:app --reload
```

Backend default olaraq burada açılır:

```text
http://127.0.0.1:8000
```

Swagger documentation:

```text
http://127.0.0.1:8000/docs
```

---

## Tests

Backend testlərini bu komanda ilə işə sal:

```bash
python -m pytest
```

Virtual environment istifadə olunursa, əvvəl onu aktiv et. Testlər ayrıca Postgres schema-da işləyir və development cədvəllərinə toxunmur.

---

## Login Flow

Login endpoint:

```text
POST /auth/login
```

Bu endpoint `OAuth2PasswordRequestForm` istifadə edir. Swagger-də login zamanı:

```text
username = user email
password = user password
```

Token alandan sonra protected endpoint-lər üçün Swagger-də `Authorize` buttonundan istifadə et.

---

## Main API Endpoints

### Auth

```text
POST /auth/login
GET  /auth/me
```

### Users

```text
GET    /users/
POST   /users/
GET    /users/{user_id}
PUT    /users/{user_id}
DELETE /users/{user_id}
```

### Clients

```text
GET    /clients/
POST   /clients/
GET    /clients/{client_id}
PUT    /clients/{client_id}
DELETE /clients/{client_id}
GET    /clients/{client_id}/tasks
```

### Tasks

```text
GET    /tasks/
GET    /tasks/my
POST   /tasks/
GET    /tasks/{task_id}
PUT    /tasks/{task_id}
DELETE /tasks/{task_id}
```

### Task Comments

```text
POST   /tasks/{task_id}/comments
GET    /tasks/{task_id}/comments
PUT    /tasks/comments/{comment_id}
DELETE /tasks/comments/{comment_id}
```

### Task Attachments

```text
POST   /tasks/{task_id}/attachments
GET    /tasks/{task_id}/attachments
GET    /attachments/{attachment_id}/download
DELETE /attachments/{attachment_id}
```

### Activity Logs

```text
GET /logs/
```

### Dashboard

```text
GET /dashboard/stats
```

---

## Task Values

Allowed task statuses:

```text
todo
in_progress
done
```

Allowed task priorities:

```text
low
medium
high
```

Allowed task types:

```text
general
tax_report
audit_check
salary_calculation
invoice_review
contract_review
bank_reconciliation
financial_statement
```

---

## File Upload Rules

Allowed file types:

```text
PDF
Word
Excel
PNG
JPG
JPEG
TXT
```

Maximum file size:

```text
10 MB
```

Uploaded files are saved under:

```text
uploads/tasks/{task_id}/
```

---

## Activity Logs

System important actions üçün activity log saxlayır.

Examples:

```text
TASK_CREATED
TASK_UPDATED
TASK_STATUS_UPDATED
TASK_DELETED

CLIENT_CREATED
CLIENT_UPDATED
CLIENT_DELETED

USER_CREATED
USER_UPDATED
USER_DELETED

FILE_UPLOADED
FILE_DELETED
```

---

## Example Task Create Request

```json
{
  "title": "Monthly tax report",
  "description": "Prepare monthly tax report for the client.",
  "task_type": "tax_report",
  "status": "todo",
  "priority": "high",
  "deadline": "2026-06-25T18:00:00",
  "client_id": "client_uuid_here",
  "assigned_to": "user_uuid_here"
}
```

---

## Local Notes

- Bu README lokal development üçün yazılıb.
- Fresh DB schema üçün `python -m alembic upgrade head` işlədin.
- Mövcud pre-Alembic DB schema artıq uyğundursa `python -m alembic stamp head` işlədin.
- App start zamanı yalnız sistem rolları yoxlanır; lokal/demo bootstrap isə yalnız `ENABLE_LOCAL_SEED=true` olduqda işləyir.
- `.env` içində initial admin məlumatları production üçün default aktiv edilməməlidir.
- Frontend origin-ləri artıq koddan avtomatik əlavə edilmir. Lokal `.env` içində `BACKEND_CORS_ORIGINS=http://localhost:5173,http://127.0.0.1:5173` saxlayın.
- Production-da frontend domain-i `BACKEND_CORS_ORIGINS`-ə açıq yazılmalıdır; credentials aktiv olduğu üçün `*` istifadə edilməməlidir.

---
