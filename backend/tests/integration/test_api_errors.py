from fastapi.testclient import TestClient

from app.errors import FVError
from app.main import app


@app.get("/api/_test/fv-error")
def _raise_fv():
    raise FVError("FV-ACT-002", detail={"action_id": "a1"})


@app.get("/api/_test/boom")
def _raise_boom():
    raise RuntimeError("boom")


client = TestClient(app, raise_server_exceptions=False)


def test_health():
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert "X-Request-Id" in r.headers


def test_fv_error_uses_catalog_status_and_body():
    r = client.get("/api/_test/fv-error")
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "FV-ACT-002"


def test_unknown_route_is_resource_not_found():
    r = client.get("/api/nope")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "FV-REQ-004"


def test_unexpected_error_hides_internals():
    r = client.get("/api/_test/boom")
    assert r.status_code == 500
    body = r.json()["error"]
    assert body["code"] == "FV-SYS-001"
    assert "boom" not in str(body)
