# MCP Server Setup

The Pokemon Agent exposes an MCP server so the CLI agent can control the emulator
directly as MCP tools, without the file-based agent loop.

The server uses **stdio transport**: CLI agent spawns a Docker container on-demand and
communicates over stdin/stdout. No persistent HTTP port is required.

## Quick Start

### 1. Start the emulator

```bash
docker compose up -d mgba
```

This starts mGBA headlessly via `Xvfb` inside the `mgba` container, loading your ROM
and the Lua socket server on port 5000 (internal to the Docker network).

Check it started:

```bash
docker compose logs mgba
```

### 2. Register with CLI Agent

Example for Claude Code:

```bash
claude mcp add pokemon-agent -- \
  docker compose -f /path/to/pokemon_agent/docker-compose.yml \
  run --rm --no-deps mcp-server uv run pokemon-mcp
```

Replace `/path/to/pokemon_agent` with the absolute path to this repository.

Verify:

```bash
claude mcp list
```

### 3. Use from CLI Agent

The CLI agent should spawn the `mcp-server` container automatically each session and communicates
via stdio. Example prompts:

- "Call `observe` to see what's on screen, then decide what to do next."
- "Press the A button 3 times, then call `observe` again."
- "Use `query_knowledge` to check if we've been to Oldale Town before."

For Claude Code, use `/mcp` inside a session to confirm `pokemon-agent` appears as connected.

---

## Available Tools

### Perception

| Tool | Parameters | Description |
| ------ | ------------ | ------------- |
| `observe` | — | **Primary tool** — game state text + screenshot image in one call |
| `get_observation` | — | Text-only game state; use only when image is not needed |
| `get_extended_state` | — | Bag items and PC box occupancy |

### Actions

| Tool | Parameters | Description |
| ------ | ------------ | ------------- |
| `press_button` | `button: str`, `duration_frames: int = 8` | Hold a single GBA button |
| `press_buttons` | `buttons: list[str]`, `duration_frames: int = 8` | Hold multiple buttons simultaneously |
| `wait` | `frames: int = 60` | Idle for N emulated frames (~60 fps) |
| `execute_sequence` | `steps: list[dict]` | Run a multi-step input sequence |

### Knowledge base

| Tool | Parameters | Description |
| ------ | ------------ | ------------- |
| `query_knowledge` | `query: str`, `limit: int = 5` | Search discoveries by keyword |
| `record_discovery` | `category`, `title`, `description`, `map_id?`, `x?`, `y?`, `metadata?` | Save a game discovery |
| `record_progress` | `event_type`, `event_name`, `details?` | Log a milestone (`badge`/`capture`/`evolution`/`milestone`) |
| `record_strategy` | `situation`, `approach`, `outcome?`, `effectiveness?` | Save a battle or navigation strategy |
| `record_pokemon` | `species_id`, `species_name`, `type_primary?`, `type_secondary?`, `notes?` | Store species knowledge |
| `add_guidance` | `instruction`, `context?`, `priority?` | Add an instruction surfaced in every `observe` |
| `update_guidance_status` | `guidance_id: int`, `status: str` | Mark guidance `completed` or `superseded` |

### Using `observe` (recommended workflow)

`observe` returns a multimodal response: structured game state text followed by the current
screenshot image. Both arrive in a single tool call, ensuring every decision is grounded in
what is actually on screen.

```text
# Recommended loop
1. Call observe() — receive text state + screenshot image together
2. Decide what to do based on both the coordinates AND what you see
3. Call press_button / execute_sequence / wait
4. Go to step 1
```

The screenshot is captured via the Lua `emu:screenshot()` handler — no external tools
required. In the on-demand MCP workflow the brief PNG-write pause is imperceptible
between decision cycles.

### `execute_sequence` step format

Each step dict must have an `action` field:

```json
[
  {"action": "press_button", "button": "UP",   "duration_frames": 16},
  {"action": "wait",         "wait_frames": 20},
  {"action": "press_button", "button": "UP",   "duration_frames": 16},
  {"action": "wait",         "wait_frames": 20}
]
```

Valid `action` values: `press_button`, `press_buttons`, `wait`.

---

## Accessing Screenshots

Screenshots are written to the `screenshots` Docker volume, mounted at
`/data/screenshots` in both containers.

To copy a screenshot to the host:

```bash
docker compose cp mgba:/data/screenshots/mcp_<timestamp>.png .
```

Or bind-mount the volume to a host path in `docker-compose.yml`:

```yaml
volumes:
  screenshots:
    driver: local
    driver_opts:
      type: none
      o: bind
      device: /tmp/pokemon-screenshots
```

---

## Environment Variables

| Variable | Default | Description |
| ---------- | --------- | ------------- |
| `MGBA_HOST` | `127.0.0.1` | Hostname of the mGBA Lua server |
| `MGBA_PORT` | `5000` | Port of the mGBA Lua server |
| `SCREENSHOT_DIR` | `/data/screenshots` | Directory for saved screenshots |
| `KB_PATH` | `/data/knowledge/pokemon_knowledge.db` | Path to the SQLite knowledge base |

---

## Running Without Docker (local development)

```bash
# mGBA must already be running with the Lua script loaded
MGBA_HOST=127.0.0.1 uv run pokemon-mcp
```

Then register with Claude Code:

```bash
claude mcp add pokemon-agent -- \
  env MGBA_HOST=127.0.0.1 uv --directory /path/to/pokemon_agent run pokemon-mcp
```

---

## Stopping

```bash
docker compose down
```

The `knowledge` volume persists the SQLite database across restarts.
To reset the knowledge base: `docker compose down -v`.
