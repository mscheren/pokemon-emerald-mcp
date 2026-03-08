-- memory.lua — GBA memory reading for Pokemon Emerald (vanilla/pret decompilation)
-- Reads party Pokemon, player position, badges, and battle state from live memory.
-- All addresses verified against the pret/pokeemerald decompilation.

local memory = {}
local utils = require('utils')

-- Known memory addresses for Pokemon Emerald (vanilla/pret decompilation)
local ADDR_PARTY_COUNT    = 0x020244E9
local ADDR_PARTY_BASE     = 0x020244EC
local PARTY_SLOT_SIZE     = 100
local ADDR_SAVBLOCK1_PTR  = 0x03005D8C  -- pointer to SaveBlock1 (bag pockets live here)
local ADDR_SAVBLOCK2_PTR  = 0x03005D90  -- pointer to SaveBlock2 (encryptionKey at +0xAC)
local ADDR_PC_STORAGE_PTR = 0x03005D94  -- pointer to gPokemonStorage (PC boxes)

-- GBA character encoding table (Pokemon charset)
local CHARSET = {
    [0xBB] = 'A', [0xBC] = 'B', [0xBD] = 'C', [0xBE] = 'D', [0xBF] = 'E',
    [0xC0] = 'F', [0xC1] = 'G', [0xC2] = 'H', [0xC3] = 'I', [0xC4] = 'J',
    [0xC5] = 'K', [0xC6] = 'L', [0xC7] = 'M', [0xC8] = 'N', [0xC9] = 'O',
    [0xCA] = 'P', [0xCB] = 'Q', [0xCC] = 'R', [0xCD] = 'S', [0xCE] = 'T',
    [0xCF] = 'U', [0xD0] = 'V', [0xD1] = 'W', [0xD2] = 'X', [0xD3] = 'Y',
    [0xD4] = 'Z',
    [0xD5] = 'a', [0xD6] = 'b', [0xD7] = 'c', [0xD8] = 'd', [0xD9] = 'e',
    [0xDA] = 'f', [0xDB] = 'g', [0xDC] = 'h', [0xDD] = 'i', [0xDE] = 'j',
    [0xDF] = 'k', [0xE0] = 'l', [0xE1] = 'm', [0xE2] = 'n', [0xE3] = 'o',
    [0xE4] = 'p', [0xE5] = 'q', [0xE6] = 'r', [0xE7] = 's', [0xE8] = 't',
    [0xE9] = 'u', [0xEA] = 'v', [0xEB] = 'w', [0xEC] = 'x', [0xED] = 'y',
    [0xEE] = 'z',
    [0xA1] = '0', [0xA2] = '1', [0xA3] = '2', [0xA4] = '3', [0xA5] = '4',
    [0xA6] = '5', [0xA7] = '6', [0xA8] = '7', [0xA9] = '8', [0xAA] = '9',
    [0x00] = ' ',
}
local CHAR_TERMINATOR = 0xFF

-- Decode a GBA-encoded string from memory
local function decodeGBAString(addr, maxLen)
    local chars = {}
    for i = 0, maxLen - 1 do
        local b = emu:read8(addr + i)
        if b == CHAR_TERMINATOR then break end
        local ch = CHARSET[b]
        if ch then
            table.insert(chars, ch)
        elseif b >= 32 and b <= 126 then
            table.insert(chars, string.char(b))
        else
            table.insert(chars, '?')
        end
    end
    return table.concat(chars)
end

