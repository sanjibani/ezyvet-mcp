"""ezyVet MCP server.

Exposes ezyVet's REST API (OAuth2 client_credentials) as MCP tools so
Claude / Cursor / any MCP client can read animal records, contacts,
appointments, consults, invoices — and create new ones.

Quick start:
    pip install -e .
    export EZYVET_PARTNER_ID=...
    export EZYVET_CLIENT_ID=...
    export EZYVET_CLIENT_SECRET=...
    export EZYVET_SITE_UID=...
    export EZYVET_SCOPE="read-animal read-contact read-appointment ..."
    ezyvet_mcp
"""

from __future__ import annotations

import json
import sys
from typing import Any

import structlog
from mcp.server.fastmcp import FastMCP

from .audit import audit_tool_call
from .client import EzyvetClient
from .exceptions import (
    EzyvetAPIError,
    EzyvetAuthError,
    EzyvetConnectionError,
    EzyvetError,
    EzyvetNotFoundError,
    EzyvetRateLimitError,
)

log = structlog.get_logger(__name__)


def _format_error(e: Exception) -> str:
    if isinstance(e, EzyvetAuthError):
        return (
            "Authentication failed against ezyVet. "
            f"Check EZYVET_PARTNER_ID / EZYVET_CLIENT_ID / EZYVET_CLIENT_SECRET "
            f"/ EZYVET_SITE_UID / EZYVET_SCOPE. Details: {e}"
        )
    if isinstance(e, EzyvetNotFoundError):
        return f"Resource not found: {e}"
    if isinstance(e, EzyvetRateLimitError):
        wait = f" Retry in {e.retry_after}s." if e.retry_after else ""
        return f"ezyVet rate limit hit.{wait} Slow down — limit is 60 req/min most endpoints."
    if isinstance(e, EzyvetConnectionError):
        return f"Network failure talking to ezyVet: {e}"
    if isinstance(e, EzyvetAPIError):
        request_id = f" (request_id: {e.request_id})" if e.request_id else ""
        return f"ezyVet API error (HTTP {e.http_status}){request_id}: {e}"
    if isinstance(e, EzyvetError):
        return f"ezyVet error: {e}"
    return f"Unexpected error: {e!r}"


def _json(data: Any) -> str:
    return json.dumps(data, indent=2, default=str, ensure_ascii=False)


mcp = FastMCP(
    "ezyvet_mcp",
    instructions=(
        "Tools for ezyVet — cloud-based veterinary practice management. "
        "Read and create animals (patients), contacts (owners), appointments, "
        "consults, invoices, and reference data. OAuth2 client_credentials grant; "
        "tokens are cached in-process and refreshed automatically before expiry."
    ),
)


def _client() -> EzyvetClient:
    return EzyvetClient()


@mcp.tool()
async def get_animal(animal_id: int) -> str:
    """Fetch a single animal (patient) by ezyVet ID."""
    with audit_tool_call("get_animal", {"animal_id": animal_id}) as audit:
        try:
            out = _json(await _client().get_animal(animal_id))
            audit.set_result(out)
            return out
        except EzyvetError:
            raise


@mcp.tool()
async def find_animals(
    name: str | None = None,
    species_id: int | None = None,
    breed_id: int | None = None,
    page: int = 1,
    limit: int = 50,
) -> str:
    """Search animals by name / species / breed. Returns the standard
    ``{meta, items, messages}`` envelope from ezyVet."""
    with audit_tool_call(
        "find_animals",
        {
            "name": name,
            "species_id": species_id,
            "breed_id": breed_id,
            "page": page,
            "limit": limit,
        },
    ) as audit:
        try:
            out = _json(
                await _client().find_animals(
                    name=name, species_id=species_id, breed_id=breed_id, page=page, limit=limit
                )
            )
            audit.set_result(out)
            return out
        except EzyvetError:
            raise


