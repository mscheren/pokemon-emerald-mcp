"""CLI entry point for the Pokemon Emerald AI Agent."""

import argparse
import asyncio
import logging
import re
import subprocess
import sys
from pathlib import Path

from .controller import MGBA_BINARY, PokemonAgentController, _configure_mgba


def _discover_windows_host() -> str:
    """Return the Windows host IP as seen from WSL2 via /etc/resolv.conf.

    Reads the nameserver line that WSL2 writes into /etc/resolv.conf.
    Falls back to 127.0.0.1 if the file is absent or unparseable.
    """
    resolv = Path("/etc/resolv.conf")
    if resolv.exists():
        for line in resolv.read_text(encoding="utf-8").splitlines():
            m = re.match(r"^\s*nameserver\s+(\S+)", line)
            if m:
                return m.group(1)
    return "127.0.0.1"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Pokemon Emerald AI Agent")
    parser.add_argument(
        "--rom",
        type=Path,
        help="Path to the Pokemon Emerald ROM file",
    )
    parser.add_argument(
        "--lua-script",
        type=Path,
        default=Path("src/lua_scripts/pokemon_agent.lua"),
        help="Path to the mGBA Lua script",
    )
    parser.add_argument(
        "--knowledge-db",
        type=Path,
        default=Path("data/knowledge/pokemon_knowledge.db"),
        help="Path to the SQLite knowledge database",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="mGBA socket host (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=5000,
        help="mGBA socket port (default: 5000)",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level",
    )
    parser.add_argument(
        "--no-launch",
        action="store_true",
        default=False,
        help=(
            "Skip launching mGBA — connect to an already-running instance on host:port. "
            "Start mGBA manually (e.g. on Windows) before running this command."
        ),
    )
    parser.add_argument(
        "--discover-host",
        action="store_true",
        default=False,
        help=(
            "Auto-detect the Windows host IP from /etc/resolv.conf (WSL2 nameserver). "
            "Implies --no-launch. Useful when mGBA runs natively on Windows."
        ),
    )
    parser.add_argument(
        "--mute",
        action="store_true",
        default=False,
        help=(
            "Set mute=1 in the mGBA config before launch to disable audio. "
            "Eliminates audio crunchiness under WSLg with no effect on gameplay."
        ),
    )
    parser.add_argument(
        "--screenshot-interval",
        type=int,
        default=1,
        metavar="N",
        help=(
            "Capture a screenshot every N iterations (default: 1 = every iteration). "
            "Increase to reduce emu:screenshot() overhead in the mGBA frame callback. "
        ),
    )
    return parser.parse_args()


def main() -> None:
    """Entry point for the ``pokemon-agent`` CLI command."""
    args = parse_args()

    # Force line-buffered stdout so print() output appears immediately even
    # when stdout is redirected to a file.
    sys.stdout.reconfigure(line_buffering=True)

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    # --discover-host auto-detects the Windows host IP and implies --no-launch
    host = args.host
    no_launch = args.no_launch
    if args.discover_host:
        host = _discover_windows_host()
        no_launch = True
        print(f"[Agent] Discovered Windows host: {host}")

    if not no_launch:
        if not args.rom.exists():
            print(f"ERROR: ROM not found: {args.rom}")
            raise SystemExit(1)
        if not args.lua_script.exists():
            print(f"ERROR: Lua script not found: {args.lua_script}")
            raise SystemExit(1)

    controller = PokemonAgentController(
        rom_path=args.rom,
        lua_script_path=args.lua_script,
        knowledge_db_path=args.knowledge_db,
        host=host,
        port=args.port,
        launch_mgba=not no_launch,
        screenshot_interval=args.screenshot_interval,
    )

    try:
        asyncio.run(controller.start(mute=args.mute))
    except KeyboardInterrupt:
        pass  # handled by signal handler


def launch_mgba() -> None:
    """Entry point for the ``pokemon-mgba`` command.

    Launches mGBA with the Lua script so the user can load a save and reach a
    desired game state before starting the agent loop with ``pokemon-agent --no-launch``.
    """
    parser = argparse.ArgumentParser(description="Launch mGBA for use with the Pokemon Emerald AI Agent")
    parser.add_argument(
        "--rom",
        type=Path,
        help="Path to the Pokemon Emerald ROM file",
    )
    parser.add_argument(
        "--lua-script",
        type=Path,
        default=Path("src/lua_scripts/pokemon_agent.lua"),
        help="Path to the mGBA Lua script",
    )
    parser.add_argument(
        "--mute",
        action="store_true",
        default=False,
        help="Set mute=1 in mGBA config before launch to disable audio output.",
    )
    args = parser.parse_args()

    if not args.rom.exists():
        print(f"ERROR: ROM not found: {args.rom}")
        raise SystemExit(1)
    if not args.lua_script.exists():
        print(f"ERROR: Lua script not found: {args.lua_script}")
        raise SystemExit(1)
    if not MGBA_BINARY.exists():
        print(f"ERROR: mGBA binary not found: {MGBA_BINARY}")
        raise SystemExit(1)

    _configure_mgba(mute=args.mute)
    cmd = [str(MGBA_BINARY), "--script", str(args.lua_script), str(args.rom)]
    print(f"Launching mGBA: {' '.join(cmd)}")
    print("Load your save, then run: pokemon-agent --no-launch")
    subprocess.run(cmd, check=False)


if __name__ == "__main__":
    main()
