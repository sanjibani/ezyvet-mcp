"""Async HTTP client for ezyVet.

Uses ezyVet's REST API with OAuth 2.0 client-credentials flow. Tokens are
cached in-process with their TTL (12 hours) and auto-refreshed when expired.

Built on industry-leading patterns (encode/httpx, stripe-python, authlib):
- **Shared ``httpx.AsyncClient``** with connection pooling + transport retries.
- **Typed exception hierarchy** with structured fields. See ``exceptions.py``.
- **Application-level retry** with exponential backoff + full jitter on
  transient failures, honoring ``Retry-After``.

Docs: https://developers.ezyvet.com/
"""
from __future__ import annotations

import asyncio
import contextlib
import os
import random
import time
from typing import Any

import httpx
import structlog

from . import __version__
from .exceptions import (
    EzyvetAPIError,
    EzyvetAuthError,
    EzyvetConnectionError,
    EzyvetError,
    EzyvetNotFoundError,
    EzyvetRateLimitError,
)

log = structlog.get_logger(__name__)


# --- Configuration constants -----------------------------------------------

DEFAULT_BASE_URL = "https://api.ezyvet.com"
DEFAULT_TIMEOUT = 30.0
TOKEN_PATH = "/v1/oauth/access_token"
TOKEN_TTL_BUFFER_SECONDS = 300  # refresh 5 minutes early

# Connection pool sizing — httpx best practice
DEFAULT_MAX_CONNECTIONS = 100
DEFAULT_MAX_KEEPALIVE_CONNECTIONS = 20
DEFAULT_KEEPALIVE_EXPIRY = 30.0

# Application-level retry (orthogonal to transport-level retries)
DEFAULT_MAX_RETRIES = 3
DEFAULT_BASE_RETRY_DELAY = 0.5
DEFAULT_MAX_RETRY_DELAY = 30.0

RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})


# --- Internal helpers ------------------------------------------------------


def _retry_delay(attempt: int, retry_after: float | None = None) -> float:
    """Exponential backoff with full jitter, clamped to [0.5, 30] seconds."""
    if retry_after is not None:
        return min(float(retry_after), DEFAULT_MAX_RETRY_DELAY)
    delay = min(DEFAULT_BASE_RETRY_DELAY * (2 ** attempt), DEFAULT_MAX_RETRY_DELAY)
    return float(delay * random.uniform(0.5, 1.0))  # full jitter


