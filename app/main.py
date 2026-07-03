from fastapi import FastAPI
from .database import SessionLocal
from . import models
from .routers import clients, users, tasks, projects, project_stages, auth, logs, dashboard, attachments, excel, ai_assistant_v2, options, notifications
from .seed import seed_local_data, seed_roles
from fastapi.middleware.cors import CORSMiddleware
import os

app = FastAPI(
    title="Task Manager API",
    version="1.0.0"
)

def parse_cors_origins(value: str | None) -> list[str]:
    origins = [origin.strip() for origin in (value or "").split(",") if origin.strip()]
    unique_origins = list(dict.fromkeys(origins))

    if "*" in unique_origins:
        raise RuntimeError("Wildcard CORS origin is not allowed when credentials are enabled.")

    return unique_origins


configured_cors_origins = parse_cors_origins(
    os.getenv("BACKEND_CORS_ORIGINS") or os.getenv("CORS_ORIGINS")
)


def env_flag_enabled(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}

app.add_middleware(
    CORSMiddleware,
    allow_origins=configured_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

db = SessionLocal()
try:
    seed_roles(db)
    db.commit()

    if env_flag_enabled("ENABLE_LOCAL_SEED"):
        seed_local_data(db)
finally:
    db.close()

app.include_router(clients.router)
app.include_router(users.router)
app.include_router(tasks.router)
app.include_router(projects.router)
app.include_router(project_stages.router)
app.include_router(auth.router)
app.include_router(logs.router)
app.include_router(dashboard.router)
app.include_router(attachments.router)
app.include_router(excel.router)
app.include_router(ai_assistant_v2.router)
app.include_router(ai_assistant_v2.conversations_router)
app.include_router(options.router)
app.include_router(notifications.router)


@app.get("/")
def root():
    return {
        "message": "Task Manager API is running"
    }
