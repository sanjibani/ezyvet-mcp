"""Smoke tests for ezyvet-mcp — no live API calls required."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from ezyvet_mcp import EzyvetAPIError, EzyvetAuthError, EzyvetClient
from ezyvet_mcp.server import _format_error, _json


def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZYVET_PARTNER_ID", "partner")
    monkeypatch.setenv("EZYVET_CLIENT_ID", "client")
    monkeypatch.setenv("EZYVET_CLIENT_SECRET", "secret")
    monkeypatch.setenv("EZYVET_SITE_UID", "site")
    monkeypatch.setenv("EZYVET_SCOPE", "read-animal read-contact")


# ----- Client construction --------------------------------------------------


def test_client_missing_credentials_raises() -> None:
    with patch.dict("os.environ", {}, clear=True):
        with pytest.raises(EzyvetAuthError):
            EzyvetClient()


def test_client_uses_env_when_no_args(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch)
    client = EzyvetClient()
    assert client._partner_id == "partner"
    assert client._site_uid == "site"


# ----- Token caching -------------------------------------------------------


@pytest.mark.asyncio
async def test_token_caching_reuses_token(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch)
    client = EzyvetClient()

    # First call fetches token
    fake_token = AsyncMock()
    fake_token.status_code = 200
    fake_token.json = lambda: {"access_token": "tok-1", "expires_in": 43200}
    fake_token.text = ""
    # List users response
    fake_users = AsyncMock()
    fake_users.status_code = 200
    fake_users.text = "{}"
    fake_users.json = lambda: {"items": [], "meta": {}, "messages": []}

    fake_http = AsyncMock()
    fake_http.__aenter__ = AsyncMock(return_value=fake_http)
    fake_http.__aexit__ = AsyncMock(return_value=None)
    # First call returns token, subsequent return user list
    fake_http.post = AsyncMock(return_value=fake_token)
    fake_http.request = AsyncMock(return_value=fake_users)

    with patch("ezyvet_mcp.client.httpx.AsyncClient", return_value=fake_http):
        await client.list_users()
        await client.list_users()  # second call should reuse token
        # Only ONE token mint (post was called once)
        assert fake_http.post.call_count == 1
        # Both API requests used the same bearer token
        for call in fake_http.request.call_args_list:
            assert call.kwargs["headers"]["Authorization"] == "Bearer tok-1"


# ----- Request error handling ----------------------------------------------


@pytest.mark.asyncio
async def test_401_refreshes_token(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch)
    client = EzyvetClient()

    # First token mint
    token_resp = AsyncMock()
    token_resp.status_code = 200
    token_resp.json = lambda: {"access_token": "tok-1", "expires_in": 43200}
    token_resp.text = ""

    # Token mint #2 after 401
    token_resp_2 = AsyncMock()
    token_resp_2.status_code = 200
    token_resp_2.json = lambda: {"access_token": "tok-2", "expires_in": 43200}
    token_resp_2.text = ""

    # First call returns 401, second succeeds
    bad = AsyncMock()
    bad.status_code = 401
    bad.text = ""
    ok = AsyncMock()
    ok.status_code = 200
    ok.text = "{}"
    ok.json = lambda: {}

    fake_http = AsyncMock()
    fake_http.__aenter__ = AsyncMock(return_value=fake_http)
    fake_http.__aexit__ = AsyncMock(return_value=None)
    fake_http.post = AsyncMock(side_effect=[token_resp, token_resp_2])
    fake_http.request = AsyncMock(side_effect=[bad, ok])

    with patch("ezyvet_mcp.client.httpx.AsyncClient", return_value=fake_http):
        result = await client.list_users()
        assert result == {}
        # Token refreshed exactly once after 401
        assert fake_http.post.call_count == 2


@pytest.mark.asyncio
async def test_403_raises_auth_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch)
    client = EzyvetClient()

    token_resp = AsyncMock()
    token_resp.status_code = 200
    token_resp.json = lambda: {"access_token": "tok", "expires_in": 43200}
    token_resp.text = ""
    forbidden = AsyncMock()
    forbidden.status_code = 403
    forbidden.text = "forbidden"
    forbidden.json = lambda: (_ for _ in ()).throw(ValueError("not json"))

    fake_http = AsyncMock()
    fake_http.__aenter__ = AsyncMock(return_value=fake_http)
    fake_http.__aexit__ = AsyncMock(return_value=None)
    fake_http.post = AsyncMock(return_value=token_resp)
    fake_http.request = AsyncMock(return_value=forbidden)

    with patch("ezyvet_mcp.client.httpx.AsyncClient", return_value=fake_http):
        with pytest.raises(EzyvetAuthError):
            await client.list_users()


@pytest.mark.asyncio
async def test_429_raises_api_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch)
    client = EzyvetClient()

    token_resp = AsyncMock()
    token_resp.status_code = 200
    token_resp.json = lambda: {"access_token": "tok", "expires_in": 43200}
    token_resp.text = ""
    limited = AsyncMock()
    limited.status_code = 429
    limited.text = "rate limited"
    limited.json = lambda: (_ for _ in ()).throw(ValueError("not json"))

    fake_http = AsyncMock()
    fake_http.__aenter__ = AsyncMock(return_value=fake_http)
    fake_http.__aexit__ = AsyncMock(return_value=None)
    fake_http.post = AsyncMock(return_value=token_resp)
    fake_http.request = AsyncMock(return_value=limited)

    with patch("ezyvet_mcp.client.httpx.AsyncClient", return_value=fake_http):
        with pytest.raises(EzyvetAPIError):
            await client.list_users()


# ----- Server helpers -------------------------------------------------------


def test_format_error_auth() -> None:
    msg = _format_error(EzyvetAuthError("nope"))
    assert "Authentication" in msg


def test_format_error_api() -> None:
    msg = _format_error(EzyvetAPIError("kaboom", 500, "body"))
    assert "API error" in msg


def test_format_error_generic() -> None:
    msg = _format_error(ValueError("nope"))
    assert "Unexpected" in msg


def test_json_serializes() -> None:
    assert json.loads(_json({"a": 1})) == {"a": 1}