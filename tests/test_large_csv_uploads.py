from __future__ import annotations

import asyncio
import importlib
import re

import httpx

import pytest
from fastapi.testclient import TestClient

CSV = b"""latitude,longitude,acq_date,acq_time,satellite,instrument,confidence,frp,version
30.1,75.1,2025-11-03,0930,Terra,MODIS,82,11,6.1
30.2,75.2,2025-11-04,1020,Aqua,MODIS,73,9,6.1
"""


@pytest.fixture
def app_client(tmp_path, monkeypatch):
    monkeypatch.delenv("VERCEL", raising=False)
    monkeypatch.setenv("EMBERLENS_ENV", "development")
    monkeypatch.setenv("EMBERLENS_DB", str(tmp_path / "large-upload.sqlite"))
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    monkeypatch.delenv("SUPABASE_DB_URL", raising=False)
    from backend import main
    importlib.reload(main)
    with TestClient(main.app) as client:
        yield main, client
    importlib.reload(main)


def test_upload_ticket_uses_private_signed_storage_url(app_client, monkeypatch):
    main, client = app_client
    monkeypatch.setattr(main, "SUPABASE_URL", "https://example-project.supabase.co")
    monkeypatch.setattr(main, "SUPABASE_SERVICE_ROLE_KEY", "server-only-test-key")

    async def fake_signed_url(path):
        assert re.fullmatch(r"pending/[0-9a-f]{32}\.csv", path)
        return {
            "upload_url": "https://example-project.supabase.co/storage/v1/object/upload/sign/private?token=one-time",
            "upload_token": "one-time",
            "tus_endpoint": "https://example-project.storage.supabase.co/storage/v1/upload/resumable/sign",
        }

    monkeypatch.setattr(main, "create_supabase_signed_upload_url", fake_signed_url)
    response = client.post("/api/import/csv/upload-url", json={
        "source": "MODIS_SP", "filename": "modis-export.csv", "size": 12_000_000,
    })
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["upload_url"].startswith("https://example-project.supabase.co/")
    assert body["max_bytes"] == 50_000_000
    assert body["expires_in_seconds"] == 7200
    assert body["upload_token"] == "one-time"
    assert body["bucket"] == "emberlens-csv-imports"
    assert body["tus_endpoint"] == "https://example-project.storage.supabase.co/storage/v1/upload/resumable/sign"
    assert "service_role" not in body
    assert re.fullmatch(r"pending/[0-9a-f]{32}\.csv", body["path"])



def test_signed_url_helper_uses_server_credentials_and_storage_api(app_client, monkeypatch):
    main, _ = app_client
    monkeypatch.setattr(main, "SUPABASE_URL", "https://example-project.supabase.co")
    monkeypatch.setattr(main, "SUPABASE_SERVICE_ROLE_KEY", "server-only-test-key")
    path = "pending/0123456789abcdef0123456789abcdef.csv"
    calls = []

    def handler(request):
        calls.append(request)
        assert request.method == "POST"
        assert request.headers["apikey"] == "server-only-test-key"
        assert request.headers["authorization"] == "Bearer server-only-test-key"
        assert request.url.path.endswith("/emberlens-csv-imports/" + path)
        return httpx.Response(200, json={
            "url": "/object/upload/sign/emberlens-csv-imports/" + path + "?token=temporary",
        })

    real_async_client = main.httpx.AsyncClient
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(main.httpx, "AsyncClient", lambda *args, **kwargs: real_async_client(
        *args, transport=transport, **kwargs
    ))
    signed = asyncio.run(main.create_supabase_signed_upload_url(path))
    assert signed["upload_url"] == (
        "https://example-project.supabase.co/storage/v1/object/upload/sign/"
        "emberlens-csv-imports/" + path + "?token=temporary"
    )
    assert signed["upload_token"] == "temporary"
    assert signed["tus_endpoint"] == "https://example-project.storage.supabase.co/storage/v1/upload/resumable/sign"
    assert len(calls) == 1

