# h3m.py: H3M map dict schema

`tools/h3tools/h3m.py` reads and writes Heroes III maps in the RoE (`0x0E`), AB (`0x15`) and SoD (`0x1C`) formats without losing any bytes. HotA (`0x20`) and WoG (`0x33`) maps are refused with `H3MError`.

```python
from h3tools import h3m
m = h3m.load("Maps/Foo.h3m")        # gzip or raw file -> dict
raw = h3m.build(m)                  # dict -> decompressed bytes
m2 = h3m.parse(raw)                 # decompressed bytes -> dict
h3m.save(m, "out/foo.h3m")          # gzip.compress(build(m), mtime=0)
m = h3m.blank_map(size=72, two_levels=True, name="...", version="SoD")
```

Test: `PYTHONIOENCODING=utf-8 python -X utf8 tests/test_h3m_roundtrip.py`. It checks byte-exact round trips over all 332 maps shipped with the game, including the maps inside campaigns.

## Conventions

- **The format is decided by `version`** (`"RoE"`, `"AB"` or `"SoD"`). The reader and writer are the same code, so a field marked *AB+* or *SoD* below exists only in those formats. The writer ignores keys that the format does not have.
- **IDs are plain ints, in H3 numbering.** `-1` means none, neutral or random in every field documented as `id/-1`. On disk that is `0xFF` for u8 fields, `0xFFFF` for u16 fields, and the u32 value 255 for u32 owners.
- **Creature ids** are u8 in RoE and u16 in AB+. `-1` means an empty slot. `-2` to `-15` mean a random creature: level `(-id-2)//2`, upgraded when `(-id-2)` is odd.
- **Artifact ids** are u8 in RoE and u16 in AB+, with `-1` for none.
- **Armies** (`army`, `guards`) are lists of 7 `[creature, count]` pairs. An empty slot is `[-1, 0]`. The writer pads a shorter list with empty slots.
- **Bitmasks** are stored as sorted lists of the indices of the set bits, LSB first. Bits past the official count are kept as-is.
- **Flags** are `true`/`false`. A raw byte other than 0 or 1 is kept as an int. Some unplayable players carry uninitialised garbage here.
- **Optional parts** are stored as `None`/`null` when absent. These are the parts the format guards with a presence byte: a custom name, an army, artifacts, and so on.
- **Keys starting with `_`** are bytes VCMI skips, stored as hex strings. A key appears only when its bytes are not all zero, and none appears in the 332 shipped maps. Leave them out when you write a map by hand.
- **Writer defaults.** A missing key gets a sensible default (0, `None`, `[]`, `-1`, `"standard"`, and so on). Hand-written objects can therefore be short, for example `{"x":5,"y":5,"z":0,"template":3,"kind":"sign","message":"…"}`.
- **Strings** are stored on disk as a u32 length followed by bytes in the game codepage: GBK for the Simplified Chinese release, selected by `parse(..., encoding=)` and `build(..., encoding=)`. Undecodable bytes survive as lone surrogates (`surrogateescape`); none occur in the corpus. `json.dumps` with the default `ensure_ascii=True` handles them. When writing JSON with `ensure_ascii=False`, open the file with `errors="surrogatepass"`, or just use plain GBK-encodable text.
- **Text encoding in VCMI.** VCMI decodes a map in `Maps/` with the base game's language (Chinese → GBK). A map shipped inside a VCMI mod is decoded with the mod's `"language"`, so set `"language": "chinese"` in that mod's `mod.json`.

## Top-level keys

| key | type | notes |
|---|---|---|
| `version` | str | `"RoE"`, `"AB"` or `"SoD"` |
| `has_players` | flag | always true in practice |
| `name`, `description` | str | |
| `difficulty` | int | 0 easy, 1 normal, 2 hard, 3 expert, 4 impossible |
| `level_limit` | int | *AB+*. 0 means no limit |
| `players` | list of 8 player dicts | index = colour: 0 red, 1 blue, 2 tan, 3 green, 4 orange, 5 purple, 6 teal, 7 pink |
| `victory`, `loss` | dict | see below |
| `team_count` | int | 0 means no alliances |
| `teams` | list of 8 ints | team of each player; only present when `team_count > 0` |
| `allowed_heroes` | bit list | heroes that may appear (16 bytes RoE, 20 AB+) |
| `reserved_heroes` | list of hero ids | *AB+*. Heroes reserved for campaign placeholders |
| `disposed_heroes` | list | *SoD*. `{hero, portrait, name, players}`: per-hero name/portrait overrides and the players that may hire the hero |
| `disabled_artifacts` | bit list | *AB+*. Set bit = banned (17 bytes AB, 18 SoD) |
| `disabled_spells`, `disabled_skills` | bit list | *SoD*. Set bit = banned |
| `rumors` | list of `{name, text}` | tavern rumours |
| `hero_settings` | list | *SoD*. Per hero type: `{hero, experience, secondary_skills, artifacts, biography, gender, spells, primary_skills}`. Same meanings as on a hero object. Only customised heroes are listed |
| `terrain` | dict | `{size, two_levels, tiles}` |
| `templates` | list | object templates ("defs") |
| `objects` | list | placed objects |
| `events` | list | global timed events |
| `trailing` | hex str | bytes after the events. 124 zero bytes in every editor-saved map; empty for the random-map-generator output |

