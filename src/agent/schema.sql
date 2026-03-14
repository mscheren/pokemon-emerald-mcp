-- Pokemon AI Agent Knowledge Base Schema

CREATE TABLE IF NOT EXISTS discoveries (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    category    TEXT NOT NULL CHECK(category IN ('location','item','npc','mechanic','strategy','pokemon')),
    title       TEXT NOT NULL,
    description TEXT NOT NULL,
    map_id      INTEGER,
    x_coord     INTEGER,
    y_coord     INTEGER,
    metadata    TEXT,  -- JSON blob for flexible extra data
    timestamp   DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_discoveries_category ON discoveries(category);
CREATE INDEX IF NOT EXISTS idx_discoveries_map ON discoveries(map_id);

CREATE TABLE IF NOT EXISTS user_guidance (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    instruction TEXT NOT NULL,
    context     TEXT DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'active'
                CHECK(status IN ('active','completed','superseded')),
    priority    INTEGER DEFAULT 0,
    timestamp   DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_guidance_status ON user_guidance(status);

CREATE TABLE IF NOT EXISTS strategies (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    situation      TEXT NOT NULL,
    approach       TEXT NOT NULL,
    outcome        TEXT DEFAULT '',
    effectiveness  INTEGER DEFAULT 0 CHECK(effectiveness BETWEEN 0 AND 5),
    timestamp      DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_strategies_situation ON strategies(situation);

CREATE TABLE IF NOT EXISTS pokemon_knowledge (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    species_id        INTEGER NOT NULL,
    species_name      TEXT NOT NULL,
    type_primary      TEXT,
    type_secondary    TEXT,
    notes             TEXT,
    first_encountered DATETIME DEFAULT CURRENT_TIMESTAMP,
    last_seen         DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(species_id)
);

CREATE INDEX IF NOT EXISTS idx_pokemon_species ON pokemon_knowledge(species_id);

CREATE TABLE IF NOT EXISTS progress (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type  TEXT NOT NULL CHECK(event_type IN ('badge','capture','milestone','evolution')),
    event_name  TEXT NOT NULL,
    details     TEXT DEFAULT '',
    timestamp   DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_progress_type ON progress(event_type);

CREATE TABLE IF NOT EXISTS pokeapi_cache (
    cache_key  TEXT PRIMARY KEY,
    data       TEXT NOT NULL,
    timestamp  DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS map_tiles (
    map_id    INTEGER NOT NULL,
    x         INTEGER NOT NULL,
    y         INTEGER NOT NULL,
    tile_type TEXT NOT NULL CHECK(tile_type IN (
                  'passable','blocked',
                  'ledge_south','ledge_north','ledge_west','ledge_east',
                  'grass','water','npc','item',
                  'rock_smash','rock_strength','tree_cut',
                  'unknown'
              )),
    notes     TEXT,  -- optional annotation, e.g. NPC name, item name
    PRIMARY KEY (map_id, x, y)
);
CREATE INDEX IF NOT EXISTS idx_map_tiles_map_id ON map_tiles(map_id);
