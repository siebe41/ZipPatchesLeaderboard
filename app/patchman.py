"""PatchMan: a side game that shares the app and nothing else.

Isolation is the whole point of this module, so it is worth being explicit
about what that means:

* It never opens ``leaderboard.json`` or ``history.json`` at all, for reading or
  for writing. Players type their own name, so there is no roster to consult.
* It never touches a daily rank, penalty, streak, excused day, weekly total or
  win counter. Nothing in here knows those concepts exist.
* It adds no scheduled job. Seasons are derived from ``created_at`` when a board
  is queried, so there is no rollover to run and nothing to get out of step.
* Its tables are prefixed ``patchman_`` and live in the SQLite file the app
  already keeps on the data volume, so a deploy stays "upload files, restart".

The rules of the game itself live in ``patchman/sim.mjs`` and every tuning value
lives in ``patchman/config.mjs``. That simulation is mirrored here, exactly, so
the server can replay a submitted run rather than take its word for the score.
``tools/check_patchman_parity.py`` fails the moment the two engines disagree.

On trusting submissions. A deterministic game is replayable, and a replayable
run is forgeable: anyone can import the client's own rules, search for a good
input trace offline, and hand over a trace that replays perfectly. Replay proves
a trace is self-consistent, never that a person produced it. So the seed is
issued by the server and spent once, the run is paced against the server's own
clock through heartbeats, and the trace is measured for input a human hand
cannot produce. Replay is the floor here, not the ceiling.

On matching the JavaScript exactly. This game holds every position as an
integer number of sub-units rather than as a float, which removes the usual
source of drift between two ports: there is no accumulated rounding to drift.
The generator is the only remaining hazard, because JavaScript's bitwise
operators coerce to 32 bits, so its state is held here as an unsigned Python
int and masked after every operation.

None of the artwork, the maze, the characters or the names come from any
existing arcade game. It is a maze chase, which is a genre, built out of Patch
My PC's own material.
"""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, FileResponse
from pydantic import BaseModel, Field
import json
import logging
import math
import os
import re
import secrets
import sqlite3
import threading
import time
from collections import deque
from datetime import datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo  # Python 3.9+ (needs tzdata on slim images)
except ImportError:  # pragma: no cover
    ZoneInfo = None


router = APIRouter(prefix="/patchman", tags=["patchman"])

log = logging.getLogger("patchman")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "patchman")

# Read-only, and only the buffer database. This module deliberately does not
# open the real leaderboard state file, because names here are free text.
BUFFER_DB = os.environ.get("ZS_BUFFER_DB", "/home/zipscores_buffer.db")
TIMEZONE = os.environ.get("ZS_TIMEZONE", "America/Chicago")

SEASON_HISTORY_LIMIT = 12  # past seasons kept in the hall of fame
SEASON_SEARCH_LIMIT = 60   # how far back to look for them, in months
BOARD_LIMIT = 10

VIEWS = ("alltime", "season", "today", "volume")
VIEW_TITLES = {
    "alltime": "All time",
    "season": "This season",
    "today": "Today",
    "volume": "Most patched",
}


# --------------------------------------------------------------------------- #
# Time
# --------------------------------------------------------------------------- #

def _tz():
    if ZoneInfo is not None:
        try:
            return ZoneInfo(TIMEZONE)
        except Exception:
            pass
    return timezone.utc


def _utc_stamp(when=None):
    """A fixed-width UTC stamp, so string comparison is also time comparison."""
    when = when or datetime.now(timezone.utc)
    return when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_stamp(text):
    try:
        return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _day_bounds(local_day):
    """UTC bounds of one local calendar day.

    Converting in Python rather than shifting by a fixed offset in SQL is what
    keeps this right on the two days a year the offset changes.
    """
    tz = _tz()
    start = datetime(local_day.year, local_day.month, local_day.day, tzinfo=tz)
    return _utc_stamp(start), _utc_stamp(start + timedelta(days=1))


def _month_bounds(year, month):
    tz = _tz()
    start = datetime(year, month, 1, tzinfo=tz)
    end = datetime(year + (month == 12), 1 if month == 12 else month + 1, 1, tzinfo=tz)
    return _utc_stamp(start), _utc_stamp(end)


def _season_of(stamp):
    """The season a run belongs to, worked out from its timestamp on demand.

    Nothing stamps a season on a row and nothing rolls one over. A run written
    in March is a March run forever, whatever the code is doing later.
    """
    when = _parse_stamp(stamp)
    if when is None:
        return ""
    local = when.astimezone(_tz())
    return "%04d-%02d" % (local.year, local.month)


def _season_label(season):
    try:
        year, month = season.split("-")
        return datetime(int(year), int(month), 1).strftime("%B %Y")
    except Exception:
        return season


def _local_now():
    return datetime.now(_tz())


# --------------------------------------------------------------------------- #
# Storage
# --------------------------------------------------------------------------- #

_db_lock = threading.Lock()
_db_dir = os.path.dirname(os.path.abspath(BUFFER_DB))
if _db_dir:
    os.makedirs(_db_dir, exist_ok=True)

# A separate connection to the same file, with a busy timeout so a write here
# waits for the collector rather than failing. The journal mode is deliberately
# left alone: it is a property of the database, not of this connection, and
# changing it would change it for main.py too.
_conn = sqlite3.connect(BUFFER_DB, check_same_thread=False, timeout=10.0)
_conn.row_factory = sqlite3.Row
_conn.execute(
    """
    CREATE TABLE IF NOT EXISTS patchman_runs (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        player      TEXT    NOT NULL,
        player_key  TEXT    NOT NULL,
        score       INTEGER NOT NULL,
        seed        INTEGER NOT NULL,
        duration_ms INTEGER NOT NULL,
        turns       TEXT,
        created_at  TEXT    NOT NULL,
        verified    INTEGER NOT NULL DEFAULT 0,
        session_id  TEXT,
        elapsed_ms  INTEGER,
        turn_count  INTEGER,
        level       INTEGER,
        patches     INTEGER,
        flags       TEXT
    )
    """
)

# A run is issued before it is played, so the server knows which world the
# player was given and when the clock started. One row per attempt, spent once.
_conn.execute(
    """
    CREATE TABLE IF NOT EXISTS patchman_sessions (
        id          TEXT    PRIMARY KEY,
        seed        INTEGER NOT NULL,
        issued_at   INTEGER NOT NULL,
        issued_ip   TEXT,
        first_beat  INTEGER,
        first_tick  INTEGER,
        last_beat   INTEGER,
        last_tick   INTEGER,
        beats       INTEGER NOT NULL DEFAULT 0,
        consumed_at INTEGER
    )
    """
)

# Columns added after a release land here rather than in a migration step,
# because the deploy is "upload files, restart" and there is nowhere for a
# migration step to run. Adding to this tuple is the whole procedure.
_EXTRA_RUN_COLUMNS = (
    ("session_id", "TEXT"),
    ("elapsed_ms", "INTEGER"),
    ("turn_count", "INTEGER"),
    ("level", "INTEGER"),
    ("patches", "INTEGER"),
    ("flags", "TEXT"),
)
_have = {r["name"] for r in _conn.execute("PRAGMA table_info(patchman_runs)")}
for _name, _type in _EXTRA_RUN_COLUMNS:
    if _name not in _have:
        _conn.execute("ALTER TABLE patchman_runs ADD COLUMN %s %s" % (_name, _type))

_conn.execute("CREATE INDEX IF NOT EXISTS idx_patchman_created ON patchman_runs(created_at)")
_conn.execute("CREATE INDEX IF NOT EXISTS idx_patchman_player ON patchman_runs(player_key)")
_conn.execute("CREATE INDEX IF NOT EXISTS idx_patchman_score ON patchman_runs(score DESC, id ASC)")
_conn.execute("CREATE INDEX IF NOT EXISTS idx_patchman_sess ON patchman_sessions(issued_at)")
_conn.commit()


def _rows(sql, params=()):
    with _db_lock:
        return [dict(r) for r in _conn.execute(sql, params).fetchall()]


def _write(sql, params=()):
    with _db_lock:
        cur = _conn.execute(sql, params)
        _conn.commit()
        return cur.lastrowid


# --------------------------------------------------------------------------- #
# Player names
# --------------------------------------------------------------------------- #

# Matches the maxlength on the name box. A cap belongs on the server too,
# because the client is not the only thing that can post a score.
MAX_NAME_LEN = 60


def name_key(name):
    return clean_player_name(name).lower()