def test_storage_download_and_cleanup_use_private_object_endpoints(app_client, monkeypatch):
    main, _ = app_client
    monkeypatch.setattr(main, "SUPABASE_URL", "https://example-project.supabase.co")
    monkeypatch.setattr(main, "SUPABASE_SERVICE_ROLE_KEY", "server-only-test-key")
    path = "pending/0123456789abcdef0123456789abcdef.csv"
    seen = []

    def handler(request):
        seen.append(request)
        assert request.headers["authorization"] == "Bearer server-only-test-key"
        if request.method == "GET":
            assert "/object/authenticated/emberlens-csv-imports/" in request.url.path
            return httpx.Response(200, content=CSV)
        assert request.method == "DELETE"
        assert request.url.path.endswith("/object/emberlens-csv-imports")
        assert request.read() == b'{"prefixes":["' + path.encode() + b'"]}'
        return httpx.Response(200, json={"message": "deleted"})

    real_async_client = main.httpx.AsyncClient
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(main.httpx, "AsyncClient", lambda *args, **kwargs: real_async_client(
        *args, transport=transport, **kwargs
    ))
    assert asyncio.run(main.download_supabase_csv(path)) == CSV
    asyncio.run(main.delete_supabase_csv(path))
    assert [request.method for request in seen] == ["GET", "DELETE"]


def test_upload_ticket_rejects_oversized_or_non_csv_files(app_client):
    _, client = app_client
    too_large = client.post("/api/import/csv/upload-url", json={
        "source": "MODIS_SP", "filename": "too-large.csv", "size": 50_000_001,
    })
    assert too_large.status_code == 413
    wrong_extension = client.post("/api/import/csv/upload-url", json={
        "source": "MODIS_SP", "filename": "archive.zip", "size": 100,
    })
    assert wrong_extension.status_code == 422
    wrong_source = client.post("/api/import/csv/upload-url", json={
        "source": "MODIS", "filename": "modis.csv", "size": 100,
    })
    assert wrong_source.status_code == 422


def test_upload_ticket_fails_closed_without_storage_credentials(app_client):
    _, client = app_client
    response = client.post("/api/import/csv/upload-url", json={
        "source": "MODIS_SP", "filename": "modis.csv", "size": 100,
    })
    assert response.status_code == 503
    assert "SUPABASE_URL" in response.json()["detail"]


def test_complete_upload_imports_and_cleans_up_private_object(app_client, monkeypatch):
    main, client = app_client
    deleted = []

    async def fake_download(path):
        assert re.fullmatch(r"pending/[0-9a-f]{32}\.csv", path)
        return CSV

    async def fake_delete(path):
        deleted.append(path)

    monkeypatch.setattr(main, "download_supabase_csv", fake_download)
    monkeypatch.setattr(main, "delete_supabase_csv", fake_delete)
    path = "pending/0123456789abcdef0123456789abcdef.csv"
    response = client.post("/api/import/csv/complete", json={
        "source": "MODIS_SP", "filename": "modis-export.csv", "path": path,
    })
    assert response.status_code == 200, response.text
    assert response.json()["rows_added"] == 2
    assert deleted == [path]
    assert client.get("/api/profile").json()["total_observations"] == 2


def test_complete_upload_rejects_oversized_object_and_cleans_it(app_client, monkeypatch):
    main, client = app_client
    deleted = []
    monkeypatch.setattr(main, "MAX_CSV_UPLOAD_BYTES", 3)

    async def fake_download(path):
        return CSV

    async def fake_delete(path):
        deleted.append(path)

    monkeypatch.setattr(main, "download_supabase_csv", fake_download)
    monkeypatch.setattr(main, "delete_supabase_csv", fake_delete)
    path = "pending/0123456789abcdef0123456789abcdef.csv"
    response = client.post("/api/import/csv/complete", json={
        "source": "MODIS_SP", "filename": "modis.csv", "path": path,
    })
    assert response.status_code == 413
    assert deleted == [path]


def test_complete_upload_rejects_untrusted_object_paths(app_client, monkeypatch):
    main, client = app_client
    called = False

    async def fake_download(path):
        nonlocal called
        called = True
        return CSV

    monkeypatch.setattr(main, "download_supabase_csv", fake_download)
    response = client.post("/api/import/csv/complete", json={
        "source": "MODIS_SP", "filename": "modis.csv", "path": "../private.csv",
    })
    assert response.status_code == 422
    assert called is False


def test_vercel_status_advertises_50mb_when_storage_credentials_exist(app_client, monkeypatch):
    main, client = app_client
    monkeypatch.setattr(main, "IS_VERCEL", True)
    monkeypatch.setattr(main, "SUPABASE_URL", "https://example-project.supabase.co")
    monkeypatch.setattr(main, "SUPABASE_SERVICE_ROLE_KEY", "server-only-test-key")
    status = client.get("/api/status").json()
    assert status["max_csv_upload_bytes"] == 50_000_000
    assert status["large_csv_upload_ready"] is True
    assert status["deployment_platform"] == "vercel"
