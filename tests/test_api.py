"""Integration tests for FastAPI endpoints using an in-memory SQLite database."""

from __future__ import annotations

import io
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base, get_db
from app.main import app
from app.models import Invoice, InvoiceStatus


# ── Test database setup ───────────────────────────────────────────────────────

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"


@pytest_asyncio.fixture
async def test_db():
    engine = create_async_engine(TEST_DB_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async def override_get_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db

    yield session_factory

    app.dependency_overrides.clear()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.fixture
def client(test_db):
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


# ── Tests ─────────────────────────────────────────────────────────────────────


class TestHealthEndpoint:
    def test_health_returns_ok(self, client: TestClient) -> None:
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_health_includes_service_name(self, client: TestClient) -> None:
        resp = client.get("/health")
        assert "invoice-processing" in resp.json()["service"]


class TestListInvoices:
    def test_empty_list(self, client: TestClient) -> None:
        resp = client.get("/invoices")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_pagination_params_accepted(self, client: TestClient) -> None:
        resp = client.get("/invoices?page=1&page_size=10")
        assert resp.status_code == 200

    def test_invalid_page_rejected(self, client: TestClient) -> None:
        resp = client.get("/invoices?page=0")
        assert resp.status_code == 422

    def test_status_filter_accepted(self, client: TestClient) -> None:
        resp = client.get("/invoices?status=pending")
        assert resp.status_code == 200


class TestGetInvoice:
    def test_404_for_missing_invoice(self, client: TestClient) -> None:
        resp = client.get("/invoices/9999")
        assert resp.status_code == 404

    def test_returns_invoice_by_id(self, client: TestClient, test_db) -> None:
        import asyncio

        async def seed():
            async with test_db() as session:
                inv = Invoice(filename="test.pdf", status=InvoiceStatus.pending)
                session.add(inv)
                await session.commit()
                await session.refresh(inv)
                return inv.id

        invoice_id = asyncio.get_event_loop().run_until_complete(seed())
        resp = client.get(f"/invoices/{invoice_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == invoice_id
        assert data["filename"] == "test.pdf"
        assert data["status"] == "pending"


class TestInvoiceStats:
    def test_stats_empty_db(self, client: TestClient) -> None:
        resp = client.get("/invoices/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["success_rate"] == 0.0
        assert isinstance(data["by_status"], dict)

    def test_stats_counts_correctly(self, client: TestClient, test_db) -> None:
        import asyncio

        async def seed():
            async with test_db() as session:
                session.add(Invoice(filename="a.pdf", status=InvoiceStatus.completed))
                session.add(Invoice(filename="b.pdf", status=InvoiceStatus.completed))
                session.add(Invoice(filename="c.pdf", status=InvoiceStatus.failed))
                await session.commit()

        asyncio.get_event_loop().run_until_complete(seed())
        resp = client.get("/invoices/stats")
        data = resp.json()
        assert data["total"] == 3
        assert data["by_status"]["completed"] == 2
        assert data["by_status"]["failed"] == 1
        assert data["success_rate"] == pytest.approx(66.67, rel=0.01)


class TestProcessInvoiceUpload:
    def test_rejects_non_pdf(self, client: TestClient) -> None:
        resp = client.post(
            "/invoices/process",
            files={"file": ("test.txt", b"not a pdf", "text/plain")},
        )
        assert resp.status_code == 400

    @patch("app.main.process_invoice_task")
    def test_accepts_pdf_and_dispatches(
        self, mock_task: MagicMock, client: TestClient, tmp_path, mock_settings
    ) -> None:
        mock_task.apply_async = MagicMock()

        with patch("app.main.settings") as mock_s:
            mock_s.watch_folder_path = tmp_path

            resp = client.post(
                "/invoices/process",
                files={"file": ("invoice.pdf", b"%PDF-1.4 test content", "application/pdf")},
            )

        # 202 Accepted — task is queued, record created
        assert resp.status_code == 202
        data = resp.json()
        assert data["filename"] == "invoice.pdf"
        assert data["status"] == "pending"


class TestRetryInvoice:
    def test_404_for_missing_invoice(self, client: TestClient) -> None:
        resp = client.post("/invoices/9999/retry")
        assert resp.status_code == 404

    def test_rejects_retry_on_completed_invoice(self, client: TestClient, test_db) -> None:
        import asyncio

        async def seed():
            async with test_db() as session:
                inv = Invoice(filename="done.pdf", status=InvoiceStatus.completed)
                session.add(inv)
                await session.commit()
                await session.refresh(inv)
                return inv.id

        inv_id = asyncio.get_event_loop().run_until_complete(seed())
        resp = client.post(f"/invoices/{inv_id}/retry")
        assert resp.status_code == 400

    @patch("app.main.process_invoice_task")
    def test_retries_failed_invoice(self, mock_task: MagicMock, client: TestClient, test_db) -> None:
        import asyncio

        mock_task.apply_async = MagicMock()

        async def seed():
            async with test_db() as session:
                inv = Invoice(
                    filename="fail.pdf",
                    status=InvoiceStatus.failed,
                    retry_count=1,
                    error_message="timeout",
                )
                session.add(inv)
                await session.commit()
                await session.refresh(inv)
                return inv.id

        inv_id = asyncio.get_event_loop().run_until_complete(seed())
        resp = client.post(f"/invoices/{inv_id}/retry")
        assert resp.status_code == 200
        assert resp.json()["status"] == "pending"


class TestLogs:
    def test_empty_logs(self, client: TestClient) -> None:
        resp = client.get("/logs")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_limit_param_accepted(self, client: TestClient) -> None:
        resp = client.get("/logs?limit=50")
        assert resp.status_code == 200
