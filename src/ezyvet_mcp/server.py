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

from mcp.server.fastmcp import FastMCP

from .client import EzyvetAPIError, EzyvetAuthError, EzyvetClient, EzyvetError


def _format_error(e: Exception) -> str:
    if isinstance(e, EzyvetAuthError):
        return (
            "Authentication failed against ezyVet. Check EZYVET_PARTNER_ID / "
            f"EZYVET_CLIENT_ID / EZYVET_CLIENT_SECRET / EZYVET_SITE_UID / EZYVET_SCOPE. Details: {e}"
        )
    if isinstance(e, EzyvetAPIError):
        return f"ezyVet API error (HTTP {e.status_code}): {e}"
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


# ----- Animal tools ---------------------------------------------------------


@mcp.tool()
async def get_animal(animal_id: int) -> str:
    """Fetch a single animal (patient) by ezyVet ID."""
    try:
        return _json(await _client().get_animal(animal_id))
    except EzyvetError as e:
        return _format_error(e)


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
    try:
        return _json(await _client().find_animals(
            name=name, species_id=species_id, breed_id=breed_id,
            page=page, limit=limit,
        ))
    except EzyvetError as e:
        return _format_error(e)


@mcp.tool()
async def create_animal(animal_json: str) -> str:
    """Create a new animal (patient). ``animal_json`` is a JSON object string
    matching ezyVet's animal schema (at minimum: name, species_id)."""
    try:
        data = json.loads(animal_json)
        if not isinstance(data, dict):
            return "animal_json must decode to a JSON object."
        return _json(await _client().create_animal(data))
    except json.JSONDecodeError as e:
        return f"Invalid JSON in animal_json: {e}"
    except EzyvetError as e:
        return _format_error(e)


@mcp.tool()
async def update_animal(animal_id: int, updates_json: str) -> str:
    """Patch an animal record. ``updates_json`` is a JSON object with only the
    fields you want to change (e.g. ``'{"name": "Rex II", "weight": 32.4}'``)."""
    try:
        updates = json.loads(updates_json)
        if not isinstance(updates, dict):
            return "updates_json must decode to a JSON object."
        return _json(await _client().update_animal(animal_id, updates))
    except json.JSONDecodeError as e:
        return f"Invalid JSON in updates_json: {e}"
    except EzyvetError as e:
        return _format_error(e)


# ----- Contact tools --------------------------------------------------------


@mcp.tool()
async def get_contact(contact_id: int) -> str:
    """Fetch a contact (the pet owner) by ID."""
    try:
        return _json(await _client().get_contact(contact_id))
    except EzyvetError as e:
        return _format_error(e)


@mcp.tool()
async def find_contacts(
    first_name: str | None = None,
    last_name: str | None = None,
    email: str | None = None,
    page: int = 1,
    limit: int = 50,
) -> str:
    """Search contacts by name or email."""
    try:
        return _json(await _client().find_contacts(
            first_name=first_name, last_name=last_name, email=email,
            page=page, limit=limit,
        ))
    except EzyvetError as e:
        return _format_error(e)


@mcp.tool()
async def create_contact(contact_json: str) -> str:
    """Create a new contact (owner). ``contact_json`` is a JSON object string."""
    try:
        data = json.loads(contact_json)
        if not isinstance(data, dict):
            return "contact_json must decode to a JSON object."
        return _json(await _client().create_contact(data))
    except json.JSONDecodeError as e:
        return f"Invalid JSON in contact_json: {e}"
    except EzyvetError as e:
        return _format_error(e)


# ----- Appointment tools ---------------------------------------------------


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
    try:
        return _json(await _client().find_appointments(
            animal_id=animal_id, start_date=start_date, end_date=end_date,
            appointment_type_id=appointment_type_id, page=page, limit=limit,
        ))
    except EzyvetError as e:
        return _format_error(e)


@mcp.tool()
async def create_appointment(appointment_json: str) -> str:
    """Book a new appointment. ``appointment_json`` is a JSON object string."""
    try:
        data = json.loads(appointment_json)
        if not isinstance(data, dict):
            return "appointment_json must decode to a JSON object."
        return _json(await _client().create_appointment(data))
    except json.JSONDecodeError as e:
        return f"Invalid JSON in appointment_json: {e}"
    except EzyvetError as e:
        return _format_error(e)


# ----- Consult tools -------------------------------------------------------


@mcp.tool()
async def find_consults(
    animal_id: int | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    page: int = 1,
    limit: int = 50,
) -> str:
    """List clinical consults (visits)."""
    try:
        return _json(await _client().find_consults(
            animal_id=animal_id, start_date=start_date, end_date=end_date,
            page=page, limit=limit,
        ))
    except EzyvetError as e:
        return _format_error(e)


@mcp.tool()
async def create_consult(consult_json: str) -> str:
    """Open a new clinical consult (visit record)."""
    try:
        data = json.loads(consult_json)
        if not isinstance(data, dict):
            return "consult_json must decode to a JSON object."
        return _json(await _client().create_consult(data))
    except json.JSONDecodeError as e:
        return f"Invalid JSON in consult_json: {e}"
    except EzyvetError as e:
        return _format_error(e)


# ----- Invoice tools -------------------------------------------------------


@mcp.tool()
async def find_invoices(
    contact_id: int | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    page: int = 1,
    limit: int = 50,
) -> str:
    """List invoices, optionally filtered by contact and date range."""
    try:
        return _json(await _client().find_invoices(
            contact_id=contact_id, start_date=start_date, end_date=end_date,
            page=page, limit=limit,
        ))
    except EzyvetError as e:
        return _format_error(e)


# ----- Reference data tools ------------------------------------------------


@mcp.tool()
async def list_species() -> str:
    """List animal species (dog, cat, rabbit, etc.)."""
    try:
        return _json(await _client().list_species())
    except EzyvetError as e:
        return _format_error(e)


@mcp.tool()
async def list_breeds() -> str:
    """List animal breeds."""
    try:
        return _json(await _client().list_breeds())
    except EzyvetError as e:
        return _format_error(e)


@mcp.tool()
async def list_appointment_types() -> str:
    """List appointment type definitions (consult, vaccination, surgery, etc.)."""
    try:
        return _json(await _client().list_appointment_types())
    except EzyvetError as e:
        return _format_error(e)


@mcp.tool()
async def list_users() -> str:
    """List practice users (vets, nurses, receptionists)."""
    try:
        return _json(await _client().list_users())
    except EzyvetError as e:
        return _format_error(e)


@mcp.tool()
async def health_check() -> str:
    """Verify credentials by minting an OAuth token and listing users."""
    try:
        await _client().list_users()
        return _json({"status": "ok"})
    except EzyvetError as e:
        return _format_error(e)


def main() -> None:
    try:
        mcp.run()
    except EzyvetAuthError as e:
        print(f"[ezyvet-mcp] {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()