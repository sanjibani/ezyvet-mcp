"""Smoke tests for ezyvet-mcp — no live API calls required.

Built with ``respx`` (industry-standard httpx mocking) + ``pytest-asyncio``.
"""
from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import respx
from hypothesis import given, settings
from hypothesis import strategies as st

from ezyvet_mcp import (
    EzyvetAPIError,
    EzyvetAuthError,
    EzyvetClient,
    EzyvetConnectionError,
    EzyvetNotFoundError,
    EzyvetRateLimitError,
)
from ezyvet_mcp.server import _format_error, _json

# --- Fixtures --------------------------------------------------------------


def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZYVET_PARTNER_ID", "partner")
    monkeypatch.setenv("EZYVET_CLIENT_ID", "client")
    monkeypatch.setenv("EZYVET_CLIENT_SECRET", "secret")
    monkeypatch.setenv("EZYVET_SITE_UID", "site")
    monkeypatch.setenv("EZYVET_SCOPE", "read-animal read-contact")


@pytest.fixture
async def client(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[EzyvetClient]:
    _env(monkeypatch)
    c = EzyvetClient()
    try:
        yield c
    finally:
        await c.aclose()


@pytest.fixture
async def authed_client(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[EzyvetClient]:
    """Client with a pre-minted token (skips the OAuth call)."""
    _env(monkeypatch)
    c = EzyvetClient()
    # Manually set a token to skip the OAuth dance in tests that don't care about it
    c._token = "pre-minted-token"
    c._token_expires_at = float("inf")
    try:
        yield c
    finally:
        await c.aclose()


# --- Client construction --------------------------------------------------


def test_client_missing_credentials_raises() -> None:
    for k in ("EZYVET_PARTNER_ID", "EZYVET_CLIENT_ID", "EZYVET_CLIENT_SECRET", "EZYVET_SITE_UID"):
        os.environ.pop(k, None)
    with pytest.raises(EzyvetAuthError):
        EzyvetClient()


def test_client_uses_env_when_no_args(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch)
    client = EzyvetClient()
    assert client._partner_id == "partner"
    assert client._site_uid == "site"


@pytest.mark.asyncio
async def test_client_aclose_closes_underlying_httpx_client(
    authed_client: EzyvetClient,
) -> None:
    assert not authed_client._client.is_closed
    await authed_client.aclose()
    assert authed_client._client.is_closed


# --- OAuth token lifecycle ------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_first_request_mints_token(client: EzyvetClient) -> None:
    token_route = respx.post("https://api.ezyvet.com/v1/oauth/access_token").mock(
        return_value=httpx.Response(200, json={"access_token": "tok-1", "expires_in": 43200})
    )
    users_route = respx.get("https://api.ezyvet.com/v4/user").mock(
        return_value=httpx.Response(200, json={"items": [], "meta": {}, "messages": []})
    )
    await client.list_users()
    assert token_route.call_count == 1
    assert users_route.call_count == 1
    assert users_route.calls[0].request.headers["Authorization"] == "Bearer tok-1"


@pytest.mark.asyncio
@respx.mock
async def test_token_caching_reuses_token(authed_client: EzyvetClient) -> None:
    users_route = respx.get("https://api.ezyvet.com/v4/user").mock(
        return_value=httpx.Response(200, json={"items": [], "meta": {}, "messages": []})
    )
    await authed_client.list_users()
    await authed_client.list_users()
    # Token cached — only 2 user-list calls, no token mint
    assert users_route.call_count == 2
    # Both requests used same bearer
    for c in users_route.calls:
        assert c.request.headers["Authorization"] == "Bearer pre-minted-token"


@pytest.mark.asyncio
@respx.mock
async def test_401_refreshes_token(authed_client: EzyvetClient) -> None:
    # OAuth refresh
    refresh = respx.post("https://api.ezyvet.com/v1/oauth/access_token").mock(
        return_value=httpx.Response(200, json={"access_token": "tok-2", "expires_in": 43200})
    )
    # First list_users call returns 401, retry succeeds
    users_route = respx.get("https://api.ezyvet.com/v4/user").mock(
        side_effect=[
            httpx.Response(401, text=""),
            httpx.Response(200, json=[]),
        ]
    )
    authed_client._max_retries = 3  # allow retries
    await authed_client.list_users()
    assert refresh.call_count == 1
    assert users_route.call_count == 2
    assert users_route.calls[1].request.headers["Authorization"] == "Bearer tok-2"


@pytest.mark.asyncio
@respx.mock
async def test_token_mint_failure_raises(client: EzyvetClient) -> None:
    respx.post("https://api.ezyvet.com/v1/oauth/access_token").mock(
        return_value=httpx.Response(401, json={"error": "invalid_client"})
    )
    with pytest.raises(EzyvetAuthError) as exc_info:
        await client.list_users()
    assert exc_info.value.http_status == 401


# --- HTTP status code mapping --------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_403_raises_auth_error(authed_client: EzyvetClient) -> None:
    respx.get("https://api.ezyvet.com/v4/user").mock(
        return_value=httpx.Response(403, text="forbidden")
    )
    with pytest.raises(EzyvetAuthError) as exc_info:
        await authed_client.list_users()
    assert exc_info.value.http_status == 403


@pytest.mark.asyncio
@respx.mock
async def test_404_raises_not_found(authed_client: EzyvetClient) -> None:
    respx.get("https://api.ezyvet.com/v1/animal/9999").mock(
        return_value=httpx.Response(404, json={"message": "no such animal"})
    )
    with pytest.raises(EzyvetNotFoundError):
        await authed_client.get_animal(9999)


@pytest.mark.asyncio
@respx.mock
async def test_429_includes_retry_after(authed_client: EzyvetClient) -> None:
    respx.get("https://api.ezyvet.com/v4/user").mock(
        return_value=httpx.Response(429, headers={"retry-after": "2.5"}, text="slow")
    )
    with pytest.raises(EzyvetRateLimitError) as exc_info:
        await authed_client.list_users()
    assert exc_info.value.retry_after == 2.5


@pytest.mark.asyncio
@respx.mock
async def test_500_captures_request_id(authed_client: EzyvetClient) -> None:
    respx.get("https://api.ezyvet.com/v4/user").mock(
        return_value=httpx.Response(500, headers={"x-request-id": "req-abc"}, text="boom")
    )
    with pytest.raises(EzyvetAPIError) as exc_info:
        await authed_client.list_users()
    assert exc_info.value.request_id == "req-abc"


@pytest.mark.asyncio
@respx.mock
async def test_connection_error_wrapped(authed_client: EzyvetClient) -> None:
    respx.get("https://api.ezyvet.com/v4/user").mock(
        side_effect=httpx.ConnectError("DNS failure")
    )
    with pytest.raises(EzyvetConnectionError):
        await authed_client.list_users()


# --- Retry with exponential backoff ---------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_429_is_retried_then_raises(authed_client: EzyvetClient) -> None:
    route = respx.get("https://api.ezyvet.com/v4/user").mock(
        return_value=httpx.Response(429, text="slow")
    )
    authed_client._max_retries = 2
    with pytest.raises(EzyvetRateLimitError):
        await authed_client.list_users()
    assert route.call_count == 3  # initial + 2 retries


@pytest.mark.asyncio
@respx.mock
async def test_5xx_eventually_succeeds_after_retry(authed_client: EzyvetClient) -> None:
    route = respx.get("https://api.ezyvet.com/v4/user").mock(
        side_effect=[
            httpx.Response(502, text="bad gateway"),
            httpx.Response(503, text="unavailable"),
            httpx.Response(
                200,
                json={"items": [{"id": 1, "name": "Sarah"}], "meta": {}, "messages": []},
            ),
        ]
    )
    authed_client._max_retries = 3
    result = await authed_client.list_users()
    assert result["items"][0]["name"] == "Sarah"
    assert route.call_count == 3


# --- Write endpoints: POST / PATCH ----------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_create_animal_uses_post(authed_client: EzyvetClient) -> None:
    route = respx.post("https://api.ezyvet.com/v1/animal").mock(
        return_value=httpx.Response(201, json={"id": 1, "name": "Rex"})
    )
    result = await authed_client.create_animal({"name": "Rex", "species_id": 1})
    assert result["id"] == 1
    assert route.calls[0].request.method == "POST"
    body = json.loads(route.calls[0].request.content)
    assert body == {"name": "Rex", "species_id": 1}


@pytest.mark.asyncio
@respx.mock
async def test_update_animal_uses_patch(authed_client: EzyvetClient) -> None:
    route = respx.patch("https://api.ezyvet.com/v1/animal/1").mock(
        return_value=httpx.Response(200, json={"id": 1, "name": "Rex II"})
    )
    await authed_client.update_animal(1, {"name": "Rex II"})
    assert route.calls[0].request.method == "PATCH"


# --- Pagination -----------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_find_animals_passes_pagination(authed_client: EzyvetClient) -> None:
    route = respx.get("https://api.ezyvet.com/v1/animal").mock(
        return_value=httpx.Response(200, json={"items": [], "meta": {"page": 1}, "messages": []})
    )
    await authed_client.find_animals(name="Rex", page=3, limit=25)
    assert route.calls[0].request.url.params["name"] == "Rex"
    assert route.calls[0].request.url.params["page"] == "3"
    assert route.calls[0].request.url.params["limit"] == "25"


# --- Property-based test --------------------------------------------------


@given(st.dictionaries(st.text(min_size=1), st.integers() | st.text() | st.booleans(), max_size=10))
@settings(max_examples=50, deadline=None)
def test_json_serialization_round_trip(d: dict[str, Any]) -> None:
    try:
        json.loads(_json(d))
    except (TypeError, ValueError):
        pytest.skip("non-JSON value")
    assert json.loads(_json(d)) == d


# --- Server error helpers -------------------------------------------------


def test_format_error_auth_suggests_env_vars() -> None:
    msg = _format_error(EzyvetAuthError("bad"))
    assert "EZYVET_PARTNER_ID" in msg


def test_format_error_404_says_not_found() -> None:
    msg = _format_error(EzyvetNotFoundError("missing"))
    assert "not found" in msg.lower()


def test_format_error_429_includes_retry_after() -> None:
    msg = _format_error(EzyvetRateLimitError("slow", retry_after=5.0))
    assert "Retry in 5.0s" in msg or "Retry in 5s" in msg


def test_format_error_connection_says_network() -> None:
    msg = _format_error(EzyvetConnectionError("dns"))
    assert "network" in msg.lower()


def test_format_error_generic() -> None:
    msg = _format_error(ValueError("nope"))
    assert "Unexpected" in msg


def test_error_repr_includes_structured_fields() -> None:
    err = EzyvetAPIError("boom", http_status=500, error_code="oops", request_id="req-1")
    r = repr(err)
    assert "http_status=500" in r
    assert "error_code='oops'" in r
    assert "request_id='req-1'" in r



# --- Security regression: tools must raise (not return string) so FastMCP ---
# sets isError=true. See the Blackwell Systems audit (54 MCPs / 20 bugs)
# and MCPTox benchmark (arXiv:2508.14925): returning error strings as plain
# content makes agents retry indefinitely. We verify our tools do NOT
# regress to that pattern.
#
# Pattern: call the FastMCP server in-process, force a tool to encounter
# a downstream error, assert that FastMCP sees a ToolError (which its
# internal call_tool() handler converts to CallToolResult with isError=true).


@pytest.mark.asyncio
@respx.mock
async def test_tool_error_sets_iserror_true() -> None:
    """FastMCP must wrap the raised exception → sets isError=true over the wire."""
    # Set up creds for client construction
    os.environ["EZYVET_PARTNER_ID"] = "p"
    os.environ["EZYVET_CLIENT_ID"] = "c"
    os.environ["EZYVET_CLIENT_SECRET"] = "s"
    os.environ["EZYVET_SITE_UID"] = "site"
    os.environ["EZYVET_SCOPE"] = "read"

    # Mock the OAuth refresh (if applicable) + a failing API call
    respx.post("https://api.ezyvet.com/v1/oauth/access_token").mock(
        return_value=httpx.Response(
            200, json={"access_token": "new", "expires_in": 43200}
        )
    )

    respx.get("https://api.ezyvet.com/v4/user").mock(
        return_value=httpx.Response(401, text="")
    )

    from mcp.server.fastmcp.exceptions import ToolError

    from ezyvet_mcp import server as _ezyvet_mcp_server

    with pytest.raises(ToolError) as exc_info:
        await _ezyvet_mcp_server.mcp.call_tool("list_users", {})

    msg = str(exc_info.value)
    assert "ezyVet rejected the bearer token" in msg, (
        f"Expected auth hint in the error; got: {msg!r}. "
        "Returning error strings as plain content (the OLD pattern) loses "
        "isError=true and the agent cannot tell the tool failed."
    )
