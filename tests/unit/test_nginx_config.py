"""Static checks for the Nginx balancer configuration (rate limiting and error shape).

Nginx itself is validated by the CI job that runs `nginx -t` in a container. These tests
pin the rules that must not disappear in a later edit.
"""

from __future__ import annotations

from pathlib import Path

import pytest

NGINX_DIR = Path(__file__).resolve().parents[2] / "deploy" / "nginx"
CONFIGS = ["vm.conf", "local.conf"]


@pytest.mark.parametrize("name", CONFIGS)
def test_login_has_a_strict_per_client_limit(name: str) -> None:
    text = (NGINX_DIR / name).read_text(encoding="utf-8")

    assert "map $uri $login_key" in text
    assert "/api/v1/auth/login $binary_remote_addr" in text
    assert "limit_req_zone $login_key zone=login:" in text
    assert "rate=5r/m" in text
    assert "limit_req zone=login burst=5 nodelay" in text


@pytest.mark.parametrize("name", CONFIGS)
def test_api_has_a_general_per_client_limit(name: str) -> None:
    text = (NGINX_DIR / name).read_text(encoding="utf-8")

    assert "limit_req_zone $binary_remote_addr zone=api:" in text
    assert "limit_req zone=api burst=40 nodelay" in text


@pytest.mark.parametrize("name", CONFIGS)
def test_rejected_requests_get_a_problem_json_429(name: str) -> None:
    text = (NGINX_DIR / name).read_text(encoding="utf-8")

    assert "error_page 429 = @rate_limited" in text
    assert "application/problem+json" in text
    assert '"status":429' in text
    assert "Retry-After" in text
