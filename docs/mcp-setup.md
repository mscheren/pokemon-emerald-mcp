# MCP Server Setup

The Pokemon Agent exposes an MCP server so the CLI agent can control the emulator
directly as MCP tools, without the file-based agent loop.

The server uses **stdio transport**: CLI agent spawns a Docker container on-demand and
communicates over stdin/stdout.

## Available Tools

### Perception

| Tool | Parameters | Description |
| ------ | ------------ | ------------- |
| `observe` | — | **Primary tool** — game state text + screenshot image in one call |
| `get_extended_state` | — | Bag items and PC box occupancy |

### Actions

| Tool | Parameters | Description |
| ------ | ------------ | ------------- |
| `press_button` | `button: str`, `duration_frames: int = 8` | Hold a single GBA button |
| `press_buttons` | `buttons: list[str]`, `duration_frames: int = 8` | Hold multiple buttons simultaneously |
| `wait` | `frames: int = 60` | Idle for N emulated frames (~60 fps) |
| `execute_sequence` | `steps: list[dict]` | Run a multi-step input sequence |

### Knowledge base — read

| Tool | Parameters | Description |
| ------ | ------------ | ------------- |
| `query_knowledge` | `query: str`, `limit: int = 5` | Search discoveries by keyword |
| `search_strategies` | `keyword: str`, `limit: int = 5` | Search strategies by keyword |
| `get_pokemon_info` | `species_id: int` | Retrieve stored species knowledge |
| `get_active_guidance` | — | List all active guidance instructions |
| `get_progress_summary` | — | Summarise badges, captures, evolutions, milestones |
| `get_map_tiles` | `map_id: int` | Retrieve all recorded tiles for a map |

### Knowledge base — write

| Tool | Parameters | Description |
| ------ | ------------ | ------------- |
| `record_discovery` | `category`, `title`, `description`, `map_id?`, `x?`, `y?`, `metadata?` | Save a game discovery |
| `record_progress` | `event_type`, `event_name`, `details?` | Log a milestone (`badge`/`capture`/`evolution`/`milestone`) |
| `record_strategy` | `situation`, `approach`, `outcome?`, `effectiveness?` | Save a battle or navigation strategy |
| `record_pokemon` | `species_id`, `species_name`, `type_primary?`, `type_secondary?`, `notes?` | Store species knowledge |
| `add_guidance` | `instruction`, `context?`, `priority?` | Add an instruction surfaced in every `observe` |
| `update_guidance_status` | `guidance_id: int`, `status: str` | Mark guidance `completed` or `superseded` |
| `record_tile` | `map_id: int`, `x: int`, `y: int`, `tile_type: str`, `notes?: str` | Record terrain type at a map coordinate |

### PokeAPI (cached)

| Tool | Parameters | Description |
| ------ | ------------ | ------------- |
| `lookup_pokemon` | `species_id: int` | Types, base stats, and evolution chain |
| `lookup_move` | `move_id: int` | Move name, type, power, accuracy, PP |
| `lookup_item` | `item_id: int` | Item name, category, and effect |

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

## Environment Variables

| Variable | Default | Description |
| ---------- | --------- | ------------- |
| `MGBA_HOST` | `127.0.0.1` | Hostname of the mGBA Lua server |
| `MGBA_PORT` | `5000` | Port of the mGBA Lua server |
| `SCREENSHOT_DIR` | `/data/screenshots` | Directory for saved screenshots |
| `KB_PATH` | `/data/knowledge/pokemon_knowledge.db` | Path to the SQLite knowledge base |

---

## Running The Server

```bash
# mGBA must already be running with the Lua script loaded
uv run pokemon-mcp
```

Then register with Claude Code or any other CLI agent:

```bash
claude mcp add pokemon-agent -- \
  env ... uv --directory /path/to/pokemon_agent run pokemon-mcp
```

Verify:

```bash
claude mcp list
```

---

### Use from CLI Agent

The CLI agent should spawn the `mcp-server` container automatically each session and communicates
via stdio. Example prompts:

- "Play Pokemon Emerald using the pokemon_agent MCP server. Try to beat the game!"
- "Call `observe` to see what's on screen, then decide what to do next."
- "Press the A button 3 times, then call `observe` again."
- "Use `query_knowledge` to check if we've been to Oldale Town before."

For Claude Code, use `/mcp` inside a session to confirm `pokemon-agent` appears as connected.