class EzyvetClient:
    """Async client for ezyVet's REST API.

    OAuth2 client-credentials grant. Credentials:
    - ``partner_id``   — assigned by ezyVet when you register as a partner
    - ``client_id``    — generated per integration
    - ``client_secret`` — generated per integration
    - ``site_uid``     — the ezyVet site to access (UUID)
    - ``scope``        — space-separated scopes your integration needs

    Set the ``EZYVET_*`` env vars or pass them to the constructor.

    Use as an async context manager:

        async with EzyvetClient() as client:
            await client.list_species()
    """

    def __init__(
        self,
        partner_id: str | None = None,
        client_id: str | None = None,
        client_secret: str | None = None,
        site_uid: str | None = None,
        scope: str | None = None,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
    ) -> None:
        partner_id = partner_id or os.environ.get("EZYVET_PARTNER_ID")
        client_id = client_id or os.environ.get("EZYVET_CLIENT_ID")
        client_secret = client_secret or os.environ.get("EZYVET_CLIENT_SECRET")
        site_uid = site_uid or os.environ.get("EZYVET_SITE_UID")
        scope = scope or os.environ.get("EZYVET_SCOPE", "")

        if not all([partner_id, client_id, client_secret, site_uid]):
            raise EzyvetAuthError(
                "ezyVet credentials missing. Set EZYVET_PARTNER_ID, EZYVET_CLIENT_ID, "
                "EZYVET_CLIENT_SECRET, and EZYVET_SITE_UID environment variables, or pass them."
            )
        assert partner_id is not None
        assert client_id is not None
        assert client_secret is not None
        assert site_uid is not None
        self._partner_id: str = partner_id
        self._client_id: str = client_id
        self._client_secret: str = client_secret
        self._site_uid: str = site_uid
        self._scope: str = scope
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._max_retries = max_retries

        # Token cache — protected by lock for concurrent refresh
        self._token: str | None = None
        self._token_expires_at: float = 0.0
        self._token_lock = asyncio.Lock()

        # Build shared httpx.AsyncClient with pooling + transport retries.
        transport = httpx.AsyncHTTPTransport(retries=3)
        limits = httpx.Limits(
            max_connections=DEFAULT_MAX_CONNECTIONS,
            max_keepalive_connections=DEFAULT_MAX_KEEPALIVE_CONNECTIONS,
            keepalive_expiry=DEFAULT_KEEPALIVE_EXPIRY,
        )
        timeout_obj = httpx.Timeout(
            timeout,
            connect=10.0,
            read=timeout,
            write=10.0,
            pool=5.0,
        )
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=timeout_obj,
            limits=limits,
            transport=transport,
            headers={
                "User-Agent": f"ezyvet-mcp/{__version__}",
                "Accept": "application/json",
            },
            follow_redirects=False,
        )

    # --- Context manager ------------------------------------------------------

    async def __aenter__(self) -> EzyvetClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Flush keepalive connections and release the httpx client."""
        await self._client.aclose()

    # --- Token lifecycle ------------------------------------------------------

    async def _fetch_token(self) -> str:
        """Mint a new access token via OAuth client_credentials."""
        try:
            response = await self._client.post(
                TOKEN_PATH,
                json={
                    "partner_id": self._partner_id,
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "grant_type": "client_credentials",
                    "scope": self._scope,
                    "site_uid": self._site_uid,
                },
            )
        except httpx.HTTPError as exc:
            raise EzyvetConnectionError(
                f"Network failure during ezyVet token fetch: {exc}",
            ) from exc

        if response.status_code != 200:
            try:
                body = response.json()
            except ValueError:
                body = response.text
            raise EzyvetAuthError(
                f"Failed to mint ezyVet token (HTTP {response.status_code}): {body}",
                http_status=response.status_code,
                body=body,
            )
        data = response.json()
        token = data.get("access_token")
        ttl = int(data.get("expires_in", 0))
        if not token:
            raise EzyvetAuthError(f"No access_token in OAuth response: {data}")
        self._token = token
        self._token_expires_at = time.monotonic() + ttl - TOKEN_TTL_BUFFER_SECONDS
        return str(token)

    async def _get_token(self) -> str:
        """Return a valid token, refreshing if expired. Concurrent-safe."""
        async with self._token_lock:
            if self._token is None or time.monotonic() >= self._token_expires_at:
                await self._fetch_token()
            assert self._token is not None
            return self._token

    # --- Request execution ----------------------------------------------------

    def _raise_for_status(self, response: httpx.Response) -> None:
        """Map a non-2xx response to the most specific typed exception."""
        request_id = (
            response.headers.get("x-request-id")
            or response.headers.get("x-amzn-requestid")
            or response.headers.get("request-id")
        )
        try:
            body = response.json()
        except ValueError:
            body = response.text

        if response.status_code == 404:
            raise EzyvetNotFoundError(
                f"ezyVet resource not found: {response.url}",
                http_status=404,
                request_id=request_id,
                body=body,
            )
        if response.status_code == 401:
            raise EzyvetAuthError(
                "ezyVet rejected the bearer token (HTTP 401).",
                http_status=401,
                request_id=request_id,
                body=body,
            )
        if response.status_code == 403:
            raise EzyvetAuthError(
                "ezyVet denied access (HTTP 403). Check your scope and site_uid.",
                http_status=403,
                request_id=request_id,
                body=body,
            )
        if response.status_code == 429:
            retry_after: float | None = None
            with contextlib.suppress(ValueError):
                ra_header = response.headers.get("retry-after")
                if ra_header:
                    retry_after = float(ra_header)
            raise EzyvetRateLimitError(
                "ezyVet rate limit hit (HTTP 429). Slow down — limit is 60 req/min most endpoints.",
                retry_after=retry_after,
                request_id=request_id,
                body=body,
            )
        if 500 <= response.status_code < 600:
            raise EzyvetAPIError(
                f"ezyVet server error (HTTP {response.status_code})",
                http_status=response.status_code,
                request_id=request_id,
                body=body,
            )
        raise EzyvetAPIError(
            f"ezyVet returned HTTP {response.status_code}",
            http_status=response.status_code,
            request_id=request_id,
            body=body,
        )

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
    ) -> Any:
        """Issue an authenticated request with retry on transient errors."""
        last_exc: EzyvetError | None = None
        for attempt in range(self._max_retries + 1):
            try:
                token = await self._get_token()
            except EzyvetAuthError:
                # Don't retry if we can't even mint a token.
                raise

            headers = {"Authorization": f"Bearer {token}"}
            log.info("request.start", method=method, path=path, attempt=attempt)
            t0 = time.monotonic()
            try:
                response = await self._client.request(
                    method, path, params=params, json=json, headers=headers,
                )
            except httpx.HTTPError as exc:
                duration_ms = (time.monotonic() - t0) * 1000
                log.warning(
                    "request.connection_error",
                    method=method, path=path, error=str(exc),
                    duration_ms=round(duration_ms, 1),
                )
                if attempt < self._max_retries:
                    await asyncio.sleep(_retry_delay(attempt))
                    continue
                raise EzyvetConnectionError(
                    f"Network failure calling ezyVet {method} {path}: {exc}",
                ) from exc

            duration_ms = (time.monotonic() - t0) * 1000
            log.info(
                "request.end",
                method=method, path=path, status=response.status_code,
                duration_ms=round(duration_ms, 1),
            )

            # 401 → force token refresh + retry once
            if response.status_code == 401 and attempt == 0:
                log.warning("request.401_forcing_token_refresh", path=path)
                async with self._token_lock:
                    self._token = None
                continue

            # Retryable errors → backoff and try again
            if response.status_code in RETRYABLE_STATUS_CODES and attempt < self._max_retries:
                retry_after: float | None = None
                with contextlib.suppress(ValueError):
                    ra_header = response.headers.get("retry-after")
                    if ra_header:
                        retry_after = float(ra_header)
                delay = _retry_delay(attempt, retry_after)
                log.warning(
                    "request.retry",
                    method=method, path=path, status=response.status_code,
                    attempt=attempt, delay=round(delay, 2),
                )
                await asyncio.sleep(delay)
                continue

            if 200 <= response.status_code < 300:
                text = response.text
                if not text:
                    return None
                try:
                    return response.json()
                except ValueError:
                    return text

            try:
                self._raise_for_status(response)
            except EzyvetRateLimitError as exc:
                last_exc = exc
                if attempt < self._max_retries:
                    delay = _retry_delay(attempt, exc.retry_after)
                    log.warning("request.retry_after_429", delay=round(delay, 2))
                    await asyncio.sleep(delay)
                    continue
                raise
            except (EzyvetAPIError, EzyvetAuthError, EzyvetNotFoundError):
                raise
            except EzyvetError as exc:
                last_exc = exc
                if attempt < self._max_retries:
                    await asyncio.sleep(_retry_delay(attempt))
                    continue
                raise

        assert last_exc is not None
        raise last_exc

    # ----- Animals (patients) ------------------------------------------------

    async def get_animal(self, animal_id: int) -> Any:
        """Fetch a single animal by ID."""
        return await self._request("GET", f"/v1/animal/{animal_id}")

    async def find_animals(
        self,
        *,
        name: str | None = None,
        species_id: int | None = None,
        breed_id: int | None = None,
        page: int = 1,
        limit: int = 50,
    ) -> Any:
        """List/search animals. Returns ``{meta, items, messages}`` envelope."""
        params: dict[str, Any] = {"page": page, "limit": limit}
        if name:
            params["name"] = name
        if species_id is not None:
            params["species_id"] = species_id
        if breed_id is not None:
            params["breed_id"] = breed_id
        return await self._request("GET", "/v1/animal", params=params)

    async def create_animal(self, animal: dict[str, Any]) -> Any:
        """Create a new animal/patient record."""
        return await self._request("POST", "/v1/animal", json=animal)

    async def update_animal(self, animal_id: int, updates: dict[str, Any]) -> Any:
        """Patch fields on an animal record."""
        return await self._request("PATCH", f"/v1/animal/{animal_id}", json=updates)

    # ----- Contacts (clients) ------------------------------------------------

    async def get_contact(self, contact_id: int) -> Any:
        """Fetch a single contact (the pet owner)."""
        return await self._request("GET", f"/v2/contact/{contact_id}")

    async def find_contacts(
        self,
        *,
        first_name: str | None = None,
        last_name: str | None = None,
        email: str | None = None,
        page: int = 1,
        limit: int = 50,
    ) -> Any:
        """List/search contacts."""
        params: dict[str, Any] = {"page": page, "limit": limit}
        if first_name:
            params["first_name"] = first_name
        if last_name:
            params["last_name"] = last_name
        if email:
            params["email"] = email
        return await self._request("GET", "/v2/contact", params=params)

    async def create_contact(self, contact: dict[str, Any]) -> Any:
        """Create a new contact."""
        return await self._request("POST", "/v2/contact", json=contact)

    # ----- Appointments ------------------------------------------------------

    async def find_appointments(
        self,
        *,
        animal_id: int | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        appointment_type_id: int | None = None,
        page: int = 1,
        limit: int = 50,
    ) -> Any:
        """List appointments with optional filters."""
        params: dict[str, Any] = {"page": page, "limit": limit}
        if animal_id is not None:
            params["animal_id"] = animal_id
        if start_date:
            params["start_date"] = start_date
        if end_date:
            params["end_date"] = end_date
        if appointment_type_id is not None:
            params["appointment_type_id"] = appointment_type_id
        return await self._request("GET", "/v2/appointment", params=params)

    async def create_appointment(self, appointment: dict[str, Any]) -> Any:
        """Create a new appointment."""
        return await self._request("POST", "/v2/appointment", json=appointment)

    # ----- Consults (clinical visits) ----------------------------------------

    async def find_consults(
        self,
        *,
        animal_id: int | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        page: int = 1,
        limit: int = 50,
    ) -> Any:
        """List clinical consults (visits)."""
        params: dict[str, Any] = {"page": page, "limit": limit}
        if animal_id is not None:
            params["animal_id"] = animal_id
        if start_date:
            params["start_date"] = start_date
        if end_date:
            params["end_date"] = end_date
        return await self._request("GET", "/v1/consult", params=params)

    async def create_consult(self, consult: dict[str, Any]) -> Any:
        """Open a new clinical consult."""
        return await self._request("POST", "/v1/consult", json=consult)

    # ----- Invoices ---------------------------------------------------------

    async def find_invoices(
        self,
        *,
        contact_id: int | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        page: int = 1,
        limit: int = 50,
    ) -> Any:
        """List invoices."""
        params: dict[str, Any] = {"page": page, "limit": limit}
        if contact_id is not None:
            params["contact_id"] = contact_id
        if start_date:
            params["start_date"] = start_date
        if end_date:
            params["end_date"] = end_date
        return await self._request("GET", "/v2/invoice", params=params)

    # ----- Reference data ---------------------------------------------------

    async def list_species(self) -> Any:
        """List animal species (dog, cat, etc.)."""
        return await self._request("GET", "/v4/species")

    async def list_breeds(self) -> Any:
        """List animal breeds."""
        return await self._request("GET", "/v4/breed")

    async def list_appointment_types(self) -> Any:
        """List appointment type definitions."""
        return await self._request("GET", "/v2/appointmenttype")

    async def list_users(self) -> Any:
        """List vet practice users (vets, nurses, receptionists)."""
        return await self._request("GET", "/v4/user")