**Player:**
- `can_human`, `can_computer`, `ai_tactic`: `ai_tactic` is -1 none, 0 random, 1 warrior, 2 builder, 3 explorer.
- `faction_selectable` (*SoD*): the raw byte VCMI skips. Unplayable players hold garbage here.
- `factions`: bit list of factions. 1 byte RoE (8 factions), 2 bytes AB+ (index 8 is Conflux).
- `random_faction`.
- `main_town`: `None` or `{generate_hero (AB+), town_type (AB+, unused byte, -1), x, y, z}`. The position is the town's entrance tile.
- `random_hero`.
- `main_hero`: `None` or `{hero, portrait, name}`.
- *AB+*: `placeholder_count` (unused byte) and `heroes`, a list of `[{hero, name}]` with the heroes this player starts with.

**Victory** has a `type`. Every type except `standard` also carries `allow_normal` and `applies_to_ai`. The extra fields per type:

| type | fields |
|---|---|
| `standard` | none |
| `acquire_artifact` | `artifact` |
| `accumulate_creatures` | `creature`, `amount` |
| `accumulate_resources` | `resource`, `amount` |
| `upgrade_town` | `x`, `y`, `z`, `hall_level`, `castle_level` |
| `build_grail`, `defeat_hero`, `capture_town`, `defeat_monster` | `x`, `y`, `z` |
| `flag_dwellings`, `flag_mines` | none |
| `transport_artifact` | `artifact` (always u8, even in AB/SoD), `x`, `y`, `z` |

**Loss** has a `type`: `standard`; `lose_town` or `lose_hero` with `x`, `y`, `z`; or `time_expires` with `days`.

## Terrain

`terrain.tiles[z][y][x]` is a 7-int list: `[terrain, view, river, river_dir, road, road_dir, flags]`.

- **Terrain values:** 0 dirt, 1 sand, 2 grass, 3 snow, 4 swamp, 5 rough, 6 subterranean, 7 lava, 8 water, 9 rock.
- **`view`** is the frame of the terrain sprite.
  - Interior frames are dirt 21–44, sand 0–23, water 21–32, rock 0–7, and 49–72 for the other terrains.
  - The editor mostly uses the first 8 frames of each range.
  - Frames below those ranges are edge transitions. Changing terrain therefore needs re-tiling.
- **Rivers:** 0 none, 1 clear, 2 icy, 3 muddy, 4 lava.
- **Roads:** 0 none, 1 dirt, 2 gravel, 3 cobblestone.
- **`flags`:** bits 0–1 mirror the terrain frame (horizontal/vertical), bits 2–3 the river, bits 4–5 the road. Bit 6 marks land tiles that border water (coast). VCMI uses bit 7 at runtime for Favourable Winds.
- **Size and levels** come from `terrain.size` and `terrain.two_levels`; the map header has no separate copy.

## Templates

Fields of a template dict:

- `def`: sprite name, e.g. `"AVXsign0.def"`.
- `block_mask`, `visit_mask`: 6 raw bytes each, covering the 8×6 tile box whose bottom-right corner is the object's `x,y`.
  - Byte 0 is the top row and byte 5 the bottom row. Bit 0 is the leftmost column and bit 7 the rightmost.
  - A clear bit in the block mask means the tile is blocked. A set bit in the visit mask means the tile is the entrance.
  - Example: a 1-tile monster has `block_mask[5]=127` and `visit_mask[5]=128`.