@mcp.tool()
async def create_animal(animal_json: str) -> str:
    """Create a new animal (patient). ``animal_json`` is a JSON object string
    matching ezyVet's animal schema (at minimum: name, species_id)."""
    with audit_tool_call("create_animal", {"animal_json": animal_json}) as audit:
        try:
            data = json.loads(animal_json)
            if not isinstance(data, dict):
                out = "animal_json must decode to a JSON object."
                audit.set_result(out)
                return out
            out = _json(await _client().create_animal(data))
            audit.set_result(out)
            return out
        except json.JSONDecodeError as e:
            out = f"Invalid JSON in animal_json: {e}"
            audit.set_result(out)
            return out
        except EzyvetError:
            raise


@mcp.tool()
async def update_animal(animal_id: int, updates_json: str) -> str:
    """Patch an animal record. ``updates_json`` is a JSON object with only the
    fields you want to change (e.g. ``'{"name": "Rex II", "weight": 32.4}'``)."""
    with audit_tool_call(
        "update_animal", {"animal_id": animal_id, "updates_json": updates_json}
    ) as audit:
        try:
            updates = json.loads(updates_json)
            if not isinstance(updates, dict):
                out = "updates_json must decode to a JSON object."
                audit.set_result(out)
                return out
            out = _json(await _client().update_animal(animal_id, updates))
            audit.set_result(out)
            return out
        except json.JSONDecodeError as e:
            out = f"Invalid JSON in updates_json: {e}"
            audit.set_result(out)
            return out
        except EzyvetError:
            raise


@mcp.tool()
async def get_contact(contact_id: int) -> str:
    """Fetch a contact (the pet owner) by ID."""
    with audit_tool_call("get_contact", {"contact_id": contact_id}) as audit:
        try:
            out = _json(await _client().get_contact(contact_id))
            audit.set_result(out)
            return out
        except EzyvetError:
            raise


@mcp.tool()
async def find_contacts(
    first_name: str | None = None,
    last_name: str | None = None,
    email: str | None = None,
    page: int = 1,
    limit: int = 50,
) -> str:
    """Search contacts by name or email."""
    with audit_tool_call(
        "find_contacts",
        {
            "first_name": first_name,
            "last_name": last_name,
            "email": email,
            "page": page,
            "limit": limit,
        },
    ) as audit:
        try:
            out = _json(
                await _client().find_contacts(
                    first_name=first_name, last_name=last_name, email=email, page=page, limit=limit
                )
            )
            audit.set_result(out)
            return out
        except EzyvetError:
            raise


@mcp.tool()
async def create_contact(contact_json: str) -> str:
    """Create a new contact (owner). ``contact_json`` is a JSON object string."""
    with audit_tool_call("create_contact", {"contact_json": contact_json}) as audit:
        try:
            data = json.loads(contact_json)
            if not isinstance(data, dict):
                out = "contact_json must decode to a JSON object."
                audit.set_result(out)
                return out
            out = _json(await _client().create_contact(data))
            audit.set_result(out)
            return out
        except json.JSONDecodeError as e:
            out = f"Invalid JSON in contact_json: {e}"
            audit.set_result(out)
            return out
        except EzyvetError:
            raise


@mcp.tool()
async def find_appointments(
    animal_id: int | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    appointment_type_id: int | None = None,
    page: int = 1,
    limit: int = 50,
) -> str:
    """List appointments. Use ``start_date`` + ``end_date`` for a date range,
    ``animal_id`` for a specific patient's visits."""
    with audit_tool_call(
        "find_appointments",
        {
            "animal_id": animal_id,
            "start_date": start_date,
            "end_date": end_date,
            "appointment_type_id": appointment_type_id,
            "page": page,
            "limit": limit,
        },
    ) as audit:
        try:
            out = _json(
                await _client().find_appointments(
                    animal_id=animal_id,
                    start_date=start_date,
                    end_date=end_date,
                    appointment_type_id=appointment_type_id,
                    page=page,
                    limit=limit,
                )
            )
            audit.set_result(out)
            return out
        except EzyvetError:
            raise


