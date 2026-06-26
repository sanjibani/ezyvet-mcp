"""Async HTTP client for ezyVet.

Uses ezyVet's REST API with OAuth 2.0 client-credentials flow. Tokens are
cached in-process with their TTL (12 hours) and auto-refreshed when expired.

Docs: https://developers.ezyvet.com/
"""
from __future__ import annotations

import asyncio
import os
import time
from typing import Any

import httpx


DEFAULT_BASE_URL = "https://api.ezyvet.com"
DEFAULT_TIMEOUT = 30.0
TOKEN_PATH = "/v1/oauth/access_token"
TOKEN_TTL_BUFFER_SECONDS = 300  # refresh 5 minutes early


class EzyvetError(RuntimeError):
    """Base exception for ezyVet client errors."""

    def __init__(self, message: str, status_code: int | None = None, body: Any = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.body = body


class EzyvetAuthError(EzyvetError):
    """Raised when credentials are missing, invalid, or unauthorized."""


class EzyvetAPIError(EzyvetError):
    """Raised on non-2xx API responses other than auth failures."""


class EzyvetClient:
    """Async client for ezyVet's REST API.

    OAuth2 client-credentials grant. Credentials:
    - ``partner_id``   — assigned by ezyVet when you register as a partner
    - ``client_id``    — generated per integration
    - ``client_secret`` — generated per integration
    - ``site_uid``     — the ezyVet site to access (UUID)
    - ``scope``        — space-separated scopes your integration needs

    Set the ``EZYVET_*`` env vars or pass them to the constructor.
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
        self._partner_id = partner_id
        self._client_id = client_id
        self._client_secret = client_secret
        self._site_uid = site_uid
        self._scope = scope
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

        # Token cache — protected by lock for concurrent refresh
        self._token: str | None = None
        self._token_expires_at: float = 0.0
        self._token_lock = asyncio.Lock()

    async def _fetch_token(self) -> str:
        """Mint a new access token via OAuth client_credentials."""
        async with httpx.AsyncClient(timeout=self._timeout) as http:
            response = await http.post(
                f"{self._base_url}{TOKEN_PATH}",
                json={
                    "partner_id": self._partner_id,
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "grant_type": "client_credentials",
                    "scope": self._scope,
                    "site_uid": self._site_uid,
                },
            )
        if response.status_code != 200:
            try:
                body = response.json()
            except ValueError:
                body = response.text
            raise EzyvetAuthError(
                f"Failed to mint ezyVet token (HTTP {response.status_code}): {body}",
                status_code=response.status_code,
                body=body,
            )
        data = response.json()
        token = data.get("access_token")
        ttl = int(data.get("expires_in", 0))
        if not token:
            raise EzyvetAuthError(f"No access_token in OAuth response: {data}")
        self._token = token
        self._token_expires_at = time.monotonic() + ttl - TOKEN_TTL_BUFFER_SECONDS
        return token

    async def _get_token(self) -> str:
        """Return a valid token, refreshing if expired. Concurrent-safe."""
        async with self._token_lock:
            if self._token is None or time.monotonic() >= self._token_expires_at:
                await self._fetch_token()
            assert self._token is not None
            return self._token

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
    ) -> Any:
        url = f"{self._base_url}{path}"
        # Token may have expired between checks — retry once on 401.
        for attempt in range(2):
            token = await self._get_token()
            headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
            async with httpx.AsyncClient(timeout=self._timeout) as http:
                response = await http.request(
                    method, url, params=params, json=json, headers=headers,
                )
            if response.status_code == 401 and attempt == 0:
                # Force token refresh on next iteration
                async with self._token_lock:
                    self._token = None
                continue
            break

        if response.status_code == 401:
            raise EzyvetAuthError("ezyVet rejected the bearer token (HTTP 401).", 401)
        if response.status_code == 403:
            raise EzyvetAuthError(
                "ezyVet denied access (HTTP 403). Check your scope and site_uid.", 403
            )
        if response.status_code == 429:
            raise EzyvetAPIError(
                "ezyVet rate limit hit (HTTP 429). Slow down — limit is 60 req/min most endpoints.",
                429,
            )
        if not 200 <= response.status_code < 300:
            try:
                body = response.json()
            except ValueError:
                body = response.text
            raise EzyvetAPIError(
                f"ezyVet returned HTTP {response.status_code}",
                status_code=response.status_code,
                body=body,
            )

        text = response.text
        if not text:
            return None
        try:
            return response.json()
        except ValueError:
            return text

    # ----- Animals (patients) ------------------------------------------------

    async def get_animal(self, animal_id: int) -> dict[str, Any]:
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
    ) -> dict[str, Any]:
        """List/search animals. Returns ``{meta, items, messages}`` envelope."""
        params: dict[str, Any] = {"page": page, "limit": limit}
        if name:
            params["name"] = name
        if species_id is not None:
            params["species_id"] = species_id
        if breed_id is not None:
            params["breed_id"] = breed_id
        return await self._request("GET", "/v1/animal", params=params)

    async def create_animal(self, animal: dict[str, Any]) -> dict[str, Any]:
        """Create a new animal/patient record."""
        return await self._request("POST", "/v1/animal", json=animal)

    async def update_animal(self, animal_id: int, updates: dict[str, Any]) -> dict[str, Any]:
        """Patch fields on an animal record."""
        return await self._request("PATCH", f"/v1/animal/{animal_id}", json=updates)

    # ----- Contacts (clients) ------------------------------------------------

    async def get_contact(self, contact_id: int) -> dict[str, Any]:
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
    ) -> dict[str, Any]:
        """List/search contacts."""
        params: dict[str, Any] = {"page": page, "limit": limit}
        if first_name:
            params["first_name"] = first_name
        if last_name:
            params["last_name"] = last_name
        if email:
            params["email"] = email
        return await self._request("GET", "/v2/contact", params=params)

    async def create_contact(self, contact: dict[str, Any]) -> dict[str, Any]:
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
    ) -> dict[str, Any]:
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

    async def create_appointment(self, appointment: dict[str, Any]) -> dict[str, Any]:
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
    ) -> dict[str, Any]:
        """List clinical consults (visits)."""
        params: dict[str, Any] = {"page": page, "limit": limit}
        if animal_id is not None:
            params["animal_id"] = animal_id
        if start_date:
            params["start_date"] = start_date
        if end_date:
            params["end_date"] = end_date
        return await self._request("GET", "/v1/consult", params=params)

    async def create_consult(self, consult: dict[str, Any]) -> dict[str, Any]:
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
    ) -> dict[str, Any]:
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

    async def list_species(self) -> dict[str, Any]:
        """List animal species (dog, cat, etc.)."""
        return await self._request("GET", "/v4/species")

    async def list_breeds(self) -> dict[str, Any]:
        """List animal breeds."""
        return await self._request("GET", "/v4/breed")

    async def list_appointment_types(self) -> dict[str, Any]:
        """List appointment type definitions."""
        return await self._request("GET", "/v2/appointmenttype")

    async def list_users(self) -> dict[str, Any]:
        """List vet practice users (vets, nurses, receptionists)."""
        return await self._request("GET", "/v4/user")