from __future__ import annotations

import importlib
from datetime import date

from fastapi.testclient import TestClient

from backend.integrations import parse_meteomatics_payload, _parse_stats


def test_parse_meteomatics_parameter_first_payload_into_rows():
    payload = {
        "data": [
            {"parameter": "t_2m:C", "coordinates": [{"dates": [
                {"date": "2025-10-01T00:00:00Z", "value": 22.5},
                {"date": "2025-10-01T06:00:00Z", "value": 24.0},
            ]}]},
            {"parameter": "relative_humidity_2m:p", "coordinates": [{"dates": [
                {"date": "2025-10-01T00:00:00Z", "value": 70.0},
                {"date": "2025-10-01T06:00:00Z", "value": -999},
            ]}]},
        ]
    }
    rows = parse_meteomatics_payload(payload)
    assert len(rows) == 2
    assert rows[0]["t_2m:C"] == 22.5
    assert rows[0]["relative_humidity_2m:p"] == 70.0
    assert rows[1]["relative_humidity_2m:p"] is None


def test_planetary_computer_statistics_parser_supports_geojson_response():
    payload = {"type": "Feature", "properties": {"statistics": {
        "250m_16_days_NDVI": {
            "mean": 0.42, "min": -0.1, "max": 0.81,
            "percentile_10": 0.12, "percentile_90": 0.68,
            "valid_percent": 88.5, "valid_pixels": 1234,
        }
    }}}
    stats = _parse_stats(payload)
    assert stats["mean"] == 0.42
    assert stats["valid_percent"] == 88.5


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            import httpx
            request = httpx.Request("GET", "https://mock.invalid")
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError("mock failure", request=request, response=response)

    def json(self):
        return self._payload


class FakeAsyncClient:
    calls = []
    weather_payload = {"data": [
        {"parameter": "t_2m:C", "coordinates": [{"dates": [
            {"date": "2025-10-01T00:00:00Z", "value": 24.0},
        ]}]},
        {"parameter": "relative_humidity_2m:p", "coordinates": [{"dates": [
            {"date": "2025-10-01T00:00:00Z", "value": 62.0},
        ]}]},
    ]}

    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        return FakeResponse(self.weather_payload)

    async def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        if url.endswith("/search"):
            return FakeResponse({"features": [
                {"id": "MOD13Q1.A2025273.h10v05.061", "properties": {"datetime": "2025-10-01T00:00:00Z"}, "assets": {"250m_16_days_NDVI": {"href": "https://example.invalid/ndvi.tif"}}},
                {"id": "MOD13Q1.A2025289.h10v05.061", "properties": {"datetime": "2025-10-17T00:00:00Z"}, "assets": {"250m_16_days_NDVI": {"href": "https://example.invalid/ndvi2.tif"}}},
            ]})
        return FakeResponse({"type": "Feature", "properties": {"statistics": {
            "250m_16_days_NDVI": {
                "mean": 0.41, "min": -0.1, "max": 0.8,
                "percentile_10": 0.1, "percentile_90": 0.7,
                "valid_percent": 91.0, "valid_pixels": 1000,
            }
        }}})


def _client(monkeypatch, tmp_path):
    monkeypatch.setenv("EMBERLENS_DB", str(tmp_path / "integrations.sqlite"))
    from backend import main
    importlib.reload(main)
    monkeypatch.setattr(main.httpx, "AsyncClient", FakeAsyncClient)
    FakeAsyncClient.calls = []
    return main, TestClient(main.app)


def test_integrations_status_does_not_expose_credentials(monkeypatch, tmp_path):
    monkeypatch.delenv("METEOMATICS_USERNAME", raising=False)
    monkeypatch.delenv("METEOMATICS_PASSWORD", raising=False)
    _, client = _client(monkeypatch, tmp_path)
    response = client.get("/api/integrations/status")
    assert response.status_code == 200
    body = response.json()
    assert body["meteomatics_configured"] is False
    assert body["planetary_computer_available"] is True
    assert "password" not in str(body).lower()