- `landscape`: first u16, bit list of terrains. The editor's placement terrains.
- `allowed_terrains`: second u16, bit list of terrains. VCMI uses this one as the allowed terrains.
- `class`, `subclass`: the object class and subclass ids (VCMI `Obj` enum).
- `group`: editor group; 2 creature, 3 hero, 4 artifact, 5 resource.
- `is_overlay`: 1 for flat decorations drawn under other objects.

The editor always writes `AVWmrnd0.def` and `AVLholg0.def` as templates 0 and 1, and `blank_map` does the same. To place a new kind of object, copy its template from a map that contains one, or from `Objects.txt` in `H3bitmap.lod`.

## Objects

Every object has `x`, `y`, `z` (the bottom-right tile of the sprite box, not the entrance), `template` (an index into `templates`) and `kind`.

`kind` is derived from the template's class and subclass. The writer ignores it except to reject a kind that contradicts the template. Objects without a payload get the snake_case class name as their kind: `mountain`, `oak_trees`, `monolith_two_way`, `border_guard`, `keymaster`, `border_gate`, and so on.

**Identifiers.** `identifier` (*AB+*, on heroes, monsters and towns) is the u32 that quests and random dwellings use to refer to the object:
- `kill_hero` and `kill_monster` quests name their target by it in `target`.
- A random dwelling links to a town through `town_identifier`.

The editor fills it with random values. When authoring, give each object a unique non-zero value.

| kind (class ids) | payload fields |
|---|---|
| `event` (26) | *box* fields + `players` (bit list of who may trigger it), `computer_activate`, `remove_after_visit` |
| `pandora` (6) | *box* fields |
| `hero` (34, 70 random), `prison` (62) | `identifier`*AB+*, `owner` (u8/-1), `hero` (type id, -1 random), `name`?, `experience` (*SoD*: optional; RoE/AB: int where 0 means default), `portrait`?, `secondary_skills`? `[[skill, level]...]`, `army`?, `formation` (0 spread, 1 grouped), `artifacts`? `{worn: [19 slots SoD / 18], backpack: [...]}`, `patrol_radius` (-1 none), `biography`?*AB+*, `gender`*AB+* (-1 default, 0 male, 1 female), `spells`?*SoD* (bit list) / `spell`*AB only* (u8; 254 default, 255 none), `primary_skills`?*SoD* `[atk, def, pow, know]` |
| `monster` (54, 71–75, 162–164) | `identifier`*AB+*, `count` (0 = random), `disposition` (0 compliant … 4 savage), `message`? (when set, `resources` [7] and `artifact` follow; both are given to the hero after the fight), `never_flees`, `no_grow` |
| `sign` (91), `ocean_bottle` (59) | `message` |
| `seer_hut` (83) | `quest`, `reward` (absent when the quest is `none`) |
| `quest_guard` (215) | `quest` |
| `witch_hut` (113) | `skills`*AB+* (bit list of skills it may teach) |
| `scholar` (81) | `bonus_type` (-1 random, 0 primary, 1 secondary, 2 spell), `bonus_id` |
| `garrison` (33, 219 vertical) | `owner` (u32/-1), `army`, `removable_units`*AB+* |
| `artifact` (5, 65–69 random) | `message`?, `guards`? (guards require a message, but it may be `""`). The artifact type comes from the template subclass |
| `spell_scroll` (93) | `message`?, `guards`?, `spell` (u32) |
| `resource` (79, 76 random) | `message`?, `guards`?, `amount` (0 = random; gold is stored in hundreds, so 50 means 5000 gold, and VCMI multiplies it by 100) |
| `town` (98, 77 random) | `identifier`*AB+*, `owner` (u8/-1), `name`?, `army`?, `formation`, `buildings`? `{built, forbidden}` (6-byte bit lists) or `null` plus `has_fort`, `must_have_spells`*AB+* (bit list), `banned_spells` (bit list), `events` (town events), `alignment`*SoD* (u8/-1; for a random town: same as player N) |
| `mine` (53, 220 with subclass < 7) | `owner` (u32/-1) |
| `abandoned_mine` (53/220 with subclass ≥ 7) | `resources` (bit list of possible resources) |
| `dwelling` (17–20), `shipyard` (87), `lighthouse` (42) | `owner` (u32/-1) |
| `shrine` (88–90) | `spell` (u32, 255 = random) |
| `grail` (36) | `radius` |
| `random_dwelling` (216 any, 217 by level, 218 by faction) | `owner`, `town_identifier` (216/217; 0 means "use `factions`"), `factions` (bit list, only when `town_identifier` is 0), `min_level`, `max_level` (216/218, 0-based) |
| `hero_placeholder` (214) | `owner` (u8/-1), `hero` (-1 means placeholder by power), `power_rank` (only when `hero` is -1) |

