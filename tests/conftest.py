"""Los tests usan un PostgreSQL real (RLS, JSONB y arrays no se pueden probar con SQLite).

Base: CARGAS_TEST_DATABASE_URL (por defecto la base `cargas_test` local). El esquema se
recrea al inicio de la sesión con las migraciones de Alembic, igual que en producción. El rol
debe ser dueño de la base y NO superusuario (si no, RLS no se aplicaría y los tests de
aislamiento fallarían, como corresponde).
"""

import os
import uuid
from collections.abc import Iterator

import pytest

TEST_URL = os.environ.get(
    "CARGAS_TEST_DATABASE_URL", "postgresql+psycopg://cargas:cargas@localhost:5432/cargas_test"
)
os.environ["CARGAS_DATABASE_URL"] = TEST_URL
os.environ["CARGAS_ENVIRONMENT"] = "test"
os.environ["CARGAS_LLM_PROVIDER"] = "none"
os.environ["CARGAS_ROUTING_PROVIDER"] = "haversine"

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402

from cargas import cli  # noqa: E402
from cargas.config import get_settings  # noqa: E402
from cargas.db import get_engine  # noqa: E402


def _reset_schema() -> None:
    engine = create_engine(TEST_URL)
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    engine.dispose()


@pytest.fixture(scope="session", autouse=True)
def database() -> Iterator[None]:
    get_settings.cache_clear()
    get_engine.cache_clear()
    _reset_schema()
    cli.migrate()
    cli.seed_sources()
    cli.ingest()
    yield
    get_engine().dispose()


@pytest.fixture(scope="session")
def client() -> Iterator[TestClient]:
    from cargas.main import app

    with TestClient(app) as c:
        yield c


def signup(client: TestClient, name: str | None = None) -> dict[str, str]:
    suffix = uuid.uuid4().hex[:8]
    resp = client.post(
        "/api/companies",
        json={
            "company_name": name or f"Transportes {suffix}",
            "admin_name": "Admin Prueba",
            "admin_email": f"admin-{suffix}@example.com",
            "password": "secreto-123",
        },
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    return {"authorization": f"Bearer {data['token']}", "company_id": data["company_id"]}


def auth(headers: dict[str, str]) -> dict[str, str]:
    return {"authorization": headers["authorization"]}


@pytest.fixture
def company(client: TestClient) -> dict[str, str]:
    """Empresa nueva con un semi sider de 28 pallets y la configuración por defecto."""
    headers = signup(client)
    resp = client.post(
        "/api/trucks",
        headers=auth(headers),
        json={
            "plate": f"AB{uuid.uuid4().int % 1000:03d}CD",
            "vehicle_type": "semi",
            "body_type": "sider",
            "capacity_pallets": 28,
            "capacity_kg": 30000,
        },
    )
    assert resp.status_code == 201, resp.text
    return headers


class Chat:
    def __init__(self, client: TestClient, headers: dict[str, str]) -> None:
        self.client = client
        self.headers = auth(headers)
        self.conversation_id: str | None = None

    def say(self, text_: str) -> list[dict]:
        resp = self.client.post(
            "/api/chat/messages",
            headers=self.headers,
            json={"text": text_, "conversation_id": self.conversation_id},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        self.conversation_id = data["conversation_id"]
        return data["replies"]


@pytest.fixture
def chat(client: TestClient, company: dict[str, str]) -> Chat:
    return Chat(client, company)
