# Building mGBA Qt from Source

The Pokemon Agent requires the **mGBA Qt frontend** built from the 0.11-dev source.
The packaged `mgba` (0.10.x SDL build) does not support loading Lua scripts from the
command line — its `-l` flag means `--log-level`, not script loading. The Qt frontend
built from source adds `--script FILE` which is essential for the controller to launch
mGBA with the agent Lua script automatically.

## Prerequisites

The mGBA source is expected at `~/mgba`. If it isn't there, clone it:

```bash
git clone https://github.com/mgba-emu/mgba.git ~/mgba
```

Install build dependencies (Ubuntu/Debian):

```bash
sudo apt-get install -y cmake ninja-build qt6-base-dev qt6-multimedia-dev \
  libsdl2-dev liblua5.4-dev libpng-dev zlib1g-dev libzip-dev libepoxy-dev libelf-dev
```

> **Note:** The `libzip-dev` package may have a broken cmake config on some Ubuntu
> versions (missing `/usr/bin/zipcmp`). The build disables libzip support with
> `-DUSE_LIBZIP=OFF` to work around this; it has no impact on scripting functionality.

## Build

```bash
mkdir -p ~/mgba/build
cd ~/mgba/build
cmake .. \
  -G Ninja \
  -DCMAKE_BUILD_TYPE=Release \
  -DBUILD_QT=ON \
  -DBUILD_SDL=OFF \
  -DUSE_LUA=ON \
  -DUSE_LIBZIP=OFF \
  -DCMAKE_INSTALL_PREFIX=/usr/local
ninja -j$(nproc)
```

The binary is written to `~/mgba/build/qt/mgba-qt`.

Build time is roughly 5–10 minutes on a modern machine with all cores (`-j$(nproc)`).

## Verify

Confirm the `--script` flag is present:

```bash
~/mgba/build/qt/mgba-qt --help | grep script
# Expected output:
#   --script FILE  Run a script on start. Can be passed multiple times
```

## Running mGBA with the Agent Script

```bash
~/mgba/build/qt/mgba-qt \
  --script /path/to/pokemon_agent/src/lua_scripts/pokemon_agent.lua \
  /path/to/pokeemerald.gba
```

The mGBA scripting console (Tools → Scripting) should show:

```
[PokemonAgent] Listening on port 5000
```

once the ROM has loaded and the Lua script has initialised.

## Updating the Build

When pulling new changes to `~/mgba`, rebuild with:

```bash
cd ~/mgba/build && ninja -j$(nproc)
```

No need to re-run cmake unless `CMakeLists.txt` has changed.