-- Species ID → name (National Dex #1–386, Gen 1–3)
local SPECIES_NAMES = {
    -- Gen 1
    [1]="Bulbasaur",[2]="Ivysaur",[3]="Venusaur",[4]="Charmander",[5]="Charmeleon",
    [6]="Charizard",[7]="Squirtle",[8]="Wartortle",[9]="Blastoise",[10]="Caterpie",
    [11]="Metapod",[12]="Butterfree",[13]="Weedle",[14]="Kakuna",[15]="Beedrill",
    [16]="Pidgey",[17]="Pidgeotto",[18]="Pidgeot",[19]="Rattata",[20]="Raticate",
    [21]="Spearow",[22]="Fearow",[23]="Ekans",[24]="Arbok",[25]="Pikachu",
    [26]="Raichu",[27]="Sandshrew",[28]="Sandslash",[29]="Nidoran-F",[30]="Nidorina",
    [31]="Nidoqueen",[32]="Nidoran-M",[33]="Nidorino",[34]="Nidoking",[35]="Clefairy",
    [36]="Clefable",[37]="Vulpix",[38]="Ninetales",[39]="Jigglypuff",[40]="Wigglytuff",
    [41]="Zubat",[42]="Golbat",[43]="Oddish",[44]="Gloom",[45]="Vileplume",
    [46]="Paras",[47]="Parasect",[48]="Venonat",[49]="Venomoth",[50]="Diglett",
    [51]="Dugtrio",[52]="Meowth",[53]="Persian",[54]="Psyduck",[55]="Golduck",
    [56]="Mankey",[57]="Primeape",[58]="Growlithe",[59]="Arcanine",[60]="Poliwag",
    [61]="Poliwhirl",[62]="Poliwrath",[63]="Abra",[64]="Kadabra",[65]="Alakazam",
    [66]="Machop",[67]="Machoke",[68]="Machamp",[69]="Bellsprout",[70]="Weepinbell",
    [71]="Victreebel",[72]="Tentacool",[73]="Tentacruel",[74]="Geodude",[75]="Graveler",
    [76]="Golem",[77]="Ponyta",[78]="Rapidash",[79]="Slowpoke",[80]="Slowbro",
    [81]="Magnemite",[82]="Magneton",[83]="Farfetch'd",[84]="Doduo",[85]="Dodrio",
    [86]="Seel",[87]="Dewgong",[88]="Grimer",[89]="Muk",[90]="Shellder",
    [91]="Cloyster",[92]="Gastly",[93]="Haunter",[94]="Gengar",[95]="Onix",
    [96]="Drowzee",[97]="Hypno",[98]="Krabby",[99]="Kingler",[100]="Voltorb",
    [101]="Electrode",[102]="Exeggcute",[103]="Exeggutor",[104]="Cubone",[105]="Marowak",
    [106]="Hitmonlee",[107]="Hitmonchan",[108]="Lickitung",[109]="Koffing",[110]="Weezing",
    [111]="Rhyhorn",[112]="Rhydon",[113]="Chansey",[114]="Tangela",[115]="Kangaskhan",
    [116]="Horsea",[117]="Seadra",[118]="Goldeen",[119]="Seaking",[120]="Staryu",
    [121]="Starmie",[122]="Mr. Mime",[123]="Scyther",[124]="Jynx",[125]="Electabuzz",
    [126]="Magmar",[127]="Pinsir",[128]="Tauros",[129]="Magikarp",[130]="Gyarados",
    [131]="Lapras",[132]="Ditto",[133]="Eevee",[134]="Vaporeon",[135]="Jolteon",
    [136]="Flareon",[137]="Porygon",[138]="Omanyte",[139]="Omastar",[140]="Kabuto",
    [141]="Kabutops",[142]="Aerodactyl",[143]="Snorlax",[144]="Articuno",[145]="Zapdos",
    [146]="Moltres",[147]="Dratini",[148]="Dragonair",[149]="Dragonite",[150]="Mewtwo",
    [151]="Mew",
    -- Gen 2
    [152]="Chikorita",[153]="Bayleef",[154]="Meganium",[155]="Cyndaquil",[156]="Quilava",
    [157]="Typhlosion",[158]="Totodile",[159]="Croconaw",[160]="Feraligatr",[161]="Sentret",
    [162]="Furret",[163]="Hoothoot",[164]="Noctowl",[165]="Ledyba",[166]="Ledian",
    [167]="Spinarak",[168]="Ariados",[169]="Crobat",[170]="Chinchou",[171]="Lanturn",
    [172]="Pichu",[173]="Cleffa",[174]="Igglybuff",[175]="Togepi",[176]="Togetic",
    [177]="Natu",[178]="Xatu",[179]="Mareep",[180]="Flaaffy",[181]="Ampharos",
    [182]="Bellossom",[183]="Marill",[184]="Azumarill",[185]="Sudowoodo",[186]="Politoed",
    [187]="Hoppip",[188]="Skiploom",[189]="Jumpluff",[190]="Aipom",[191]="Sunkern",
    [192]="Sunflora",[193]="Yanma",[194]="Wooper",[195]="Quagsire",[196]="Espeon",
    [197]="Umbreon",[198]="Murkrow",[199]="Slowking",[200]="Misdreavus",[201]="Unown",
    [202]="Wobbuffet",[203]="Girafarig",[204]="Pineco",[205]="Forretress",[206]="Dunsparce",
    [207]="Gligar",[208]="Steelix",[209]="Snubbull",[210]="Granbull",[211]="Qwilfish",
    [212]="Scizor",[213]="Shuckle",[214]="Heracross",[215]="Sneasel",[216]="Teddiursa",
    [217]="Ursaring",[218]="Slugma",[219]="Magcargo",[220]="Swinub",[221]="Piloswine",
    [222]="Corsola",[223]="Remoraid",[224]="Octillery",[225]="Delibird",[226]="Mantine",
    [227]="Skarmory",[228]="Houndour",[229]="Houndoom",[230]="Kingdra",[231]="Phanpy",
    [232]="Donphan",[233]="Porygon2",[234]="Stantler",[235]="Smeargle",[236]="Tyrogue",
    [237]="Hitmontop",[238]="Smoochum",[239]="Elekid",[240]="Magby",[241]="Miltank",
    [242]="Blissey",[243]="Raikou",[244]="Entei",[245]="Suicune",[246]="Larvitar",
    [247]="Pupitar",[248]="Tyranitar",[249]="Lugia",[250]="Ho-Oh",[251]="Celebi",
    -- Gen 3 (RSE internal IDs = National Dex + 25; Gen 3 starts at internal 277)
    [277]="Treecko",[278]="Grovyle",[279]="Sceptile",[280]="Torchic",[281]="Combusken",
    [282]="Blaziken",[283]="Mudkip",[284]="Marshtomp",[285]="Swampert",[286]="Poochyena",
    [287]="Mightyena",[288]="Zigzagoon",[289]="Linoone",[290]="Wurmple",[291]="Silcoon",
    [292]="Beautifly",[293]="Cascoon",[294]="Dustox",[295]="Lotad",[296]="Lombre",
    [297]="Ludicolo",[298]="Seedot",[299]="Nuzleaf",[300]="Shiftry",[301]="Taillow",
    [302]="Swellow",[303]="Wingull",[304]="Pelipper",[305]="Ralts",[306]="Kirlia",
    [307]="Gardevoir",[308]="Surskit",[309]="Masquerain",[310]="Shroomish",[311]="Breloom",
    [312]="Slakoth",[313]="Vigoroth",[314]="Slaking",[315]="Nincada",[316]="Ninjask",
    [317]="Shedinja",[318]="Whismur",[319]="Loudred",[320]="Exploud",[321]="Makuhita",
    [322]="Hariyama",[323]="Azurill",[324]="Nosepass",[325]="Skitty",[326]="Delcatty",
    [327]="Sableye",[328]="Mawile",[329]="Aron",[330]="Lairon",[331]="Aggron",
    [332]="Meditite",[333]="Medicham",[334]="Electrike",[335]="Manectric",[336]="Plusle",
    [337]="Minun",[338]="Volbeat",[339]="Illumise",[340]="Roselia",[341]="Gulpin",
    [342]="Swalot",[343]="Carvanha",[344]="Sharpedo",[345]="Wailmer",[346]="Wailord",
    [347]="Numel",[348]="Camerupt",[349]="Torkoal",[350]="Spoink",[351]="Grumpig",
    [352]="Spinda",[353]="Trapinch",[354]="Vibrava",[355]="Flygon",[356]="Cacnea",
    [357]="Cacturne",[358]="Swablu",[359]="Altaria",[360]="Zangoose",[361]="Seviper",
    [362]="Lunatone",[363]="Solrock",[364]="Barboach",[365]="Whiscash",[366]="Corphish",
    [367]="Crawdaunt",[368]="Baltoy",[369]="Claydol",[370]="Lileep",[371]="Cradily",
    [372]="Anorith",[373]="Armaldo",[374]="Feebas",[375]="Milotic",[376]="Castform",
    [377]="Kecleon",[378]="Shuppet",[379]="Banette",[380]="Duskull",[381]="Dusclops",
    [382]="Tropius",[383]="Chimecho",[384]="Absol",[385]="Wynaut",[386]="Snorunt",
    [387]="Glalie",[388]="Spheal",[389]="Sealeo",[390]="Walrein",[391]="Clamperl",
    [392]="Huntail",[393]="Gorebyss",[394]="Relicanth",[395]="Luvdisc",[396]="Bagon",
    [397]="Shelgon",[398]="Salamence",[399]="Beldum",[400]="Metang",[401]="Metagross",
    [402]="Regirock",[403]="Regice",[404]="Registeel",[405]="Latias",[406]="Latios",
    [407]="Kyogre",[408]="Groudon",[409]="Rayquaza",[410]="Jirachi",[411]="Deoxys",
}

-- Substructure ordering: maps personality%24 → position of Growth substruct (0–3).
-- Derived from the full Gen III permutation table (Bulbapedia):
--   p%24 | order   | G at position
--   0-5  | GAEM..  | 0
--   6-7  | AG..    | 1
--   8,10 | AEG/AMG | 2
--   9,11 | AEM/AME | 3 (G is last)
--   12-13| EG..    | 1
--   14,16| EAG/EMG | 2
--   15,17| EAM/EMA | 3
--   18-19| MG..    | 1
--   20,22| MAG/MEG | 2
--   21,23| MAE/MEA | 3
-- Substruct slot positions keyed by personality%24.
-- Growth substruct (G) contains species; Attacks substruct (A) contains move IDs.
-- Full permutation table from Bulbapedia: each entry is [G,A,E,M] positions.
local GROWTH_SLOT = {
    [0]=0,[1]=0,[2]=0,[3]=0,[4]=0,[5]=0,
    [6]=1,[7]=1,[8]=2,[9]=3,[10]=2,[11]=3,
    [12]=1,[13]=1,[14]=2,[15]=3,[16]=2,[17]=3,
    [18]=1,[19]=1,[20]=2,[21]=3,[22]=2,[23]=3,
}
local ATTACKS_SLOT = {
    [0]=1,[1]=1,[2]=2,[3]=3,[4]=2,[5]=3,
    [6]=0,[7]=0,[8]=0,[9]=0,[10]=0,[11]=0,
    [12]=2,[13]=3,[14]=1,[15]=1,[16]=3,[17]=2,
    [18]=2,[19]=3,[20]=1,[21]=1,[22]=3,[23]=2,
}

-- Decrypt all 12 words of the encrypted BoxPokemon substructs.
-- Returns (decrypted_table, personality) or (nil, nil) on read failure.
-- Key = personality XOR otId; each word in bytes 0x20-0x4F is XORed with key.
local function decryptSubstructs(base)
    local ok_p, personality = pcall(function() return emu:read32(base) end)
    local ok_o, ot_id       = pcall(function() return emu:read32(base + 0x04) end)
    if not ok_p or not ok_o then return nil, nil end
    local key = personality ~ ot_id
    local decrypted = {}
    for i = 0, 11 do
        local ok_w, word = pcall(function() return emu:read32(base + 0x20 + i * 4) end)
        if not ok_w then return nil, nil end
        decrypted[i] = word ~ key
    end
    return decrypted, personality
end

-- Status byte interpretation (Emerald uses flags in low byte)
-- bits 0-2: sleep turns, bit 3: poisoned, bit 4: burned, bit 5: frozen, bit 6: paralyzed
local function decodeStatus(statusByte)
    if statusByte == 0 then return "healthy" end
    if statusByte & 0x07 ~= 0 then return "asleep" end
    if statusByte & 0x08 ~= 0 then return "poisoned" end
    if statusByte & 0x10 ~= 0 then return "burned" end
    if statusByte & 0x20 ~= 0 then return "frozen" end
    if statusByte & 0x40 ~= 0 then return "paralyzed" end
    return "affected"
end

-- Read all party Pokemon from GBA memory
-- Returns: count (number), party (array of pokemon tables)
-- Reads unencrypted fields only (level, HP, stats at known offsets).
-- Species/moves are in the encrypted substructure (+0x20) -- not read in MVP.
function memory.readPartyPokemon()
    -- Guard top-level read against memory access failures
    local ok_count, count = pcall(function() return emu:read8(ADDR_PARTY_COUNT) end)
    if not ok_count then
        console:warn("[PokemonAgent] Failed to read party count")
        return 0, {}
    end
    if count > 6 then count = 6 end  -- safety clamp

    local party = {}
    for slot = 0, count - 1 do
        local base = ADDR_PARTY_BASE + (slot * PARTY_SLOT_SIZE)

        local ok_mhp, maxHp = pcall(function() return emu:read16(base + 0x58) end)
        if not ok_mhp then maxHp = 0 end

        -- Skip empty/invalid slots
        if maxHp > 0 then
            local ok_sw, statusWord = pcall(function() return emu:read32(base + 0x50) end)
            if not ok_sw then statusWord = 0 end
            local statusStr = decodeStatus(statusWord & 0xFF)

            local ok_lv, level      = pcall(function() return emu:read8(base + 0x54) end)
            local ok_hp, current_hp = pcall(function() return emu:read16(base + 0x56) end)
            local ok_at, attack     = pcall(function() return emu:read16(base + 0x5A) end)
            local ok_df, defense    = pcall(function() return emu:read16(base + 0x5C) end)
            local ok_sp, speed      = pcall(function() return emu:read16(base + 0x5E) end)
            local ok_sa, sp_attack  = pcall(function() return emu:read16(base + 0x60) end)
            local ok_sd, sp_defense = pcall(function() return emu:read16(base + 0x62) end)

            local decrypted, personality = decryptSubstructs(base)
            local species_id = nil
            local move_ids   = {0, 0, 0, 0}
            if decrypted then
                local p24   = personality % 24
                local g_slot = GROWTH_SLOT[p24]
                local a_slot = ATTACKS_SLOT[p24]
                -- Species from Growth substruct (word 0, low 16 bits)
                local raw_sid = decrypted[g_slot * 3] & 0xFFFF
                if raw_sid > 0 and raw_sid <= 411 then species_id = raw_sid end
                -- Move IDs from Attacks substruct (words 0-1 packed as 4 × u16)
                local a0 = a_slot * 3
                move_ids = {
                    decrypted[a0]     & 0xFFFF,
                    (decrypted[a0]    >> 16) & 0xFFFF,
                    decrypted[a0 + 1] & 0xFFFF,
                    (decrypted[a0 + 1] >> 16) & 0xFFFF,
                }
            end
            local pokemon = {
                slot         = slot + 1,
                nickname     = decodeGBAString(base + 0x08, 10),
                level        = ok_lv and level      or 0,
                current_hp   = ok_hp and current_hp or 0,
                max_hp       = maxHp,
                attack       = ok_at and attack     or 0,
                defense      = ok_df and defense    or 0,
                speed        = ok_sp and speed      or 0,
                sp_attack    = ok_sa and sp_attack  or 0,
                sp_defense   = ok_sd and sp_defense or 0,
                status       = statusStr,
                species_id   = species_id,
                species_name = (species_id and SPECIES_NAMES[species_id]) or "Unknown",
                types        = {},
                moves        = {},
                move_ids     = move_ids,
            }
            table.insert(party, pokemon)
        end
    end
    return count, party
end

-- SaveBlock1 pointer (IWRAM) -- dereference to get EWRAM address of SaveBlock1
local ADDR_SAVBLOCK1_PTR = 0x03005D8C

-- Map name lookup: map_id = (mapNum * 100) + mapGroup
-- Derived from include/constants/map_groups.h in pret/pokeemerald.
-- map_id is computed from the two bytes at SaveBlock1+0x04 (WarpData.mapGroup)
-- and SaveBlock1+0x05 (WarpData.mapNum): map_id = mapNum*100 + mapGroup.
local MAP_NAMES = {
    -- Cities (mapGroup=0)
    [0]    = "Petalburg City",
    [100]  = "Slateport City",
    [200]  = "Mauville City",
    [300]  = "Rustboro City",
    [400]  = "Fortree City",
    [500]  = "Lilycove City",
    [600]  = "Mossdeep City",
    [700]  = "Sootopolis City",
    [800]  = "Ever Grande City",
    -- Towns (mapGroup=0)
    [900]  = "Littleroot Town",
    [1000] = "Oldale Town",
    [1100] = "Dewford Town",
    [1200] = "Lavaridge Town",
    [1300] = "Fallarbor Town",
    [1400] = "Verdanturf Town",
    [1500] = "Pacifidlog Town",
    -- Routes (mapGroup=0)
    [1600] = "Route 101",
    [1700] = "Route 102",
    [1800] = "Route 103",
    [1900] = "Route 104",
    [2000] = "Route 105",
    [2100] = "Route 106",
    [2200] = "Route 107",
    [2300] = "Route 108",
    [2400] = "Route 109",
    [2500] = "Route 110",
    [2600] = "Route 111",
    [2700] = "Route 112",
    [2800] = "Route 113",
    [2900] = "Route 114",
    [3000] = "Route 115",
    [3100] = "Route 116",
    [3200] = "Route 117",
    [3300] = "Route 118",
    [3400] = "Route 119",
    [3500] = "Route 120",
    [3600] = "Route 121",
    [3700] = "Route 122",
    [3800] = "Route 123",
    [3900] = "Route 124",
    [4000] = "Route 125",
    [4100] = "Route 126",
    [4200] = "Route 127",
    [4300] = "Route 128",
    [4400] = "Route 129",
    [4500] = "Route 130",
    [4600] = "Route 131",
    [4700] = "Route 132",
    [4800] = "Route 133",
    [4900] = "Route 134",
    -- Dungeons (mapGroup=24): map_id = mapNum*100 + 24
    [624]  = "Desert Ruins",
    [724]  = "Granite Cave 1F",
    [824]  = "Granite Cave B1F",
    [924]  = "Granite Cave B2F",
    [1524] = "Mt. Pyre 1F",
    [2124] = "Mt. Pyre Exterior",
    [2224] = "Mt. Pyre Summit",
    [2324] = "Aqua Hideout",
    [2724] = "Seafloor Cavern",
    [3824] = "Cave of Origin",
    [4324] = "Victory Road 1F",
    [4424] = "Victory Road B1F",
    [4524] = "Victory Road B2F",
    [4624] = "Shoal Cave",
    [7924] = "Sky Pillar",
    [8524] = "Sky Pillar Top",
    [8624] = "Magma Hideout",
    -- Pokemon League (mapGroup=16)
    [1016] = "Pokemon League",
}

-- SaveBlock1 offsets (from pokeemerald include/global.h PlayerState struct)
-- struct MapPosition { s16 x, y; u8 height; } pos; at offset 0x0000
-- mapGroup at +0x04, mapNum at +0x05 (location fields in SaveBlock1)
local SB1_POS_X     = 0x0000
local SB1_POS_Y     = 0x0002
local SB1_MAP_NUM   = 0x0004
local SB1_MAP_GROUP = 0x0005

-- Read player position and map ID
-- Returns: {x, y, map_id} table; returns zeros if SaveBlock1 not initialized
function memory.readPlayerPosition()
    -- Guard against memory read failures
    local ok_sb1, sb1 = pcall(function() return emu:read32(ADDR_SAVBLOCK1_PTR) end)
    if not ok_sb1 or sb1 == 0 then
        return {x = 0, y = 0, map_id = 0, map_name = "Unknown"}
    end
    local ok_x,  x         = pcall(function() return emu:read16(sb1 + SB1_POS_X) end)
    local ok_y,  y         = pcall(function() return emu:read16(sb1 + SB1_POS_Y) end)
    local ok_mn, map_num   = pcall(function() return emu:read8(sb1 + SB1_MAP_NUM) end)
    local ok_mg, map_group = pcall(function() return emu:read8(sb1 + SB1_MAP_GROUP) end)
    if not (ok_x and ok_y and ok_mn and ok_mg) then
        console:warn("[PokemonAgent] Failed to read player position")
        return {x = 0, y = 0, map_id = 0, map_name = "Unknown"}
    end
    local map_id   = (map_group * 100) + map_num
    local map_name = MAP_NAMES[map_id] or ("Unknown Map " .. map_id)
    return {x = x, y = y, map_id = map_id, map_name = map_name}
end

-- Badge flag IDs (from pokeemerald include/constants/flags.h)
-- FLAG_BADGE01_GET = 0x867 through FLAG_BADGE08_GET = 0x86E
local BADGE_FLAGS = {
    {name = "Stone Badge",   flag_id = 0x867},
    {name = "Knuckle Badge", flag_id = 0x868},
    {name = "Dynamo Badge",  flag_id = 0x869},
    {name = "Heat Badge",    flag_id = 0x86A},
    {name = "Balance Badge", flag_id = 0x86B},
    {name = "Feather Badge", flag_id = 0x86C},
    {name = "Mind Badge",    flag_id = 0x86D},
    {name = "Rain Badge",    flag_id = 0x86E},
}

-- Flags array offset within SaveBlock1 (from pokeemerald global.h)
-- struct SaveBlock1 { ... u8 flags[NUM_FLAG_BYTES]; ... }
-- Offset verified from pokeemerald decomp: flags start at 0x1270 in SaveBlock1
local FLAGS_OFFSET = 0x1270

-- Read earned gym badges from SaveBlock1 flags
-- Returns: array of badge name strings for all earned badges
function memory.readBadges()
    local sb1 = emu:read32(ADDR_SAVBLOCK1_PTR)
    if sb1 == 0 then return {} end

    local flags_base = sb1 + FLAGS_OFFSET
    local badges = {}
    for _, badge in ipairs(BADGE_FLAGS) do
        local flag_id    = badge.flag_id
        local byte_off   = math.floor(flag_id / 8)
        local bit_index  = flag_id % 8
        local byte_val   = emu:read8(flags_base + byte_off)
        if (byte_val >> bit_index) & 1 == 1 then
            table.insert(badges, badge.name)
        end
    end
    return badges
end

-- Battle type flags address (Emerald EWRAM)
local ADDR_BATTLE_TYPE    = 0x02022FEC
local ADDR_BATTLE_OUTCOME = 0x0202433A

-- Detect whether a battle is currently active
-- gBattleTypeFlags remains non-zero after battle ends, so we combine it with
-- gBattleOutcome: battle is active only when flags are set AND outcome==0.
function memory.readBattleState()
    -- Guard against memory read failures
    local ok_f, flags   = pcall(function() return emu:read32(ADDR_BATTLE_TYPE) end)
    if not ok_f or flags == 0 then return false end
    local ok_o, outcome = pcall(function() return emu:read8(ADDR_BATTLE_OUTCOME) end)
    if ok_o and outcome ~= 0 then return false end
    return true
end

-- Item ID → name (Pokemon Emerald, derived from include/constants/items.h)
local ITEM_NAMES = {
    -- Poke Balls
    [1]="Master Ball",[2]="Ultra Ball",[3]="Great Ball",[4]="Poke Ball",
    [5]="Safari Ball",[6]="Net Ball",[7]="Dive Ball",[8]="Nest Ball",
    [9]="Repeat Ball",[10]="Timer Ball",[11]="Luxury Ball",[12]="Premier Ball",
    -- Potions / healing
    [13]="Potion",[14]="Antidote",[15]="Burn Heal",[16]="Ice Heal",
    [17]="Awakening",[18]="Parlyz Heal",[19]="Full Restore",[20]="Max Potion",
    [21]="Hyper Potion",[22]="Super Potion",[23]="Full Heal",[24]="Revive",
    [25]="Max Revive",[26]="Fresh Water",[27]="Soda Pop",[28]="Lemonade",
    [29]="MooMoo Milk",[30]="Energy Powder",[31]="Energy Root",
    [32]="Heal Powder",[33]="Revival Herb",
    -- PP / elixirs
    [34]="Ether",[35]="Max Ether",[36]="Elixir",[37]="Max Elixir",
    -- Misc consumables
    [38]="Lava Cookie",[39]="Blue Flute",[40]="Yellow Flute",[41]="Red Flute",
    [42]="Black Flute",[43]="White Flute",[44]="Berry Juice",[45]="Sacred Ash",
    [46]="Shoal Salt",[47]="Shoal Shell",
    [48]="Red Shard",[49]="Blue Shard",[50]="Yellow Shard",[51]="Green Shard",
    -- Vitamins / EVs
    [57]="HP Up",[58]="Protein",[59]="Iron",[60]="Carbos",[61]="Calcium",
    [62]="Rare Candy",[63]="PP Up",[64]="Zinc",[65]="PP Max",
    -- Battle items
    [67]="Guard Spec.",[68]="Dire Hit",[69]="X Attack",[70]="X Defend",
    [71]="X Speed",[72]="X Accuracy",[73]="X Special",[74]="Poke Doll",
    [75]="Fluffy Tail",[77]="Super Repel",[78]="Max Repel",[79]="Escape Rope",
    [80]="Repel",[91]="Sun Stone",[92]="Moon Stone",[93]="Fire Stone",
    [94]="Thunder Stone",[95]="Water Stone",[96]="Leaf Stone",
    -- Hold items (common)
    [97]="Tiny Mushroom",[98]="Big Mushroom",[100]="Pearl",[101]="Big Pearl",
    [102]="Stardust",[103]="Star Piece",[104]="Nugget",[105]="Heart Scale",
    [107]="Orange Mail",[108]="Harbor Mail",[109]="Glitter Mail",
    [116]="Smoke Ball",[117]="Everstone",[118]="Focus Band",[119]="Lucky Egg",
    [120]="Scope Lens",[121]="Metal Coat",[122]="Leftovers",[123]="Dragon Scale",
    [124]="Light Ball",[125]="Soft Sand",[126]="Hard Stone",[127]="Miracle Seed",
    [128]="BlackGlasses",[129]="Black Belt",[130]="Magnet",[131]="Mystic Water",
    [132]="Sharp Beak",[133]="Poison Barb",[134]="NeverMeltIce",[135]="Spell Tag",
    [136]="TwistedSpoon",[137]="Charcoal",[138]="Dragon Fang",[139]="Silk Scarf",
    [140]="Up-Grade",[141]="Shell Bell",[142]="Sea Incense",[143]="Lax Incense",
    [144]="Lucky Punch",[145]="Metal Powder",[146]="Thick Club",[147]="Stick",
    -- Berries
    [149]="Cheri Berry",[150]="Chesto Berry",[151]="Pecha Berry",[152]="Rawst Berry",
    [153]="Aspear Berry",[154]="Leppa Berry",[155]="Oran Berry",[156]="Persim Berry",
    [157]="Lum Berry",[158]="Sitrus Berry",[159]="Figy Berry",[160]="Wiki Berry",
    [161]="Mago Berry",[162]="Aguav Berry",[163]="Iapapa Berry",[164]="Razz Berry",
    [165]="Bluk Berry",[166]="Nanab Berry",[167]="Wepear Berry",[168]="Pinap Berry",
    [169]="Pomeg Berry",[170]="Kelpsy Berry",[171]="Qualot Berry",[172]="Hondew Berry",
    [173]="Grepa Berry",[174]="Tamato Berry",[175]="Cornn Berry",[176]="Magost Berry",
    [177]="Rabuta Berry",[178]="Nomel Berry",[179]="Spelon Berry",[180]="Pamtre Berry",
    [181]="Watmel Berry",[182]="Durin Berry",[183]="Belue Berry",[184]="Liechi Berry",
    [185]="Ganlon Berry",[186]="Salac Berry",[187]="Petaya Berry",[188]="Apicot Berry",
    [189]="Lansat Berry",[190]="Starf Berry",[191]="Enigma Berry",
    -- Key Items
    [253]="Mach Bike",[254]="Coin Case",[255]="Itemfinder",[256]="Old Rod",
    [257]="Good Rod",[258]="Super Rod",[259]="S.S. Ticket",[260]="Contest Pass",
    [262]="Wailmer Pail",[263]="Devon's Goods",[264]="Soot Sack",
    [265]="Basement Key",[266]="Acro Bike",[267]="PokeBlock Case",
    [268]="Letter",[269]="Eon Ticket",[270]="Red Orb",[271]="Blue Orb",
    [272]="Scanner",[273]="Go-Goggles",[274]="Meteorite",[275]="Rm. 1 Key",
    [276]="Rm. 2 Key",[277]="Rm. 4 Key",[278]="Rm. 6 Key",[279]="Storage Key",
    [280]="Root Fossil",[281]="Claw Fossil",[282]="Devon Scope",
    -- HMs
    [339]="HM01 Cut",[340]="HM02 Fly",[341]="HM03 Surf",[342]="HM04 Strength",
    [343]="HM05 Flash",[344]="HM06 Rock Smash",[345]="HM07 Waterfall",[346]="HM08 Dive",
    -- TMs
    [289]="TM01 Focus Punch",[290]="TM02 Dragon Claw",[291]="TM03 Water Pulse",
    [292]="TM04 Calm Mind",[293]="TM05 Roar",[294]="TM06 Toxic",[295]="TM07 Hail",
    [296]="TM08 Bulk Up",[297]="TM09 Bullet Seed",[298]="TM10 Hidden Power",
    [299]="TM11 Sunny Day",[300]="TM12 Taunt",[301]="TM13 Ice Beam",[302]="TM14 Blizzard",
    [303]="TM15 Hyper Beam",[304]="TM16 Light Screen",[305]="TM17 Protect",
    [306]="TM18 Rain Dance",[307]="TM19 Giga Drain",[308]="TM20 Safeguard",
    [309]="TM21 Frustration",[310]="TM22 SolarBeam",[311]="TM23 Iron Tail",
    [312]="TM24 Thunderbolt",[313]="TM25 Thunder",[314]="TM26 Earthquake",
    [315]="TM27 Return",[316]="TM28 Dig",[317]="TM29 Psychic",[318]="TM30 Shadow Ball",
    [319]="TM31 Brick Break",[320]="TM32 Double Team",[321]="TM33 Reflect",
    [322]="TM34 Shock Wave",[323]="TM35 Flamethrower",[324]="TM36 Sludge Bomb",
    [325]="TM37 Sandstorm",[326]="TM38 Fire Blast",[327]="TM39 Rock Tomb",
    [328]="TM40 Aerial Ace",[329]="TM41 Torment",[330]="TM42 Facade",
    [331]="TM43 Secret Power",[332]="TM44 Rest",[333]="TM45 Attract",
    [334]="TM46 Thief",[335]="TM47 Steel Wing",[336]="TM48 Skill Swap",
    [337]="TM49 Snatch",[338]="TM50 Overheat",
}

-- Bag pocket descriptors (SaveBlock1 offsets and capacities)
local BAG_POCKETS = {
    {name="items",     offset=0x0560, capacity=30},
    {name="key_items", offset=0x05D8, capacity=30},
    {name="pokeballs", offset=0x0650, capacity=16},
    {name="tms_hms",   offset=0x0690, capacity=64},
    {name="berries",   offset=0x0790, capacity=46},
}

-- PC box constants
local PC_BOX_COUNT     = 14
local PC_SLOTS_PER_BOX = 30
local PC_SLOT_SIZE     = 80  -- BoxPokemon is 80 bytes
local BP_NICKNAME_OFF  = 0x08
local BP_FLAGS_OFF     = 0x13
local BP_HAS_SPECIES   = 0x02  -- bit 1 of flags byte = hasSpecies (slot occupied)

-- Read all bag pockets from SaveBlock1.
-- Returns a table keyed by pocket name; each value is an array of
-- {item_id, name, quantity} for non-empty slots.
-- Quantities are XOR-encrypted with gSaveBlock2->encryptionKey (anti-cheat).
function memory.readBag()
    local ok_sb, sb1 = pcall(function() return emu:read32(ADDR_SAVBLOCK1_PTR) end)
    if not ok_sb or sb1 == 0 then return {} end

    -- Read the 32-bit encryption key from SaveBlock2; mask to 16 bits to match u16 quantity XOR.
    local enc_key = 0
    local ok_sb2, sb2 = pcall(function() return emu:read32(ADDR_SAVBLOCK2_PTR) end)
    if ok_sb2 and sb2 ~= 0 then
        local ok_k, raw_key = pcall(function() return emu:read32(sb2 + 0xAC) end)
        if ok_k then enc_key = raw_key & 0xFFFF end
    end

    local bag = {}
    for _, pocket in ipairs(BAG_POCKETS) do
        local slots = {}
        local base = sb1 + pocket.offset
        for i = 0, pocket.capacity - 1 do
            local ok_id, item_id  = pcall(function() return emu:read16(base + i * 4) end)
            local ok_qt, quantity = pcall(function() return emu:read16(base + i * 4 + 2) end)
            if ok_id and ok_qt and item_id ~= 0 then
                table.insert(slots, {
                    item_id  = item_id,
                    name     = ITEM_NAMES[item_id] or ("Item#" .. item_id),
                    quantity = quantity ~ enc_key,
                })
            end
        end
        bag[pocket.name] = slots
    end
    return bag
end

-- Read PC box occupancy (unencrypted fields only: nickname + slot info).
-- Encrypted fields (species, level, moves) are not read without decryption.
-- Returns array of {box, pokemon:[{box, slot, nickname}]} for occupied boxes.
function memory.readPCBoxes()
    local ok_ptr, storage = pcall(function() return emu:read32(ADDR_PC_STORAGE_PTR) end)
    if not ok_ptr or storage == 0 then return {} end
    local boxes = {}
    for box = 0, PC_BOX_COUNT - 1 do
        local slots = {}
        for slot = 0, PC_SLOTS_PER_BOX - 1 do
            local base  = storage
                        + (box * PC_SLOTS_PER_BOX * PC_SLOT_SIZE)
                        + (slot * PC_SLOT_SIZE)
            local ok_fl, flags = pcall(function() return emu:read8(base + BP_FLAGS_OFF) end)
            if ok_fl and (flags & BP_HAS_SPECIES) ~= 0 then
                table.insert(slots, {
                    box      = box + 1,
                    slot     = slot + 1,
                    nickname = decodeGBAString(base + BP_NICKNAME_OFF, 10),
                })
            end
        end
        if #slots > 0 then
            table.insert(boxes, {box = box + 1, pokemon = slots})
        end
    end
    return boxes
end

return memory
