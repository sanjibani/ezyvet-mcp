# ezyVet MCP

**Model Context Protocol (MCP) server for [ezyVet](https://www.ezyvet.com/)** — cloud-based veterinary practice management software.

Talk to ezyVet from Claude, Cursor, or any MCP client. Read animals (patients), contacts (owners), appointments, consults, invoices. Create new records. Tokens are OAuth2 client-credentials with 12-hour TTL, auto-refreshed.

Built against the [ezyVet REST API](https://developers.ezyvet.com/). No existing MCP for ezyVet — this is the first.

## What you can do with it

```
You:   "Find dog 'Rex' for owner Sarah Johnson and show upcoming appointments."
Claude: *find_contacts + find_animals + find_appointments → summary*

You:   "Book a vaccination appointment for animal 4523 next Tuesday at 10am."
Claude: *list_appointment_types, then create_appointment with right type_id*

You:   "Open a new consult for the cat we just saw — note the diagnosis."
Claude: *create_consult with animal_id, vet_id, notes*

You:   "What invoices does contact 892 have from this month?"
Claude: *find_invoices with contact_id + date range*
```

## Install

```bash
pip install -e .
```

## Configure

Register as an [ezyVet integration partner](https://www.ezyvet.com/become-a-partner) to get your credentials.

```bash
export EZYVET_PARTNER_ID="..."
export EZYVET_CLIENT_ID="..."
export EZYVET_CLIENT_SECRET="..."
export EZYVET_SITE_UID="..."
export EZYVET_SCOPE="read-animal read-contact read-appointment read-consult read-invoice read-user"
```

## Use with Claude Desktop

```json
{
  "mcpServers": {
    "ezyvet_mcp": {
      "command": "ezyvet_mcp",
      "env": {
        "EZYVET_PARTNER_ID": "...",
        "EZYVET_CLIENT_ID": "...",
        "EZYVET_CLIENT_SECRET": "...",
        "EZYVET_SITE_UID": "...",
        "EZYVET_SCOPE": "..."
      }
    }
  }
}
```

## Tools

| Tool | Type | What it does |
| --- | --- | --- |
| `health_check` | Diagnostic | Mints a token + lists users |
| `get_animal` | Read | Single animal (patient) |
| `find_animals` | Read | Search animals |
| `create_animal` | Write | New patient |
| `update_animal` | Write | Patch patient fields |
| `get_contact` | Read | Single contact (owner) |
| `find_contacts` | Read | Search owners |
| `create_contact` | Write | New owner |
| `find_appointments` | Read | List appointments |
| `create_appointment` | Write | Book appointment |
| `find_consults` | Read | List visits |
| `create_consult` | Write | Open visit |
| `find_invoices` | Read | List invoices |
| `list_species` | Read | Reference: species |
| `list_breeds` | Read | Reference: breeds |
| `list_appointment_types` | Read | Reference: appointment types |
| `list_users` | Read | Reference: practice staff |

## API coverage

Maps MCP tools to ezyVet's 216-endpoint REST API. Full docs: <https://developers.ezyvet.com/>

## Rate limits

ezyVet throttles most endpoints at 60 req/min and globally at 180 req/min per database. The client auto-retries 401s (token refresh) but doesn't retry 429s — slow down on your end if you hit them.

## Development

```bash
pip install -e ".[dev]"
pytest
ezyvet_mcp
```

## License

MIT.

## Acknowledgements

- ezyVet for the public REST API + OAuth flow
- Built using [mcp-vertical-template](https://github.com/sanjibani/mcp-vertical-template)
- Inspired by [sanjibani/hawksoft-mcp](https://github.com/sanjibani/hawksoft-mcp) and [sanjibani/open-dental-mcp](https://github.com/sanjibani/open-dental-mcp) (same template pattern)

## See also

- [ezyVet API docs](https://developers.ezyvet.com/)
- [ezyVet integration partner program](https://www.ezyvet.com/become-a-partner)
- [Model Context Protocol](https://modelcontextprotocol.io)
- [More vertical MCPs from sanjibani](https://github.com/sanjibani?q=-mcp)