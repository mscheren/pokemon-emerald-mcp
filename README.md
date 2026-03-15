# Pokemon Emerald AI Agent

An agent interface for playing Pokemon Emerald. A CLI agent can control the game through an MCP
server that connects to a live mGBA emulator instance.

---

## Setup

### Prerequisites

- Installation of mGBA
- For fully automated process:
  mGBA built from source (0.11-dev). See [`docs/building-mgba.md`](./docs/building-mgba.md). Binary expected at `~/mgba/build/qt/mgba-qt`.
- Python 3.12+ and [uv](https://docs.astral.sh/uv/)
- A compiled copy of Pokemon Emerald.

Start by installing the dependencies.

```bash
uv sync
```

---

## Playing the Game

### Step 1 — Start mGBA

```bash
uv run pokemon-mgba
```

Launches mGBA with the Lua agent script attached on port 5000. Load your save file or start a new one and
get into the game world before starting the MCP server.

> **Important**: the Lua server only accepts one client at a time. Do **not** run
> `uv run pokemon-agent` at the same time as the MCP server — they will compete for
> the connection.

Alternatively, you may run the mGBA application manually, then load both the ROM and the [Pokemon agent lua script](./src/lua_scripts/pokemon_agent.lua).

### Stopping a session

If the MCP server needs a clean restart, run from a separate terminal:

```bash
uv run pokemon-mcp-admin
```

Sends SIGTERM to any running `pokemon-mcp` process. Agents like Claude Code will restart it automatically
on the next tool call.

---

### Step 2 — Register the MCP server with your CLI agent

Example for Claude Code:

```bash
export AGENT_PATH=/path/to/this/repo
claude mcp add pokemon-agent -- uv --directory $AGENT_PATH run pokemon-mcp
```

Note: The environment variables use the following default values. If these need to be different, include them in the MCP add command.

```text
MGBA_HOST      — mGBA Lua socket host (default: 127.0.0.1)
MGBA_PORT      — mGBA Lua socket port (default: 5000)
SCREENSHOT_DIR — screenshot output directory (default: data/screenshots)
KB_PATH        — SQLite knowledge-base path (default: data/knowledge/pokemon_knowledge.db)
```

Verify with `claude mcp list`.

This is a one time step on Claude Code.

### Step 3 — Tell your agent to play

Open a session in your agent's CLI and type:

> Play Pokemon Emerald using the pokemon-agent MCP server.

---

## Agent Interaction Loop

Each turn:

1. Call **`observe`** — returns game state text and the current screenshot image together in one response
2. Look at the screenshot before acting — coordinates alone are not enough
3. Call the appropriate action tool (`press_button`, `execute_sequence`, etc.)
4. Go to step 1

The screenshot arrives inline as an image block in the `observe` response. No separate file read is needed.

For available MCP tools, see the [MCP setup documentation](./docs/mcp-setup.md).

---

## Architecture

```text
CLI agent
  └─ MCP server (src/agent/mcp_server.py)
        ├─ MGBAClient <── TCP/JSON :5000 ──> Lua script (src/lua_scripts/)
        ├─ GameController ──> data/screenshots/
        └─ KnowledgeBase <── aiosqlite ──→ data/knowledge/pokemon_knowledge.db
```

The Lua script binds on port 5000, handles memory reads and input injection inside
the running mGBA process, and writes PNG screenshots on demand.

---

## Development

```bash
uv sync                       # install dependencies
uv run pytest                 # run all tests
uv run pytest tests/unit/     # unit tests only
```

---

## WSL2 / mGBA Notes

- **mGBA binary**: Must be built from source (0.11-dev). See [`docs/building-mgba.md`](./docs/building-mgba.md).
  Binary expected at `~/mgba/build/qt/mgba-qt`. The packaged 0.10.x SDL build does not
  support `--script`.
- **Fullscreen**: `controller.py` patches `~/.config/mgba/config.ini` to force
  `fullscreen=0` on startup to prevent WSL2 window freeze.
- **Host**: Use `127.0.0.1` not `localhost` to avoid IPv6 resolution issues in WSL2.
- Play on native system (not subsystem) for optimal gameplay flow.

---

## Performance & Audio Tuning

### Audio crunchiness under WSLg

mGBA running under WSLg uses PulseAudio/PipeWire with higher latency than the native
Windows audio stack, causing audible crunchiness. Two options:

**Option A — Mute (recommended for agent use):**

```bash
uv run pokemon-mgba --mute
```

Sets `mute=1` in `~/.config/mgba/config.ini` before launch. The agent does not need
audio — this completely eliminates crunchiness with no gameplay impact.

**Option B — Larger audio buffer:**

The agent automatically sets `audioBuffers=8192`. If crunchiness persists you can try
`audioBuffers=16384` by editing `~/.config/mgba/config.ini` directly.

---

### Running mGBA natively on Windows (best performance)

For best audio and rendering quality, run mGBA on Windows (or your native system in general) and
connect the agent from WSL2 over TCP.

**Step 1 — Start mGBA on Windows** with the Lua script loaded:

1. Use the mGBA Windows release from [mgba.io](https://mgba.io)
2. Open the ROM: `File → Open`
3. Load the Lua script: `Tools → Scripting → Load script → src/lua_scripts/pokemon_agent.lua`
4. The Lua server starts listening on TCP port 5000

**Step 2 — Allow WSL2 through Windows Firewall:**

Add an inbound rule for TCP port 5000 from the WSL2 subnet (typically `172.16.0.0/12`).
This should be done automatically on loading the script if the rule does not exist already.

**Step 3 — Connect the agent from WSL2:**

```bash
# Auto-detect Windows host IP from /etc/resolv.conf
uv run pokemon-agent --discover-host

# Or specify the IP manually
uv run pokemon-agent --no-launch --host 172.29.192.1
```