def test_weather_endpoint_requires_claimed_offer_credentials(monkeypatch, tmp_path):
    monkeypatch.delenv("METEOMATICS_USERNAME", raising=False)
    monkeypatch.delenv("METEOMATICS_PASSWORD", raising=False)
    _, client = _client(monkeypatch, tmp_path)
    response = client.get("/api/context/weather", params={
        "region": "punjab", "start_date": "2025-10-01", "end_date": "2025-10-01"
    })
    assert response.status_code == 503
    assert "METEOMATICS_USERNAME" in response.json()["detail"]


def test_weather_endpoint_uses_server_side_basic_auth(monkeypatch, tmp_path):
    monkeypatch.setenv("METEOMATICS_USERNAME", "offer-user")
    monkeypatch.setenv("METEOMATICS_PASSWORD", "offer-password")
    _, client = _client(monkeypatch, tmp_path)
    response = client.get("/api/context/weather", params={
        "region": "punjab", "start_date": "2025-10-01", "end_date": "2025-10-01"
    })
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["provider"] == "Meteomatics Weather API"
    assert body["observations"][0]["t_2m:C"] == 24.0
    call = next(c for c in FakeAsyncClient.calls if c[0] == "GET")
    assert call[2]["auth"] == ("offer-user", "offer-password")
    assert "offer-password" not in str(body)  # credentials are never returned


def test_planetary_computer_returns_area_ndvi_statistics(monkeypatch, tmp_path):
    _, client = _client(monkeypatch, tmp_path)
    response = client.get("/api/context/vegetation", params={
        "region": "punjab", "start_date": "2025-10-01", "end_date": "2025-10-31"
    })
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["collection"] == "modis-13Q1-061"
    assert len(body["composites"]) == 2
    assert body["composites"][0]["mean_ndvi"] == 0.41
    stats_calls = [c for c in FakeAsyncClient.calls if c[0] == "POST" and c[1].endswith("/item/statistics")]
    assert len(stats_calls) == 2
    assert stats_calls[0][2]["params"][3] == ("unscale", "true")
    assert stats_calls[0][2]["json"]["geometry"]["type"] == "Polygon"


def test_context_endpoints_reject_unbounded_region_and_oversized_period(monkeypatch, tmp_path):
    monkeypatch.setenv("METEOMATICS_USERNAME", "u")
    monkeypatch.setenv("METEOMATICS_PASSWORD", "p")
    _, client = _client(monkeypatch, tmp_path)
    weather = client.get("/api/context/weather", params={
        "region": "all", "start_date": "2025-10-01", "end_date": "2025-10-01"
    })
    assert weather.status_code == 422
    weather_long = client.get("/api/context/weather", params={
        "region": "punjab", "start_date": "2025-10-01", "end_date": "2025-10-20"
    })
    assert weather_long.status_code == 422
    vegetation_long = client.get("/api/context/vegetation", params={
        "region": "punjab", "start_date": "2025-01-01", "end_date": "2025-06-01"
    })
    assert vegetation_long.status_code == 422


def test_planetary_stats_request_uses_raw_geojson_feature():
    import asyncio
    from backend.integrations import get_vegetation_context
    class Client:
        def __init__(self): self.bodies=[]
        async def post(self,url,**kwargs):
            self.bodies.append((url,kwargs.get("json")))
            if url.endswith("/search"):
                return FakeResponse({"features":[{"id":"test-id", "properties":{"datetime":"2025-10-01T00:00:00Z"},"assets":{"250m_16_days_NDVI":{}}}]})
            return FakeResponse({"type":"Feature","properties":{"statistics":{"250m_16_days_NDVI":{"mean":0.42}}}})
    client=Client()
    result=asyncio.run(get_vegetation_context(client,region="punjab",region_meta={"bbox":[73.7,27.5,77.8,32.6]},start_date=date(2025,10,1),end_date=date(2025,10,15)))
    assert len(result["composites"])==1
    url,body=client.bodies[-1]
    assert url.endswith("/item/statistics")
    assert body["type"]=="Feature"
    assert body["geometry"]["type"]=="Polygon"
    assert "geojson" not in body