@mcp.tool()
async def create_appointment(appointment_json: str) -> str:
    """Book a new appointment. ``appointment_json`` is a JSON object string."""
    with audit_tool_call("create_appointment", {"appointment_json": appointment_json}) as audit:
        try:
            data = json.loads(appointment_json)
            if not isinstance(data, dict):
                out = "appointment_json must decode to a JSON object."
                audit.set_result(out)
                return out
            out = _json(await _client().create_appointment(data))
            audit.set_result(out)
            return out
        except json.JSONDecodeError as e:
            out = f"Invalid JSON in appointment_json: {e}"
            audit.set_result(out)
            return out
        except EzyvetError:
            raise


@mcp.tool()
async def find_consults(
    animal_id: int | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    page: int = 1,
    limit: int = 50,
) -> str:
    """List clinical consults (visits)."""
    with audit_tool_call(
        "find_consults",
        {
            "animal_id": animal_id,
            "start_date": start_date,
            "end_date": end_date,
            "page": page,
            "limit": limit,
        },
    ) as audit:
        try:
            out = _json(
                await _client().find_consults(
                    animal_id=animal_id,
                    start_date=start_date,
                    end_date=end_date,
                    page=page,
                    limit=limit,
                )
            )
            audit.set_result(out)
            return out
        except EzyvetError:
            raise


@mcp.tool()
async def create_consult(consult_json: str) -> str:
    """Open a new clinical consult (visit record)."""
    with audit_tool_call("create_consult", {"consult_json": consult_json}) as audit:
        try:
            data = json.loads(consult_json)
            if not isinstance(data, dict):
                out = "consult_json must decode to a JSON object."
                audit.set_result(out)
                return out
            out = _json(await _client().create_consult(data))
            audit.set_result(out)
            return out
        except json.JSONDecodeError as e:
            out = f"Invalid JSON in consult_json: {e}"
            audit.set_result(out)
            return out
        except EzyvetError:
            raise


@mcp.tool()
async def find_invoices(
    contact_id: int | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    page: int = 1,
    limit: int = 50,
) -> str:
    """List invoices, optionally filtered by contact and date range."""
    with audit_tool_call(
        "find_invoices",
        {
            "contact_id": contact_id,
            "start_date": start_date,
            "end_date": end_date,
            "page": page,
            "limit": limit,
        },
    ) as audit:
        try:
            out = _json(
                await _client().find_invoices(
                    contact_id=contact_id,
                    start_date=start_date,
                    end_date=end_date,
                    page=page,
                    limit=limit,
                )
            )
            audit.set_result(out)
            return out
        except EzyvetError:
            raise


@mcp.tool()
async def list_species() -> str:
    """List animal species (dog, cat, rabbit, etc.)."""
    with audit_tool_call("list_species", {}) as audit:
        try:
            out = _json(await _client().list_species())
            audit.set_result(out)
            return out
        except EzyvetError:
            raise


@mcp.tool()
async def list_breeds() -> str:
    """List animal breeds."""
    with audit_tool_call("list_breeds", {}) as audit:
        try:
            out = _json(await _client().list_breeds())
            audit.set_result(out)
            return out
        except EzyvetError:
            raise


@mcp.tool()
async def list_appointment_types() -> str:
    """List appointment type definitions (consult, vaccination, surgery, etc.)."""
    with audit_tool_call("list_appointment_types", {}) as audit:
        try:
            out = _json(await _client().list_appointment_types())
            audit.set_result(out)
            return out
        except EzyvetError:
            raise


@mcp.tool()
async def list_users() -> str:
    """List practice users (vets, nurses, receptionists)."""
    with audit_tool_call("list_users", {}) as audit:
        try:
            out = _json(await _client().list_users())
            audit.set_result(out)
            return out
        except EzyvetError:
            raise


@mcp.tool()
async def health_check() -> str:
    """Verify credentials by minting an OAuth token and listing users."""
    with audit_tool_call("health_check", {}) as audit:
        try:
            await _client().list_users()
            out = _json({"status": "ok"})
            audit.set_result(out)
            return out
        except EzyvetError:
            raise


def main() -> None:
    try:
        mcp.run()
    except EzyvetAuthError as e:
        log.error("server.auth_failed_on_start", error=str(e))
        sys.exit(1)


if __name__ == "__main__":
    main()