def clean_player_name(name):
    cleaned = str(name or "")
    cleaned = re.sub(r"[\x00-\x1F\x7F]", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip()[:MAX_NAME_LEN].strip()


def resolve_player(name):
    """Take the name as typed, once it has been sanitised.

    This board is open to anyone who wants to play, so a name is free text and
    is deliberately not checked against the real leaderboard roster. Runs are
    grouped by ``name_key``, so casing and stray spacing still collapse to a
    single player, and the board shows the most recent spelling.
    """
    cleaned = clean_player_name(name)
    if not cleaned:
        return "", "Enter your name so the run can be posted."
    return cleaned, None


# --------------------------------------------------------------------------- #
# Board queries
# --------------------------------------------------------------------------- #

# One row per player, their best qualifying run, earliest first on a tie. The
# id is the final tiebreak because it is monotonic, so two runs written in the
# same second still have a defined order.
#
# Only verified runs are eligible. A run that failed a check is still stored,
# because throwing away the evidence would make a wrong call impossible to
# review, but it does not appear on any board.
_BEST_PER_PLAYER = """
    WITH ranked AS (
        SELECT id, player, player_key, score, seed, duration_ms, created_at,
               level, patches,
               ROW_NUMBER() OVER (
                   PARTITION BY player_key
                   ORDER BY score DESC, created_at ASC, id ASC
               ) AS rn
        FROM patchman_runs
        WHERE score > 0 AND verified = 1 %s
    )
    SELECT id, player, player_key, score, seed, duration_ms, created_at,
           level, patches
    FROM ranked
    WHERE rn = 1
    ORDER BY score DESC, created_at ASC, id ASC
"""

# Cumulative score. The tiebreak is the earliest *last* run, because the player
# who got to the total first is the one who stopped needing runs first.
_VOLUME = """
    SELECT player_key,
           SUM(score)         AS total,
           COUNT(*)           AS runs,
           MAX(score)         AS best,
           COALESCE(SUM(patches), 0) AS patches,
           MAX(created_at)    AS last_at
    FROM patchman_runs
    WHERE score > 0 AND verified = 1 %s
    GROUP BY player_key
    ORDER BY total DESC, last_at ASC, player_key ASC
"""


def _range_clause(bounds, column="created_at"):
    if not bounds:
        return "", ()
    return " AND %s >= ? AND %s < ?" % (column, column), tuple(bounds)


def _display_names():
    """Latest spelling seen for each player key, from runs that counted."""
    rows = _rows(
        "SELECT player_key, player FROM patchman_runs "
        "WHERE id IN (SELECT MAX(id) FROM patchman_runs "
        "             WHERE verified = 1 GROUP BY player_key)"
    )
    return {r["player_key"]: r["player"] for r in rows}


def _bounds_for(view, now=None):
    now = now or _local_now()
    if view == "season":
        return _month_bounds(now.year, now.month)
    if view == "today":
        return _day_bounds(now.date())
    return None


def board_rows(view):
    """Ranked rows for one view. Rank is dense over the ordering, not the index."""
    bounds = _bounds_for(view)
    clause, params = _range_clause(bounds)

    if view == "volume":
        names = _display_names()
        raw = _rows(_VOLUME % clause, params)
        out = []
        for i, r in enumerate(raw):
            out.append({
                "rank": i + 1,
                "player": names.get(r["player_key"], r["player_key"]),
                "player_key": r["player_key"],
                "score": r["total"],
                "runs": r["runs"],
                "best": r["best"],
                "patches": r["patches"],
                "created_at": r["last_at"],
                "season": _season_of(r["last_at"]),
            })
        return out

    raw = _rows(_BEST_PER_PLAYER % clause, params)
    out = []
    for i, r in enumerate(raw):
        out.append({
            "rank": i + 1,
            "player": r["player"],
            "player_key": r["player_key"],
            "score": r["score"],
            "seed": r["seed"],
            "duration_ms": r["duration_ms"],
            "level": r["level"],
            "patches": r["patches"],
            "created_at": r["created_at"],
            "season": _season_of(r["created_at"]),
        })
    return out


def board_view(view, player_key=None, limit=BOARD_LIMIT):
    """The top slice, plus the asking player's own row when it falls outside it.

    Being ranked 41st is only discouraging if you cannot see it, so the row is
    pinned rather than dropped.
    """
    rows = board_rows(view)
    top = rows[:limit]
    pinned = None
    if player_key:
        for r in rows:
            if r["player_key"] == player_key:
                if r["rank"] > limit:
                    pinned = r
                break
    return {
        "view": view,
        "title": VIEW_TITLES[view],
        "rows": top,
        "pinned": pinned,
        "players": len(rows),
    }


def hall_of_fame():
    """Winners of finished seasons, newest first.

    One query per season rather than a single grouped query, because a season
    boundary is a local-time boundary and a fixed offset in SQL gets the two
    DST changeovers wrong.

    It walks backwards from last month and keeps the seasons that actually
    produced a run, so a quiet stretch does not push the real winners off the
    list. The walk stops at the earliest run, and in any case at
    ``SEASON_SEARCH_LIMIT`` months, so the number of queries stays bounded.
    """
    first = _rows("SELECT MIN(created_at) AS m FROM patchman_runs "
                  "WHERE score > 0 AND verified = 1")
    if not first or not first[0]["m"]:
        return []
    earliest = _parse_stamp(first[0]["m"])
    if earliest is None:
        return []

    now = _local_now()
    local_first = earliest.astimezone(_tz())
    oldest = (local_first.year, local_first.month)

    year, month = (now.year - 1, 12) if now.month == 1 else (now.year, now.month - 1)
    out = []
    for _ in range(SEASON_SEARCH_LIMIT):
        if (year, month) < oldest or len(out) >= SEASON_HISTORY_LIMIT:
            break
        clause, params = _range_clause(_month_bounds(year, month))
        rows = _rows(_BEST_PER_PLAYER % clause, params)
        if rows:
            winner = rows[0]
            out.append({
                "season": "%04d-%02d" % (year, month),
                "label": _season_label("%04d-%02d" % (year, month)),
                "player": winner["player"],
                "score": winner["score"],
                "created_at": winner["created_at"],
                "players": len(rows),
            })
        year, month = (year - 1, 12) if month == 1 else (year, month - 1)
    return out


def player_summary(name):
    canonical, error = resolve_player(name)
    if error:
        return {"ok": False, "error": error}
    key = name_key(canonical)

    # Names are free text, so one player can arrive spelled differently on
    # different runs. The board shows the most recent spelling, so show that
    # here too rather than echoing whatever was typed into the URL.
    stored = _display_names().get(key)

    totals = _rows(
        "SELECT COUNT(*) AS runs, COALESCE(SUM(score), 0) AS total, "
        "COALESCE(MAX(score), 0) AS best, COALESCE(SUM(patches), 0) AS patches, "
        "COALESCE(MAX(level), 0) AS deepest FROM patchman_runs "
        "WHERE player_key = ? AND verified = 1",
        (key,),
    )[0]

    def best_in(bounds):
        clause, params = _range_clause(bounds)
        sql = ("SELECT COALESCE(MAX(score), 0) AS best FROM patchman_runs "
               "WHERE player_key = ? AND verified = 1" + clause)
        return _rows(sql, (key,) + params)[0]["best"]

    ranks = {}
    for view in VIEWS:
        ranks[view] = next(
            (r["rank"] for r in board_rows(view) if r["player_key"] == key), None)

    recent = _rows(
        "SELECT score, duration_ms, level, patches, created_at FROM patchman_runs "
        "WHERE player_key = ? AND verified = 1 ORDER BY id DESC LIMIT 10",
        (key,),
    )

    return {
        "ok": True,
        "player": stored or canonical,
        "runs": totals["runs"],
        "total": totals["total"],
        "best": totals["best"],
        "patches": totals["patches"],
        "deepest_level": totals["deepest"],
        "best_season": best_in(_bounds_for("season")),
        "best_today": best_in(_bounds_for("today")),
        "ranks": ranks,
        "recent": recent,
    }


# --------------------------------------------------------------------------- #
# The simulation, ported from patchman/sim.mjs
# --------------------------------------------------------------------------- #
#
# This is the same game the browser runs, in Python, tick for tick. It exists so
# that a submitted score is something the server works out rather than something
# the client asserts. Everything below is a direct translation; if it needs to
# change, change patchman/sim.mjs first and keep the two in step.

MAZE = [
    '###########################',
    '#.........................#',
    '#.###.#######.#######.###.#',
    '#.###.#######.#######.###.#',
    '#.###.#######.#######.###.#',
    '#o###.................###o#',
    '#.#######.#######.#######.#',
    '#.#######.#######.#######.#',
    '#.#######.#######.#######.#',
    '#.........#######.........#',
    '#.###.###.#######.###.###.#',
    '#.###.###.#######.###.###.#',
    '#.###.###.#######.###.###.#',
    '#.###.###.#######.###.###.#',
    '     .................     ',
    '#.#######.###-###.#######.#',
    '#.#######.#     #.#######.#',
    '#.#######.#     #.#######.#',
    '#.#######.#######.#######.#',
    '#.....###.... ....###.....#',
    '#.###.#######.#######.###.#',
    '#.###.#######.#######.###.#',
    '#.###.#######.#######.###.#',
    '#.###.#######.#######.###.#',
    '#o###........ ........###o#',
    '#.###.###.#######.###.###.#',
    '#.###.###.#######.###.###.#',
    '#.###.###.#######.###.###.#',
    '#.###.###.#######.###.###.#',
    '#.........................#',
    '###########################',
]

COLS = 27
ROWS = 31
CELL = 64
HALF = CELL // 2
WORLD_W = COLS * CELL

STEP_MS = 1000.0 / 120.0

WALL, PATCH, LOGO, DOOR, FLOOR = '#', '.', 'o', '-', ' '

UP, LEFT, DOWN, RIGHT = 0, 1, 2, 3
DX = (0, -1, 0, 1)
DY = (-1, 0, 1, 0)
OPPOSITE = (DOWN, RIGHT, UP, LEFT)

IDLE, READY, PLAYING, DYING, CLEAR, DEAD = (
    "idle", "ready", "playing", "dying", "clear", "dead")
V_HOUSE, V_LEAVING, V_OUT, V_EYES, V_ENTERING = (
    "house", "leaving", "out", "eyes", "entering")

FAR = 1 << 30

# Speed tiers, in sub-units per tick, and the level -> tier map.
SPEEDS = (
    {"patchman": 4, "energized": 5, "vuln": 3, "elroy": 4, "frightened": 2, "eyes": 8, "tunnel": 2},
    {"patchman": 4, "energized": 5, "vuln": 3, "elroy": 4, "frightened": 2, "eyes": 8, "tunnel": 2},
    {"patchman": 5, "energized": 5, "vuln": 4, "elroy": 5, "frightened": 2, "eyes": 8, "tunnel": 3},
    {"patchman": 5, "energized": 6, "vuln": 4, "elroy": 5, "frightened": 3, "eyes": 8, "tunnel": 3},
)
SPEED_TIER = (0, 0, 1, 1, 2, 2, 2, 3)

PATCH_POINTS = 10
LOGO_POINTS = 50
VULN_POINTS = (200, 400, 800, 1600)
LEVEL_BONUS = 500

READY_TICKS = 150
DEATH_TICKS = 180
LEVEL_CLEAR_TICKS = 150
EAT_FREEZE_TICKS = 45
BONUS_TICKS = 1080

FRIGHTENED_TICKS = (960, 840, 720, 600, 480, 360, 300, 240, 180, 120)

PHASES_EARLY = (
    ("scatter", 840), ("chase", 2400), ("scatter", 840), ("chase", 2400),
    ("scatter", 600), ("chase", 2400), ("scatter", 600), ("chase", 0),
)
PHASES_LATE = (
    ("scatter", 600), ("chase", 2400), ("scatter", 600), ("chase", 3000),
    ("scatter", 600), ("chase", 3000), ("scatter", 360), ("chase", 0),
)
PHASES_LATE_FROM_LEVEL = 5

RELEASE_AT = (0, 0, 20, 50)
RELEASE_IDLE_TICKS = 480
HOUSE_LANE_ROW = 16
HOUSE_EXIT_ROW = 14
DOOR_COL = 13
HOME_COLS = (13, 13, 11, 15)
HOUSE_BOB_UNITS = 16

ELROY_AT = 40
ELROY_AT_HARDER = 18

BONUS_AT = (70, 170)
BONUS_TILE = (13, 19)
BONUS_POINTS = (100, 300, 500, 700, 1000, 2000, 3000, 5000)

LIVES = 3
SCATTER_TILES = ((25, 0), (1, 0), (25, 30), (1, 30))
AMBUSH_TILES = 4
FLANK_TILES = 2
TIMID_TILES = 8

START_TILE = (13, 24)

MAX_INPUT_TRACE = 4000
ABSOLUTE_MAX_TICKS = 120 * 60 * 12   # twelve minutes, the ceiling both engines share
TAIL_TICKS = 120 * 120               # room for the last lives to end without input

MAX_SCORE = 10_000_000


def _u32(x):
    return x & 0xFFFFFFFF


def _make_rng(seed):
    """mulberry32, matching patchman/rng.mjs.

    Every step in the JavaScript is a 32-bit bit pattern, and whether that
    pattern is read as signed or unsigned never changes the bits themselves,
    only how they print. Holding the state unsigned and masking after each
    operation is therefore the same generator, not an approximation of it.
    """
    a = _u32(seed)

    def nxt():
        nonlocal a
        a = _u32(a + 0x6D2B79F5)
        t = a
        t = _u32(_u32(t ^ (t >> 15)) * _u32(t | 1))
        t = _u32(t ^ _u32(t + _u32(_u32(t ^ (t >> 7)) * _u32(t | 61))))
        return _u32(t ^ (t >> 14)) / 4294967296.0

    return nxt


# --- The board, derived once at import ------------------------------------- #

_GRID = [list(row) for row in MAZE]


def _wrap_col(c):
    return c % COLS


def _tile_char(c, r):
    if r < 0 or r >= ROWS:
        return WALL
    return _GRID[r][_wrap_col(c)]


def _is_wall(c, r):
    return _tile_char(c, r) == WALL


def _is_door(c, r):
    return _tile_char(c, r) == DOOR


def _is_house(c, r):
    col = _wrap_col(c)
    return HOUSE_LANE_ROW <= r <= HOUSE_LANE_ROW + 1 and 11 <= col <= 15


def _is_tunnel(c, r):
    if r != 14:
        return False
    col = _wrap_col(c)
    return col <= 4 or col >= COLS - 5


def _fresh_patches():
    tiles = []
    count = 0
    for r in range(ROWS):
        for c in range(COLS):
            ch = _GRID[r][c]
            if ch == PATCH or ch == LOGO:
                tiles.append(ch)
                count += 1
            else:
                tiles.append(FLOOR)
    return tiles, count


TOTAL_PATCHES = _fresh_patches()[1]


def _build_home_distance():
    """Steps from every tile back to the corridor above the door.

    The chase rules are greedy, which is what gives each vulnerability a
    personality but is no guarantee of arriving anywhere. Going home has to
    actually arrive, so it follows this instead: exact, loop-free, and nothing
    but integer counting, so it cannot differ between two languages.
    """
    dist = [-1] * (COLS * ROWS)
    goal = DOOR_COL + HOUSE_EXIT_ROW * COLS
    dist[goal] = 0
    queue = deque([goal])
    while queue:
        at = queue.popleft()
        c = at % COLS
        r = at // COLS
        for d in range(4):
            nr = r + DY[d]
            if nr < 0 or nr >= ROWS:
                continue
            nc = _wrap_col(c + DX[d])
            idx = nr * COLS + nc
            if dist[idx] >= 0:
                continue
            if _is_wall(nc, nr) or _is_door(nc, nr) or _is_house(nc, nr):
                continue
            dist[idx] = dist[at] + 1
            queue.append(idx)
    return dist


HOME_DISTANCE = _build_home_distance()


def _home_distance(c, r):
    if r < 0 or r >= ROWS:
        return -1
    return HOME_DISTANCE[r * COLS + _wrap_col(c)]


def _center_x(c):
    return c * CELL + HALF


def _center_y(r):
    return r * CELL + HALF


def _step_to_center(p, delta):
    """Sub-units to the next tile centre ahead. Between 1 and CELL, never 0."""
    off = p % CELL
    if delta > 0:
        d = (HALF - off + CELL) % CELL
        return CELL if d == 0 else d
    d = (off - HALF + CELL) % CELL
    return CELL if d == 0 else d


def _tier(level):
    i = min(level - 1, len(SPEED_TIER) - 1)
    return SPEEDS[SPEED_TIER[max(0, i)]]


def _fright_ticks_for(level):
    return FRIGHTENED_TICKS[min(level - 1, len(FRIGHTENED_TICKS) - 1)]


def _phases_for(level):
    return PHASES_LATE if level >= PHASES_LATE_FROM_LEVEL else PHASES_EARLY


def _bonus_points_for(level):
    return BONUS_POINTS[min(level - 1, len(BONUS_POINTS) - 1)]


class _Vuln:
    __slots__ = ("index", "x", "y", "dir", "state", "fright", "bob", "bob_dir")

    def __init__(self, index):
        self.index = index
        self.x = 0
        self.y = 0
        self.dir = LEFT
        self.state = V_HOUSE
        self.fright = False
        self.bob = 0
        self.bob_dir = 1


class Sim:
    """One run. Advance it with step() and nothing else."""

    def __init__(self, seed):
        self.seed = _u32(seed)
        self._rng = _make_rng(seed)
        self.tick = 0
        self.state = IDLE
        self.level = 1
        self.score = 0
        self.lives = LIVES
        self.tiles = []
        self.patches_left = 0
        self.patches_eaten = 0
        self.total_patches = 0
        self.pac_x = 0
        self.pac_y = 0
        self.pac_dir = LEFT
        self.pac_want = LEFT
        self.vulns = [_Vuln(0), _Vuln(1), _Vuln(2), _Vuln(3)]
        self.phase_index = 0
        self.phase_ticks = 0
        self.phase_kind = "scatter"
        self.fright_ticks = 0
        self.fright_chain = 0
        # Cumulative across the whole run, unlike fright_chain which resets
        # with every window. It is what the scoreboard means by
        # "vulnerabilities patched", and it also gives the parity check
        # something durable to compare, since a chain that has already ended
        # leaves no other trace.
        self.vulns_patched = 0
        self.elroy_stage = 0
        self.freeze_ticks = 0
        self.ready_ticks = 0
        self.death_ticks = 0
        self.clear_ticks = 0
        self.house_idle = 0
        self.bonus_state = "none"
        self.bonus_ticks = 0
        self.bonuses_shown = 0
        self.play_start_tick = -1
        self.end_tick = -1
        self.turns = []
        self.pending = []
        self.last_queued_dir = -1
        self._load_level()

    # --- Setup ------------------------------------------------------------ #

    def _load_level(self):
        self.tiles, self.patches_left = _fresh_patches()
        self.patches_eaten = 0
        self.bonuses_shown = 0
        self.bonus_state = "none"
        self.bonus_ticks = 0
        self._reset_actors()

    def _reset_actors(self):
        self.pac_x = _center_x(START_TILE[0])
        self.pac_y = _center_y(START_TILE[1])
        self.pac_dir = LEFT
        self.pac_want = LEFT

        lane_y = _center_y(HOUSE_LANE_ROW)
        for i in range(4):
            g = self.vulns[i]
            g.fright = False
            g.bob = i * 8 - 12
            g.bob_dir = 1
            if i == 0:
                g.x = _center_x(DOOR_COL)
                g.y = _center_y(HOUSE_EXIT_ROW)
                g.dir = LEFT
                g.state = V_OUT
            else:
                g.x = _center_x(HOME_COLS[i])
                g.y = lane_y + g.bob
                g.dir = UP if i == 2 else DOWN
                g.state = V_HOUSE

        self.phase_index = 0
        self.phase_ticks = 0
        self.phase_kind = _phases_for(self.level)[0][0]
        self.fright_ticks = 0
        self.fright_chain = 0
        self.freeze_ticks = 0
        self.house_idle = 0
        self.ready_ticks = READY_TICKS

    # --- Input ------------------------------------------------------------ #

    def queue_turn(self, at_tick, direction):
        if direction < 0 or direction > 3:
            return
        if self.last_queued_dir == direction:
            return
        self.last_queued_dir = direction
        t = max(self.tick, int(math.floor(at_tick)))
        self.pending.append(t * 4 + direction)

    # --- Speeds ----------------------------------------------------------- #

    def _pac_speed(self):
        s = _tier(self.level)
        return s["energized"] if self.fright_ticks > 0 else s["patchman"]

    def _vuln_speed(self, g):
        s = _tier(self.level)
        if g.state == V_EYES or g.state == V_ENTERING:
            return s["eyes"]
        if g.fright:
            return s["frightened"]
        if _is_tunnel(g.x // CELL, g.y // CELL):
            return s["tunnel"]
        if g.index == 0 and self.elroy_stage > 0:
            return s["elroy"]
        return s["vuln"]

    # --- Targeting -------------------------------------------------------- #

    def _target_tile(self, g):
        scatter = self.phase_kind == "scatter"
        pc = self.pac_x // CELL
        pr = self.pac_y // CELL
        pd = self.pac_dir

        if g.index == 0:
            if scatter and self.elroy_stage < 2:
                return SCATTER_TILES[0]
            return (pc, pr)
        if scatter:
            return SCATTER_TILES[g.index]

        if g.index == 1:
            n = AMBUSH_TILES
            return (pc + DX[pd] * n, pr + DY[pd] * n)
        if g.index == 2:
            n = FLANK_TILES
            ax = pc + DX[pd] * n
            ay = pr + DY[pd] * n
            lead = self.vulns[0]
            return (2 * ax - lead.x // CELL, 2 * ay - lead.y // CELL)

        gc = g.x // CELL
        gr = g.y // CELL
        dx = gc - pc
        dy = gr - pr
        if dx * dx + dy * dy > TIMID_TILES * TIMID_TILES:
            return (pc, pr)
        return SCATTER_TILES[3]

    def _choose_dir(self, g):
        c = g.x // CELL
        r = g.y // CELL
        back = OPPOSITE[g.dir]

        opts = []
        for d in range(4):
            if d == back:
                continue
            nr = r + DY[d]
            if nr < 0 or nr >= ROWS:
                continue
            nc = _wrap_col(c + DX[d])
            if _is_wall(nc, nr) or _is_door(nc, nr) or _is_house(nc, nr):
                continue
            opts.append(d)

        if not opts:
            g.dir = back
            return
        if len(opts) == 1:
            g.dir = opts[0]
            return

        if g.fright:
            # The only place the simulation draws a random number.
            g.dir = opts[int(math.floor(self._rng() * len(opts)))]
            return

        if g.state == V_EYES:
            best = opts[0]
            best_d = FAR
            for d in opts:
                dist = _home_distance(c + DX[d], r + DY[d])
                if 0 <= dist < best_d:
                    best_d = dist
                    best = d
            g.dir = best
            return

        tc, tr = self._target_tile(g)
        best = opts[0]
        best_d = FAR
        for d in opts:
            dx = c + DX[d] - tc
            dy = r + DY[d] - tr
            dist = dx * dx + dy * dy
            if dist < best_d:
                best_d = dist
                best = d
        g.dir = best

    # --- Movement --------------------------------------------------------- #

    def _pac_can_go(self, direction):
        c = self.pac_x // CELL
        r = self.pac_y // CELL
        nr = r + DY[direction]
        if nr < 0 or nr >= ROWS:
            return False
        nc = _wrap_col(c + DX[direction])
        return not (_is_wall(nc, nr) or _is_door(nc, nr) or _is_house(nc, nr))

    def _move_pac(self):
        remaining = self._pac_speed()

        if self.pac_want == OPPOSITE[self.pac_dir] and self._pac_can_go(self.pac_want):
            self.pac_dir = self.pac_want

        while remaining > 0:
            if self.pac_x % CELL == HALF and self.pac_y % CELL == HALF:
                if self.pac_want != self.pac_dir and self._pac_can_go(self.pac_want):
                    self.pac_dir = self.pac_want
                if not self._pac_can_go(self.pac_dir):
                    break
            dx = DX[self.pac_dir]
            dy = DY[self.pac_dir]
            d = _step_to_center(self.pac_x, dx) if dx != 0 else _step_to_center(self.pac_y, dy)
            m = d if d < remaining else remaining
            self.pac_x = (self.pac_x + dx * m) % WORLD_W
            self.pac_y += dy * m
            remaining -= m
            if self.pac_x % CELL == HALF and self.pac_y % CELL == HALF:
                self._collect()

    def _move_vuln_maze(self, g):
        remaining = self._vuln_speed(g)
        while remaining > 0:
            if g.x % CELL == HALF and g.y % CELL == HALF:
                if (g.state == V_EYES and g.x // CELL == DOOR_COL
                        and g.y // CELL == HOUSE_EXIT_ROW):
                    g.state = V_ENTERING
                    g.dir = DOWN
                    return
                self._choose_dir(g)
            dx = DX[g.dir]
            dy = DY[g.dir]
            d = _step_to_center(g.x, dx) if dx != 0 else _step_to_center(g.y, dy)
            m = d if d < remaining else remaining
            g.x = (g.x + dx * m) % WORLD_W
            g.y += dy * m
            remaining -= m

    def _move_house(self, g):
        lane_y = _center_y(HOUSE_LANE_ROW)
        g.bob += g.bob_dir
        if g.bob >= HOUSE_BOB_UNITS:
            g.bob = HOUSE_BOB_UNITS
            g.bob_dir = -1
        elif g.bob <= -HOUSE_BOB_UNITS:
            g.bob = -HOUSE_BOB_UNITS
            g.bob_dir = 1
        g.y = lane_y + g.bob
        g.dir = DOWN if g.bob_dir > 0 else UP

    def _move_leaving(self, g):
        # The first leg is gated on still being off the door column, and that
        # gate is load-bearing. Climbing out moves off the lane row, so an
        # ungated first leg sees "not on the lane" and drags it straight back
        # down; the two legs undo each other and the thing never gets out.
        lane_y = _center_y(HOUSE_LANE_ROW)
        door_x = _center_x(DOOR_COL)
        exit_y = _center_y(HOUSE_EXIT_ROW)
        remaining = self._vuln_speed(g)

        while remaining > 0:
            if g.x != door_x and g.y != lane_y:
                gap = lane_y - g.y if lane_y > g.y else g.y - lane_y
                m = gap if gap < remaining else remaining
                g.dir = DOWN if lane_y > g.y else UP
                g.y += m if lane_y > g.y else -m
                remaining -= m
            elif g.x != door_x:
                gap = door_x - g.x if door_x > g.x else g.x - door_x
                m = gap if gap < remaining else remaining
                g.dir = RIGHT if door_x > g.x else LEFT
                g.x += m if door_x > g.x else -m
                remaining -= m
            elif g.y > exit_y:
                gap = g.y - exit_y
                m = gap if gap < remaining else remaining
                g.dir = UP
                g.y -= m
                remaining -= m
            else:
                g.state = V_OUT
                g.dir = LEFT
                return

    def _move_entering(self, g):
        lane_y = _center_y(HOUSE_LANE_ROW)
        door_x = _center_x(DOOR_COL)
        remaining = self._vuln_speed(g)

        while remaining > 0:
            if g.x != door_x:
                gap = door_x - g.x if door_x > g.x else g.x - door_x
                m = gap if gap < remaining else remaining
                g.dir = RIGHT if door_x > g.x else LEFT
                g.x += m if door_x > g.x else -m
                remaining -= m
            elif g.y < lane_y:
                gap = lane_y - g.y
                m = gap if gap < remaining else remaining
                g.dir = DOWN
                g.y += m
                remaining -= m
            else:
                g.state = V_LEAVING
                g.fright = False
                return

    # --- Collecting ------------------------------------------------------- #

    def _collect(self):
        c = self.pac_x // CELL
        r = self.pac_y // CELL
        i = r * COLS + c
        ch = self.tiles[i]

        if ch == PATCH:
            self.tiles[i] = FLOOR
            self.patches_left -= 1
            self.patches_eaten += 1
            self.total_patches += 1
            self.score += PATCH_POINTS
            self.house_idle = 0
            self._check_bonus_spawn()
        elif ch == LOGO:
            self.tiles[i] = FLOOR
            self.patches_left -= 1
            self.patches_eaten += 1
            self.total_patches += 1
            self.score += LOGO_POINTS
            self.house_idle = 0
            self._energize()
            self._check_bonus_spawn()

        if self.bonus_state == "up" and c == BONUS_TILE[0] and r == BONUS_TILE[1]:
            self.score += _bonus_points_for(self.level)
            self.bonus_state = "eaten"

        self._update_elroy()

    def _check_bonus_spawn(self):
        if self.bonuses_shown >= len(BONUS_AT):
            return
        if self.patches_eaten < BONUS_AT[self.bonuses_shown]:
            return
        self.bonuses_shown += 1
        self.bonus_state = "up"
        self.bonus_ticks = BONUS_TICKS

    def _update_elroy(self):
        if self.patches_left <= ELROY_AT_HARDER:
            self.elroy_stage = 2
        elif self.patches_left <= ELROY_AT:
            self.elroy_stage = 1
        else:
            self.elroy_stage = 0

    def _energize(self):
        self.fright_ticks = _fright_ticks_for(self.level)
        self.fright_chain = 0
        for g in self.vulns:
            if g.state == V_EYES or g.state == V_ENTERING:
                continue
            g.fright = True
            if g.state == V_OUT:
                g.dir = OPPOSITE[g.dir]

    # --- Phases ----------------------------------------------------------- #

    def _update_phase(self):
        if self.fright_ticks > 0:
            self.fright_ticks -= 1
            if self.fright_ticks == 0:
                for g in self.vulns:
                    g.fright = False
                self.fright_chain = 0
            return

        phases = _phases_for(self.level)
        if self.phase_index >= len(phases):
            return
        length = phases[self.phase_index][1]
        if length == 0:
            return

        self.phase_ticks += 1
        if self.phase_ticks < length:
            return

        self.phase_ticks = 0
        self.phase_index += 1
        if self.phase_index >= len(phases):
            self.phase_index = len(phases) - 1
        self.phase_kind = phases[self.phase_index][0]
        for g in self.vulns:
            if g.state == V_OUT:
                g.dir = OPPOSITE[g.dir]

    def _release_vulns(self):
        self.house_idle += 1
        for i in range(1, 4):
            g = self.vulns[i]
            if g.state != V_HOUSE:
                continue
            due = (self.patches_eaten >= RELEASE_AT[i]
                   or self.house_idle >= RELEASE_IDLE_TICKS)
            if not due:
                break
            g.state = V_LEAVING
            self.house_idle = 0
            break

    def _update_bonus(self):
        if self.bonus_state != "up":
            return
        self.bonus_ticks -= 1
        if self.bonus_ticks <= 0:
            self.bonus_state = "none"

    # --- Collisions ------------------------------------------------------- #

    def _resolve_contact(self):
        pc = self.pac_x // CELL
        pr = self.pac_y // CELL
        for g in self.vulns:
            if g.state in (V_HOUSE, V_EYES, V_ENTERING):
                continue
            if g.x // CELL != pc or g.y // CELL != pr:
                continue
            if g.fright:
                chain = min(self.fright_chain, len(VULN_POINTS) - 1)
                self.score += VULN_POINTS[chain]
                self.fright_chain += 1
                self.vulns_patched += 1
                g.fright = False
                g.state = V_EYES
                self.freeze_ticks = EAT_FREEZE_TICKS
                return True
            self._die()
            return True
        return False

    def _die(self):
        self.state = DYING
        self.death_ticks = DEATH_TICKS

    def _after_death(self):
        self.lives -= 1
        if self.lives <= 0:
            self._finish()
            return
        self._reset_actors()
        self.bonus_state = "none"
        self.state = READY

    def _next_level(self):
        self.score += LEVEL_BONUS
        self.level += 1
        self._load_level()
        self.elroy_stage = 0
        self.state = READY

    def _finish(self):
        self.state = DEAD
        self.end_tick = self.tick

    # --- The tick --------------------------------------------------------- #

    def step(self):
        if self.state == DEAD:
            return

        while self.pending and self.pending[0] // 4 <= self.tick:
            code = self.pending.pop(0)
            direction = code % 4
            self.turns.append(self.tick * 4 + direction)
            self.pac_want = direction
            if self.state == IDLE:
                self.state = READY
                self.play_start_tick = self.tick
                self.ready_ticks = READY_TICKS

        if self.state == IDLE:
            self.tick += 1
            return

        if self.state == READY:
            self.ready_ticks -= 1
            if self.ready_ticks <= 0:
                self.state = PLAYING
            self.tick += 1
            return

        if self.state == DYING:
            self.death_ticks -= 1
            if self.death_ticks <= 0:
                self._after_death()
            self.tick += 1
            return

        if self.state == CLEAR:
            self.clear_ticks -= 1
            if self.clear_ticks <= 0:
                self._next_level()
            self.tick += 1
            return

        if self.freeze_ticks > 0:
            self.freeze_ticks -= 1
            self.tick += 1
            return

        self._update_phase()
        self._update_bonus()
        self._release_vulns()

        self._move_pac()
        if self.patches_left <= 0:
            self.state = CLEAR
            self.clear_ticks = LEVEL_CLEAR_TICKS
            self.tick += 1
            return
        if self._resolve_contact():
            self.tick += 1
            return

        for g in self.vulns:
            if g.state == V_HOUSE:
                self._move_house(g)
            elif g.state == V_LEAVING:
                self._move_leaving(g)
            elif g.state == V_ENTERING:
                self._move_entering(g)
            else:
                self._move_vuln_maze(g)
        self._resolve_contact()

        self.tick += 1
        if self.tick >= ABSOLUTE_MAX_TICKS and self.state != DEAD:
            self._finish()

    def duration_ms(self):
        if self.play_start_tick < 0:
            return 0
        end = self.end_tick if self.end_tick >= 0 else self.tick
        # Math.floor(x + 0.5) is what Math.round does, and unlike Python's
        # round() it does not turn a halfway value into the nearest even one.
        return int(math.floor((end - self.play_start_tick) * STEP_MS + 0.5))

    def snapshot(self):
        return {
            "tick": self.tick,
            "state": self.state,
            "score": self.score,
            "level": self.level,
            "lives": self.lives,
            "patchesLeft": self.patches_left,
            "totalPatches": self.total_patches,
            "pacX": self.pac_x,
            "pacY": self.pac_y,
            "pacDir": self.pac_dir,
            "phaseIndex": self.phase_index,
            "phaseKind": self.phase_kind,
            "frightTicks": self.fright_ticks,
            "elroyStage": self.elroy_stage,
            "endTick": self.end_tick,
            "vulns": [
                {"x": g.x, "y": g.y, "dir": g.dir, "state": g.state,
                 "fright": 1 if g.fright else 0}
                for g in self.vulns
            ],
            "turns": list(self.turns),
        }


def replay(seed, turns, max_ticks=None):
    """Run a recorded trace and return the sim it produced.

    The tick budget comes from the trace itself: a run with no input left keeps
    going only until the remaining lives are lost, which is bounded. The
    absolute cap is what keeps a hostile trace from costing real CPU.
    """
    if max_ticks is None:
        last = turns[-1] // 4 if turns else 0
        max_ticks = min(last + TAIL_TICKS, ABSOLUTE_MAX_TICKS)

    sim = Sim(seed)
    nxt = 0
    total = len(turns)
    while sim.state != DEAD and sim.tick < max_ticks:
        while nxt < total and turns[nxt] // 4 <= sim.tick:
            sim.queue_turn(turns[nxt] // 4, turns[nxt] % 4)
            nxt += 1
        sim.step()
    return sim


# --------------------------------------------------------------------------- #
# Sessions
# --------------------------------------------------------------------------- #
#
# A run is issued before it is played. That single change is what stops a run
# being shopped for: the player is handed one world, not their pick of every
# world, and the server notes the time it handed it over. The seed is spent on
# submission, so the same trace cannot be posted twice.

def _now_ms():
    return int(time.time() * 1000)


def issue_session(ip):
    """Mint a seed and start its clock."""
    sid = secrets.token_hex(16)
    # Matches randomSeed() in patchman/rng.mjs, from a source worth trusting.
    seed = secrets.randbelow(0x7FFFFFFF) + 1
    _write(
        "INSERT INTO patchman_sessions (id, seed, issued_at, issued_ip) "
        "VALUES (?, ?, ?, ?)",
        (sid, seed, _now_ms(), ip),
    )
    return {"session": sid, "seed": seed}


def claim_session(sid, ip):
    """Look up a session for submission. Returns ``(row, error)``."""
    if not sid:
        return None, ("That run did not come with a session. Reload the page.", 400)
    rows = _rows("SELECT * FROM patchman_sessions WHERE id = ?", (sid,))
    if not rows:
        return None, ("That run's session is not one this server issued.", 400)
    row = rows[0]
    if row["consumed_at"] is not None:
        return None, ("That run has already been posted.", 409)
    if _now_ms() - int(row["issued_at"]) > SESSION_TTL_MS:
        return None, ("That run took too long to post. Start a fresh one.", 400)
    return row, None


def consume_session(sid, now_ms):
    _write("UPDATE patchman_sessions SET consumed_at = ? WHERE id = ?", (now_ms, sid))


def record_beat(sid, tick):
    """Note that a session was still being played, and how far it had got.

    The tick matters as much as the timestamp. Comparing how far the simulation
    advanced against how much real time passed is what catches a run that was
    computed rather than played.
    """
    rows = _rows(
        "SELECT first_beat, beats, consumed_at FROM patchman_sessions WHERE id = ?",
        (sid,))
    if not rows or rows[0]["consumed_at"] is not None:
        return False
    now = _now_ms()
    if rows[0]["first_beat"] is None:
        _write(
            "UPDATE patchman_sessions SET first_beat = ?, first_tick = ?, "
            "last_beat = ?, last_tick = ?, beats = 1 WHERE id = ?",
            (now, tick, now, tick, sid),
        )
    else:
        _write(
            "UPDATE patchman_sessions SET last_beat = ?, last_tick = ?, "
            "beats = beats + 1 WHERE id = ?",
            (now, tick, sid),
        )
    return True


def sweep_sessions():
    """Drop sessions too old to be redeemed. Cheap, and keeps the table small."""
    _write("DELETE FROM patchman_sessions WHERE issued_at < ?",
           (_now_ms() - SESSION_TTL_MS * 2,))


# --------------------------------------------------------------------------- #
# Deciding whether a run happened
# --------------------------------------------------------------------------- #
#
# Replaying a trace proves the trace is consistent. It cannot prove a person
# produced it, and on a deterministic game that difference is the whole problem:
# the client's own rules can be imported and searched for a perfect trace, and
# the result replays exactly like a real run because it is a real run, just not
# one anybody played.
#
# So the checks below are not about the score. They are about whether the run
# cost what a run costs:
#
#   the world       the seed comes from here and is spent once, so a run cannot
#                   be shopped for offline and cannot be handed in twice
#   the clock       a run that simulates four minutes has to have taken four
#                   minutes of the server's own time, which is what turns an
#                   instant forgery back into a four minute wait
#   the hand        turns land on a spread of intervals because hands are not
#                   clocks, and machine timing shows up as a spike on one exact
#                   value
#
# What this deliberately does not claim: a patient attacker who paces a forged
# run in real time and scatters its timing can still get through. For an office
# side game that is far enough.

SESSION_TTL_MS = 45 * 60 * 1000   # a seed goes stale rather than waiting forever
CLOCK_SLACK_MS = 3000             # request latency and a second of clock drift
BEAT_INTERVAL_MS = 5000           # matches the client's heartbeat timer
MIN_BEAT_RATIO = 0.4              # a dropped beat or two is normal
BEAT_FLOOR = 2                    # below this a run is too short to judge

# A solver playing this game turns on tile centres, and a tile takes a whole
# number of ticks to cross, so its gaps between turns pile up on a handful of
# exact values. A hand does not do that even when the player is turning on a
# rhythm, because the rhythm is 8ms-resolution and the hand is not.
#
# These thresholds are deliberately looser than Flappy Duck's. That game had 800
# measured human runs to calibrate against; this one has none yet, and the cost
# of a false positive — telling someone their real run does not count — is much
# worse than the cost of letting a careful forgery through on an office game.
# Tighten them once there is a corpus to tighten them against.
MODAL_SHARE_LIMIT = 0.35
MODAL_MIN_INTERVALS = 30

# Four direction changes a second, held for half a minute, is not a hand. Short
# bursts around a corner are, so this only applies once a run is long enough
# that a burst cannot be the explanation.
MAX_TURNS_PER_SEC = 4.0
RATE_MIN_DURATION_MS = 20000

# Two inputs a 40th of a second apart are one press as far as a hand is
# concerned. A few are a fumbled corner; dozens are a machine.
MIN_HUMAN_GAP_TICKS = 3
MAX_SHORT_GAPS = 15

FLAG_TEXT = {
    "faster_than_real_time": "That run finished sooner than it could have been played.",
    "not_enough_beats": "That run was not in contact while it was being played.",
    "beats_outran_clock": "That run reported progress faster than time passed.",
    "machine_timing": "That run's inputs are too evenly spaced to be hand timed.",
    "turn_rate": "That run holds a turning rate a hand cannot keep up.",
    "double_inputs": "That run has inputs too close together to be separate presses.",
    "replay_mismatch": "That run does not replay to the score it was posted with.",
    "unreadable_trace": "That run's input trace could not be read.",
}

# The flags that came from comparing the run against real time. They are raised
# once, at submission, from the session's heartbeats — and the heartbeats are
# not kept forever. So nothing can decide these again after the fact, and
# anything that re-judges a stored run has to carry them forward rather than
# recompute them. audit_run() deliberately does not return them.
CLOCK_FLAGS = ("faster_than_real_time", "not_enough_beats", "beats_outran_clock")


def interval_stats(turns):
    """Shape of the gaps between turns, which is where a machine gives itself away."""
    ticks = [t // 4 for t in turns]
    gaps = [ticks[i + 1] - ticks[i] for i in range(len(ticks) - 1)]
    if not gaps:
        return {"intervals": 0, "modal_share": 0.0, "short": 0}
    counts = {}
    for g in gaps:
        counts[g] = counts.get(g, 0) + 1
    return {
        "intervals": len(gaps),
        "modal_share": max(counts.values()) / len(gaps),
        "short": sum(1 for g in gaps if g < MIN_HUMAN_GAP_TICKS),
    }


def judge_run(sim, session, elapsed_ms, now_ms):
    """Returns the reasons a run should not count. Empty means it counts."""
    flags = []
    duration = sim.duration_ms()

    # The clock. A session is issued before the run starts, so real time can
    # only ever exceed simulated time. Less means the run was not played.
    if elapsed_ms + CLOCK_SLACK_MS < duration:
        flags.append("faster_than_real_time")

    beats = int(session["beats"] or 0)
    first_beat, last_beat = session["first_beat"], session["last_beat"]
    first_tick, last_tick = session["first_tick"], session["last_tick"]

    expected = int(duration / BEAT_INTERVAL_MS * MIN_BEAT_RATIO)
    if expected >= BEAT_FLOOR and beats < expected:
        flags.append("not_enough_beats")

    if first_beat is not None and last_beat is not None and beats >= 2:
        real = last_beat - first_beat
        simulated = (last_tick - first_tick) * STEP_MS
        if simulated > real + CLOCK_SLACK_MS:
            flags.append("beats_outran_clock")

    # The hand.
    stats = interval_stats(sim.turns)
    if stats["intervals"] >= MODAL_MIN_INTERVALS \
            and stats["modal_share"] >= MODAL_SHARE_LIMIT:
        flags.append("machine_timing")

    if duration >= RATE_MIN_DURATION_MS:
        rate = len(sim.turns) / (duration / 1000.0)
        if rate > MAX_TURNS_PER_SEC:
            flags.append("turn_rate")

    if stats["short"] > MAX_SHORT_GAPS:
        flags.append("double_inputs")

    return flags


def read_trace(raw):
    """Validate the submitted trace enough to replay it safely.

    Deliberately not a plausibility check. The only questions here are whether
    it is a list of ascending tick numbers carrying a real direction, and
    whether replaying it will cost the server a bounded amount of work.

    Two inputs can legitimately land on the same tick, so ticks must not
    decrease rather than must increase; within a tick the codes may go either
    way, because they are ordered by which key was pressed first.
    """
    if len(raw) > MAX_INPUT_TRACE:
        return None, "That run recorded more inputs than a run can contain."
    turns = []
    last = -1
    for code in raw:
        code = int(code)
        if code < 0:
            return None, "That run's input trace is not a trace."
        tick = code // 4
        if tick > ABSOLUTE_MAX_TICKS:
            return None, "That run's input trace is outside the length of a run."
        if tick < last:
            return None, "That run's input trace is out of order."
        turns.append(code)
        last = tick
    return turns, None


# --------------------------------------------------------------------------- #
# Re-judging runs that were stored without a verdict
# --------------------------------------------------------------------------- #
#
# The board only counts verified runs. Nothing should ever reach the table
# without a verdict, but a row with a null ``flags`` column is a row nothing has
# judged, and leaving those invisible would mean a bug that skipped judging
# quietly emptied the board instead of showing up.
#
# Runs with no trace left to check are kept. They are unjudgeable rather than
# suspect, and throwing away honest history to be seen doing something about a
# forgery would be the worse trade.

AUDIT_LIMIT = 20000


def decode_trace(raw_trace):
    """Read a stored trace.

    Returns ``(codes, stored)``. ``codes`` is None when there is nothing
    readable. ``stored`` is False only when the column is empty, which is what
    pruning leaves behind and is the one case that means "cannot be judged"
    rather than "did not happen".
    """
    if raw_trace is None or raw_trace == "":
        return None, False

    value = raw_trace
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            return None, True

    if not isinstance(value, list):
        return None, True
    return value, True


def audit_run(seed, claimed_score, raw_trace):
    """Judge one stored run. Returns ``(verified, flags)``."""
    raw, stored = decode_trace(raw_trace)

    if not stored:
        return 1, ["no_trace"]
    if raw is None:
        return 0, ["unreadable_trace"]
    if not raw:
        # A stored but empty trace is not a pruned run, because pruning empties
        # the column rather than writing "[]". Nothing can be scored without
        # moving, so a score here was typed, not played.
        if claimed_score > 0:
            return 0, ["scored_without_playing"]
        return 1, []

    turns, error = read_trace(raw[:MAX_INPUT_TRACE])
    if error:
        return 0, ["unreadable_trace"]

    sim = replay(seed, turns)

    trimmed = len(raw) >= MAX_INPUT_TRACE
    if not trimmed and sim.score != claimed_score:
        return 0, ["replay_mismatch"]

    # The clock checks need a session, so only the hand checks apply here.
    flags = []
    stats = interval_stats(sim.turns)
    duration = sim.duration_ms()
    if stats["intervals"] >= MODAL_MIN_INTERVALS \
            and stats["modal_share"] >= MODAL_SHARE_LIMIT:
        flags.append("machine_timing")
    if duration >= RATE_MIN_DURATION_MS \
            and len(sim.turns) / (duration / 1000.0) > MAX_TURNS_PER_SEC:
        flags.append("turn_rate")
    if stats["short"] > MAX_SHORT_GAPS:
        flags.append("double_inputs")
    if trimmed and not flags:
        flags.append("trace_trimmed")
        return 1, flags

    return (0 if flags else 1), flags


def audit_pending_runs():
    """Judge every run that has not been judged yet. Safe to run twice."""
    pending = _rows(
        "SELECT id, score, seed, turns FROM patchman_runs "
        "WHERE flags IS NULL ORDER BY id LIMIT ?",
        (AUDIT_LIMIT,),
    )
    if not pending:
        return {"checked": 0, "voided": 0}

    voided = 0
    for row in pending:
        verified, flags = audit_run(row["seed"], row["score"], row["turns"])
        if not verified:
            voided += 1
        _write(
            "UPDATE patchman_runs SET verified = ?, flags = ? WHERE id = ?",
            (verified, json.dumps(flags), row["id"]),
        )

    log.info("patchman: audited %d runs, %d no longer count", len(pending), voided)
    return {"checked": len(pending), "voided": voided}


def clear_board(player=None):
    """Delete runs. Everything, or one player.

    This lives here rather than only in the admin script because the admin
    script is not in the container: the compose file mounts ./app and ./data and
    nothing else. Clearing the board on the server therefore has to go through
    this module, and the alternative is someone hand typing DELETE against the
    database the real leaderboard's buffer also lives in. Keeping the statement
    here is what makes "only patchman_ tables are touched" a property of the
    code instead of a promise about a copied command.

        docker exec <container> python -c \\
            "import sys; sys.path.insert(0, '/app'); \\
             import patchman; print(patchman.clear_board())"
    """
    if player:
        key = name_key(player)
        if not key:
            return {"deleted": 0, "player": player}
        before = _rows("SELECT COUNT(*) AS n FROM patchman_runs WHERE player_key = ?",
                       (key,))[0]["n"]
        _write("DELETE FROM patchman_runs WHERE player_key = ?", (key,))
        log.info("patchman: cleared %d runs for %s", before, player)
        return {"deleted": before, "player": player}

    before = _rows("SELECT COUNT(*) AS n FROM patchman_runs")[0]["n"]
    _write("DELETE FROM patchman_runs")
    _write("DELETE FROM patchman_sessions")
    log.info("patchman: cleared the board, %d runs deleted", before)
    return {"deleted": before, "player": None}


# --------------------------------------------------------------------------- #
# Rate limiting
# --------------------------------------------------------------------------- #
#
# Names are free text, so a per-name limit is a speed bump a cheat steps over by
# typing a different name. Everything here is therefore counted per address as
# well, which is the part that actually costs an attacker something.

MIN_SUBMIT_GAP_S = 3
GAP_FRACTION_OF_RUN = 0.75
MAX_RUNS_PER_DAY = 300
MAX_RUNS_PER_DAY_IP = 600
MAX_OPEN_SESSIONS = 30        # unspent seeds one address may hold at once
SESSION_MINT_PER_HOUR = 400
TRACE_RETENTION_DAYS = 30

_last_prune = [""]


def _client_ip(request):
    """Best effort. Behind the reverse proxy this app already runs behind, the
    forwarded header is the only thing that varies between players."""
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",")[0].strip()[:64]
    return (request.client.host if request.client else "")[:64]


def _seconds_between(older, newer):
    a, b = _parse_stamp(older), _parse_stamp(newer)
    if a is None or b is None:
        return None
    return (b - a).total_seconds()


def check_rate(player_key, ip, duration_ms):
    """Returns ``(message, status)`` when a run should be refused."""
    now = _utc_stamp()
    recent = _rows(
        "SELECT created_at FROM patchman_runs WHERE player_key = ? "
        "ORDER BY id DESC LIMIT 1", (player_key,))
    if recent:
        gap = _seconds_between(recent[0]["created_at"], now)
        if gap is not None and gap < MIN_SUBMIT_GAP_S:
            return ("Posting runs a little fast. Try again in a moment.", 429)
        needed = duration_ms / 1000.0 * GAP_FRACTION_OF_RUN
        if gap is not None and gap < needed:
            return ("That run is longer than the time since your last one.", 429)

    start, end = _day_bounds(_local_now().date())
    today = _rows(
        "SELECT COUNT(*) AS n FROM patchman_runs "
        "WHERE player_key = ? AND created_at >= ? AND created_at < ?",
        (player_key, start, end))[0]["n"]
    if today >= MAX_RUNS_PER_DAY:
        return ("That is %d runs today. The board will still be here tomorrow."
                % today, 429)

    if ip:
        from_ip = _rows(
            "SELECT COUNT(*) AS n FROM patchman_runs "
            "WHERE session_id IN (SELECT id FROM patchman_sessions WHERE issued_ip = ?) "
            "AND created_at >= ? AND created_at < ?",
            (ip, start, end))[0]["n"]
        if from_ip >= MAX_RUNS_PER_DAY_IP:
            return ("That is a lot of runs from one place today.", 429)

    return None


def prune_traces():
    """Drop old input traces, keeping every score row.

    A trace is only useful for verifying a recent run. The score is the record
    and it is never touched, so the board is unaffected by this running. It is
    lazy rather than scheduled on purpose: the brief is that this module adds no
    job to the app's scheduler.
    """
    today = _local_now().strftime("%Y-%m-%d")
    if _last_prune[0] == today:
        return 0
    _last_prune[0] = today
    cutoff = _utc_stamp(datetime.now(timezone.utc)
                        - timedelta(days=TRACE_RETENTION_DAYS))
    with _db_lock:
        cur = _conn.execute(
            "UPDATE patchman_runs SET turns = NULL "
            "WHERE turns IS NOT NULL AND created_at < ?", (cutoff,))
        _conn.commit()
        return cur.rowcount


# --------------------------------------------------------------------------- #
# Submission
# --------------------------------------------------------------------------- #

class ScoreIn(BaseModel):
    session: str = Field(default="", max_length=64)
    player: str = Field(default="", max_length=120)
    score: int = 0
    duration_ms: int = 0
    turns: list[int] = Field(default_factory=list)


def _reject(message, status=400):
    return JSONResponse(status_code=status, content={"ok": False, "error": message})


@router.post("/api/score")
def submit_score(payload: ScoreIn, request: Request):
    canonical, error = resolve_player(payload.player)
    if error:
        return _reject(error)

    ip = _client_ip(request)
    key = name_key(canonical)

    # The trace is read before anything else, because everything else is
    # derived from it. Nothing the client says about the run is trusted.
    turns, trace_error = read_trace(payload.turns)
    if trace_error:
        return _reject(trace_error)

    session, session_error = claim_session(payload.session, ip)
    if session_error:
        return _reject(session_error[0], status=session_error[1])

    sim = replay(session["seed"], turns)
    score = sim.score
    duration = sim.duration_ms()

    if score <= 0:
        return _reject("A run of zero is not posted to the board.")
    if payload.score != score:
        # Either the client is lying or the two engines have drifted apart.
        # Both are worth refusing rather than quietly recording the truth.
        return _reject("That run replays to %d points, not %d." % (score, payload.score))

    guard = check_rate(key, ip, duration)
    if guard:
        return _reject(guard[0], status=guard[1])

    now_ms = _now_ms()
    elapsed = now_ms - int(session["issued_at"])
    flags = judge_run(sim, session, elapsed, now_ms)
    verified = 0 if flags else 1

    consume_session(session["id"], now_ms)

    previous = _rows(
        "SELECT COALESCE(MAX(score), 0) AS best FROM patchman_runs "
        "WHERE player_key = ? AND verified = 1",
        (key,),
    )[0]["best"]

    _write(
        "INSERT INTO patchman_runs "
        "(player, player_key, score, seed, duration_ms, turns, created_at, "
        " verified, session_id, elapsed_ms, turn_count, level, patches, flags) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (canonical, key, score, int(session["seed"]), duration,
         json.dumps(sim.turns[:MAX_INPUT_TRACE]), _utc_stamp(), verified,
         session["id"], elapsed, len(sim.turns), sim.level, sim.total_patches,
         json.dumps(flags)),
    )
    prune_traces()

    if not verified:
        return JSONResponse(status_code=202, content={
            "ok": True,
            "counted": False,
            "player": canonical,
            "score": score,
            "level": sim.level,
            "patches": sim.total_patches,
            "reason": FLAG_TEXT.get(flags[0], "That run could not be verified."),
        })

    rank = next((r["rank"] for r in board_rows("alltime") if r["player_key"] == key), None)
    return {
        "ok": True,
        "counted": True,
        "player": canonical,
        "score": score,
        "level": sim.level,
        "patches": sim.total_patches,
        "rank": rank,
        "personal_best": score > previous,
        "previous_best": previous,
    }


# --------------------------------------------------------------------------- #
# API
# --------------------------------------------------------------------------- #

class BeatIn(BaseModel):
    session: str = Field(default="", max_length=64)
    tick: int = 0


@router.post("/api/session")
def api_session(request: Request):
    """Hand out a world to play. The clock on it starts now."""
    ip = _client_ip(request)
    if ip:
        cutoff = _now_ms() - 60 * 60 * 1000
        minted = _rows(
            "SELECT COUNT(*) AS n FROM patchman_sessions "
            "WHERE issued_ip = ? AND issued_at >= ?", (ip, cutoff))[0]["n"]
        if minted >= SESSION_MINT_PER_HOUR:
            return _reject("Starting runs a little fast. Try again shortly.", status=429)
        open_now = _rows(
            "SELECT COUNT(*) AS n FROM patchman_sessions "
            "WHERE issued_ip = ? AND consumed_at IS NULL AND issued_at >= ?",
            (ip, _now_ms() - SESSION_TTL_MS))[0]["n"]
        if open_now >= MAX_OPEN_SESSIONS:
            return _reject("Too many runs open at once. Finish one first.", status=429)

    sweep_sessions()
    return issue_session(ip)


@router.post("/api/beat")
def api_beat(payload: BeatIn):
    """Say that a run is still being played, and how far along it is.

    Small and frequent on purpose. A run that never checks in, or one whose
    ticks advance faster than the server's own clock, was not played.
    """
    tick = max(0, min(int(payload.tick), ABSOLUTE_MAX_TICKS))
    return {"ok": record_beat(payload.session, tick)}


@router.get("/api/board")
def api_board(view: str = "alltime", player: str = "", limit: int = BOARD_LIMIT):
    if view not in VIEWS:
        return _reject("Unknown view. Use one of: " + ", ".join(VIEWS))
    limit = max(1, min(100, int(limit)))
    key = name_key(player) if player else None
    data = board_view(view, key, limit)
    data["hall_of_fame"] = hall_of_fame()
    return data


@router.get("/api/player/{name}")
def api_player(name: str):
    summary = player_summary(name)
    if not summary.get("ok"):
        return _reject(summary.get("error", "Unknown player."), status=404)
    return summary


# --------------------------------------------------------------------------- #
# Pages and static files
# --------------------------------------------------------------------------- #

_MEDIA_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",  # browsers go by this, not the suffix
    ".json": "application/json",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".svg": "image/svg+xml",
    ".webmanifest": "application/manifest+json",
}


def _serve(filename):
    path = os.path.join(STATIC_DIR, filename)
    if not os.path.isfile(path):
        return JSONResponse(status_code=404, content={"error": "not found"})
    media = _MEDIA_TYPES.get(os.path.splitext(path)[1].lower(), "application/octet-stream")
    # no-cache means revalidate, not skip. The browser still gets a 304 from the
    # ETag, so a deploy takes effect immediately without costing a full download.
    return FileResponse(path, media_type=media, headers={"Cache-Control": "no-cache"})


@router.get("", include_in_schema=False)
def game_page():
    return _serve("index.html")


@router.get("/board", include_in_schema=False)
def board_page():
    return _serve("board.html")


@router.get("/static/{path:path}", include_in_schema=False)
def static_file(path: str):
    target = os.path.normpath(os.path.join(STATIC_DIR, path))
    root = os.path.normpath(STATIC_DIR)
    if target != root and not target.startswith(root + os.sep):
        return JSONResponse(status_code=404, content={"error": "not found"})
    return _serve(os.path.relpath(target, root))


# --------------------------------------------------------------------------- #
# Startup
# --------------------------------------------------------------------------- #
#
# The deploy is "upload the files, restart the container", so import time is the
# only place a migration can run. Both of these are idempotent and cheap on a
# database that has already been through them.

try:
    audit_pending_runs()
    sweep_sessions()
except Exception:  # pragma: no cover - a side game must never block a restart
    log.exception("patchman: startup audit failed, board left as it was")
