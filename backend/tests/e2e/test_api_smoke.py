import os

import httpx
import pytest

BASE = os.environ.get("FV_E2E_BASE_URL")
pytestmark = pytest.mark.skipif(not BASE, reason="set FV_E2E_BASE_URL to run e2e tests")


def test_health_is_up() -> None:
    r = httpx.get(f"{BASE}/api/health", timeout=30)
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