`?` marks a field that is `null` when absent. *AB+* and *SoD* mark fields that exist only in those formats.

**Box fields** (events and Pandora's boxes):
- `message`?, `guards`?
- `experience`, `mana`, `morale`, `luck`
- `resources`: list of 7 (wood, mercury, ore, sulfur, crystal, gems, gold)
- `primary_skills`: list of 4
- `secondary_skills`: `[[skill, level]]`
- `artifacts`, `spells`: lists of ids
- `creatures`: `[[creature, count]]`

**Quest** (AB+), `type` with its extra fields:
- `none`
- `level`: `level`
- `primary_skills`: `primary_skills`, a list of 4
- `kill_hero`, `kill_monster`: `target`, the object identifier
- `artifacts`: `artifacts`, a list
- `army`: `army`, a list of `[creature, count]`
- `resources`: `resources`, a list of 7
- `hero`: `hero`, a hero type
- `player`: `player`, a colour

Every type except `none` also has:
- `deadline`: last day, -1 for none.
- `first_visit_text`, `next_visit_text`, `completed_text`: the seer's dialogue. An empty string makes the game use its default text.

A RoE seer hut can only ask for one artifact, stored as `{"type": "artifacts", "artifacts": [id]}` with no deadline or texts.

**Reward** (seer huts), `type` with its extra fields:
- `nothing`
- `experience`, `mana`: `amount`
- `morale`, `luck`: `amount`, from -3 to 3
- `resource`: `resource`, `amount`
- `primary_skill`: `skill`, `amount`
- `secondary_skill`: `skill`, `level` (1–3)
- `artifact`: `artifact`
- `spell`: `spell`
- `creature`: `creature`, `amount`

**Timed events** (top-level `events`, and the head of each town event):
- `name`: editor-only label.
- `message`: shown to the player.
- `resources`: list of 7.
- `players`: bit list.
- `human` (*SoD*; RoE/AB always apply to humans), `computer`.
- `first_day`: 0-based; 0 means day 1.
- `repeat_days`: 0 means once.

Town events add `buildings` (6-byte bit list), `creatures` (7 growth bonuses, one per level) and `_pad_town`.

## Text-bearing fields (for story maps)

- **Map header:** `name`, `description`, `rumors[].text`, `players[].main_hero.name`, `players[].heroes[].name`, `disposed_heroes[].name`, `hero_settings[].biography`.
- **Objects:**
  - `event` and `pandora`: `message`, shown on visit before the reward or the guard fight.
  - `sign` and `ocean_bottle`: `message`.
  - `monster`: `message`, shown when the monster is attacked.
  - `artifact`, `spell_scroll` and `resource`: `message`, shown on pickup or before the guards.
  - `seer_hut` and `quest_guard`: the three quest texts.
  - `hero` and `prison`: `name` and `biography`.
  - `town`: `name` and each event's `message`.
- **Timed events:** `events[].message`. `name` is shown only in the editor.

## Format quirks found while matching VCMI

- **Unplayable players** keep stale bytes in `faction_selectable` and `random_faction` (values like 197, 254, 32). VCMI reads every bool by its lowest bit (`raw & 1`), so 32 is false; the original game tests for non-zero.
- **Template terrain masks.** VCMI discards the first terrain u16 (`landscape`) and uses the second as allowed terrains. It also skips the 16 trailing template bytes.
- **Seer hut padding.** With quest `none`, a seer hut still has the reward-type byte (kept as `_pad_reward` if non-zero) and 2 pad bytes. A quest guard has neither.
- **Mines.** Classes 53 and 220 are both decoded by subclass: below 7 is an owned mine (u32 owner), 7 and up is an abandoned mine (resource mask).
- **AB heroes** store one spell byte. 254 means "default spellbook" (VCMI's PRESET), and 352 of the corpus heroes use it.
- **RoE/AB hero experience** 0 means "use the default".
- **SoD `hero_settings`** often hold entries flagged as customised with every field empty. They are kept, because dropping them would change the bytes.
- **Trailing bytes and first templates.** Editor maps end with 124 zero bytes after the events and start their template list with `AVWmrnd0` and `AVLholg0`. The two maps from the random map generator have no trailing bytes and use `AVLhlds0` as the second template.
- **Victory artifact width.** `transport_artifact` stores its artifact as a u8 even in SoD; `acquire_artifact` uses the version's width.
