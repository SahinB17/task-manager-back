import importlib
import os
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from dotenv import load_dotenv
from fastapi.testclient import TestClient
from passlib.context import CryptContext
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


def _build_schema_database_url(base_url: str, schema_name: str) -> str:
    url = make_url(base_url)
    query = dict(url.query)
    search_path_option = f"-csearch_path={schema_name}"
    existing_options = query.get("options")
    query["options"] = (
        f"{existing_options} {search_path_option}"
        if existing_options
        else search_path_option
    )
    return url.set(query=query).render_as_string(hide_password=False)


def _clear_app_modules():
    module_names = [
        name
        for name in list(sys.modules)
        if name == "app" or name.startswith("app.")
    ]
    for module_name in module_names:
        sys.modules.pop(module_name, None)


def _upgrade_test_schema():
    database_url = os.environ.get("DATABASE_URL")
    if database_url:
        os.environ["DATABASE_URL"] = database_url.replace("%", "%%")
    alembic_cfg = Config(str(BACKEND_ROOT / "alembic.ini"))
    try:
        command.upgrade(alembic_cfg, "head")
    finally:
        if database_url:
            os.environ["DATABASE_URL"] = database_url


@pytest.fixture(scope="session")
def base_database_url():
    load_dotenv(BACKEND_ROOT / ".env")
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("Tests üçün DATABASE_URL təyin edilməlidir.")
    return database_url


@pytest.fixture(scope="session")
def test_schema_name():
    return f"test_{uuid4().hex}"


@pytest.fixture(scope="session")
def app_instance(base_database_url, test_schema_name):
    admin_engine = create_engine(base_database_url)
    with admin_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{test_schema_name}"'))

    original_database_url = os.environ.get("DATABASE_URL")
    original_openai_api_key = os.environ.get("OPENAI_API_KEY")
    original_initial_admin_email = os.environ.get("INITIAL_ADMIN_EMAIL")
    original_initial_admin_password = os.environ.get("INITIAL_ADMIN_PASSWORD")
    original_initial_admin_full_name = os.environ.get("INITIAL_ADMIN_FULL_NAME")
    original_ai_read_model = os.environ.get("AI_READ_MODEL")
    os.environ["DATABASE_URL"] = _build_schema_database_url(
        base_database_url,
        test_schema_name,
    )
    os.environ.setdefault("OPENAI_API_KEY", "test-openai-key")
    os.environ["INITIAL_ADMIN_EMAIL"] = ""
    os.environ["INITIAL_ADMIN_PASSWORD"] = ""
    os.environ["INITIAL_ADMIN_FULL_NAME"] = ""
    os.environ["AI_READ_MODEL"] = "gpt-5.4-mini"

    _upgrade_test_schema()
    _clear_app_modules()
    app_main = importlib.import_module("app.main")

    try:
        yield app_main.app
    finally:
        _clear_app_modules()
        if original_database_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = original_database_url

        if original_openai_api_key is None:
            os.environ.pop("OPENAI_API_KEY", None)
        else:
            os.environ["OPENAI_API_KEY"] = original_openai_api_key

        if original_initial_admin_email is None:
            os.environ.pop("INITIAL_ADMIN_EMAIL", None)
        else:
            os.environ["INITIAL_ADMIN_EMAIL"] = original_initial_admin_email

        if original_initial_admin_password is None:
            os.environ.pop("INITIAL_ADMIN_PASSWORD", None)
        else:
            os.environ["INITIAL_ADMIN_PASSWORD"] = original_initial_admin_password

        if original_initial_admin_full_name is None:
            os.environ.pop("INITIAL_ADMIN_FULL_NAME", None)
        else:
            os.environ["INITIAL_ADMIN_FULL_NAME"] = original_initial_admin_full_name

        if original_ai_read_model is None:
            os.environ.pop("AI_READ_MODEL", None)
        else:
            os.environ["AI_READ_MODEL"] = original_ai_read_model

        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{test_schema_name}" CASCADE'))
        admin_engine.dispose()


@pytest.fixture(scope="session")
def app_modules(app_instance):
    auth_module = importlib.import_module("app.auth")
    auth_module.pwd_context = CryptContext(
        schemes=["pbkdf2_sha256"],
        deprecated="auto",
    )

    return {
        "app": importlib.import_module("app"),
        "auth": auth_module,
        "database": importlib.import_module("app.database"),
        "models": importlib.import_module("app.models"),
        "seed": importlib.import_module("app.seed"),
    }


@pytest.fixture(autouse=True)
def reset_database(app_modules):
    database = app_modules["database"]
    seed = app_modules["seed"]
    session = database.SessionLocal()

    try:
        table_names = [
            table.name
            for table in reversed(database.Base.metadata.sorted_tables)
        ]
        joined_table_names = ", ".join(table_names)
        session.execute(
            text(f"TRUNCATE TABLE {joined_table_names} RESTART IDENTITY CASCADE")
        )
        session.commit()
        seed.seed_roles(session)
        session.commit()
    finally:
        session.close()

    yield


@pytest.fixture
def client(app_instance):
    with TestClient(app_instance) as test_client:
        yield test_client


@pytest.fixture
def db_session(app_modules):
    session = app_modules["database"].SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def factories(app_modules, db_session):
    auth = app_modules["auth"]
    models = app_modules["models"]

    def create_user(*, full_name, email, role_id, password="password123", is_active=True):
        user = models.User(
            full_name=full_name,
            email=email,
            password_hash=auth.hash_password(password),
            role_id=role_id,
            is_active=is_active,
        )
        db_session.add(user)
        db_session.commit()
        db_session.refresh(user)
        return user

    def create_client(*, company_name, contact_person="Test Contact", email=None):
        client_record = models.Client(
            company_name=company_name,
            contact_person=contact_person,
            email=email,
        )
        db_session.add(client_record)
        db_session.commit()
        db_session.refresh(client_record)
        return client_record

    def create_task(
        *,
        title,
        client_id,
        created_by,
        assigned_to=None,
        status="todo",
        priority="medium",
    ):
        task = models.Task(
            title=title,
            client_id=client_id,
            created_by=created_by,
            assigned_to=assigned_to,
            status=status,
            priority=priority,
        )
        db_session.add(task)
        db_session.commit()
        db_session.refresh(task)
        return task

    return {
        "create_user": create_user,
        "create_client": create_client,
        "create_task": create_task,
    }


@pytest.fixture
def auth_headers(client):
    def build(email: str, password: str):
        response = client.post(
            "/auth/login",
            data={"username": email, "password": password},
        )
        assert response.status_code == 200, response.text
        token = response.json()["access_token"]
        return {"Authorization": f"Bearer {token}"}

    return build
