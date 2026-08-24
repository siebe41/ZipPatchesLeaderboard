"""Patchaga: a side game that shares the app and nothing else.

Isolation is the whole point of this module, so it is worth being explicit
about what that means:

* It never opens ``leaderboard.json`` or ``history.json`` at all, for reading or
  for writing. Players type their own name, so there is no roster to consult.
* It never touches a daily rank, penalty, streak, excused day, weekly total or
  win counter. Nothing in here knows those concepts exist.
* It adds no scheduled job. Seasons are derived from ``created_at`` when a board
  is queried, so there is no rollover to run and nothing to get out of step.
* Its tables are prefixed ``patchaga_`` and live in the SQLite file the app
  already keeps on the data volume, so a deploy stays "upload files, restart".

The rules of the game itself live in ``patchaga/sim.mjs`` and every tuning value
lives in ``patchaga/config.mjs``. That simulation is mirrored here, exactly, so
the server can replay a submitted run rather than take its word for the score.
``tools/check_patchaga_parity.py`` fails the moment the two engines disagree.

On trusting submissions. A deterministic game is replayable, and a replayable
run is forgeable: anyone can import the client's own rules, search for a good
input trace offline, and hand over a trace that replays perfectly. Replay proves
a trace is self-consistent, never that a person produced it. So the seed is
issued by the server and spent once, the run is paced against the server's own
clock through heartbeats, and the trace is measured for input a human hand
cannot produce. Replay is the floor here, not the ceiling.

On matching the JavaScript exactly. Every position is an integer number of
sub-units rather than a float, which removes the usual source of drift between
two ports. Two hazards remain and both are handled deliberately:

* The generator. JavaScript's bitwise operators coerce to 32 bits, so its state
  is held here as an unsigned Python int and masked after every operation.
* Angles. ``Math.sin`` and ``math.sin`` are not required to agree to the last
  bit, and a swooping dive integrates over hundreds of ticks, so a one-ULP
  disagreement would eventually put a bug on screen in one engine and off it in
  the other. Neither engine calls a transcendental function: sine comes from a
  table built with integer arithmetic, by the same Bhaskara identity, in both.

Division floors in both engines -- ``Math.floor(a / b)`` there, ``a // b`` here
-- which is why negative velocities port without a special case.

None of the artwork, the enemies or the names come from any existing arcade
game. It is a fixed shooter, which is a genre, built out of Patch My PC's own
material: a rubber duck firing patches at the bugs it is there to fix.
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
from datetime import datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo  # Python 3.9+ (needs tzdata on slim images)
except ImportError:  # pragma: no cover
    ZoneInfo = None


router = APIRouter(prefix="/patchaga", tags=["patchaga"])

log = logging.getLogger("patchaga")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "patchaga")

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
    CREATE TABLE IF NOT EXISTS patchaga_runs (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        player      TEXT    NOT NULL,
        player_key  TEXT    NOT NULL,
        score       INTEGER NOT NULL,
        seed        INTEGER NOT NULL,
        duration_ms INTEGER NOT NULL,
        inputs      TEXT,
        created_at  TEXT    NOT NULL,
        verified    INTEGER NOT NULL DEFAULT 0,
        session_id  TEXT,
        elapsed_ms  INTEGER,
        input_count INTEGER,
        wave        INTEGER,
        bugs        INTEGER,
        shots       INTEGER,
        rescues     INTEGER,
        flags       TEXT
    )
    """
)

# A run is issued before it is played, so the server knows which world the
# player was given and when the clock started. One row per attempt, spent once.
_conn.execute(
    """
    CREATE TABLE IF NOT EXISTS patchaga_sessions (
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
    ("input_count", "INTEGER"),
    ("wave", "INTEGER"),
    ("bugs", "INTEGER"),
    ("shots", "INTEGER"),
    ("rescues", "INTEGER"),
    ("flags", "TEXT"),
)
_have = {r["name"] for r in _conn.execute("PRAGMA table_info(patchaga_runs)")}
for _name, _type in _EXTRA_RUN_COLUMNS:
    if _name not in _have:
        _conn.execute("ALTER TABLE patchaga_runs ADD COLUMN %s %s" % (_name, _type))

_conn.execute("CREATE INDEX IF NOT EXISTS idx_patchaga_created ON patchaga_runs(created_at)")
_conn.execute("CREATE INDEX IF NOT EXISTS idx_patchaga_player ON patchaga_runs(player_key)")
_conn.execute("CREATE INDEX IF NOT EXISTS idx_patchaga_score ON patchaga_runs(score DESC, id ASC)")
_conn.execute("CREATE INDEX IF NOT EXISTS idx_patchaga_sess ON patchaga_sessions(issued_at)")
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
               wave, bugs,
               ROW_NUMBER() OVER (
                   PARTITION BY player_key
                   ORDER BY score DESC, created_at ASC, id ASC
               ) AS rn
        FROM patchaga_runs
        WHERE score > 0 AND verified = 1 %s
    )
    SELECT id, player, player_key, score, seed, duration_ms, created_at,
           wave, bugs
    FROM ranked
    WHERE rn = 1
    ORDER BY score DESC, created_at ASC, id ASC
"""

# Cumulative score. The tiebreak is the earliest *last* run, because the player
# who got to the total first is the one who stopped needing runs first.
_VOLUME = """
    SELECT player_key,
           SUM(score)      AS total,
           COUNT(*)        AS runs,
           MAX(score)      AS best,
           COALESCE(SUM(bugs), 0) AS bugs,
           MAX(created_at) AS last_at
    FROM patchaga_runs
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
        "SELECT player_key, player FROM patchaga_runs "
        "WHERE id IN (SELECT MAX(id) FROM patchaga_runs "
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
                "bugs": r["bugs"],
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
            "wave": r["wave"],
            "bugs": r["bugs"],
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
    first = _rows("SELECT MIN(created_at) AS m FROM patchaga_runs "
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
        "COALESCE(MAX(score), 0) AS best, COALESCE(SUM(bugs), 0) AS bugs, "
        "COALESCE(SUM(rescues), 0) AS rescues, "
        "COALESCE(MAX(wave), 0) AS furthest FROM patchaga_runs "
        "WHERE player_key = ? AND verified = 1",
        (key,),
    )[0]

    def best_in(bounds):
        clause, params = _range_clause(bounds)
        sql = ("SELECT COALESCE(MAX(score), 0) AS best FROM patchaga_runs "
               "WHERE player_key = ? AND verified = 1" + clause)
        return _rows(sql, (key,) + params)[0]["best"]

    ranks = {}
    for view in VIEWS:
        ranks[view] = next(
            (r["rank"] for r in board_rows(view) if r["player_key"] == key), None)

    recent = _rows(
        "SELECT score, duration_ms, wave, bugs, rescues, created_at "
        "FROM patchaga_runs "
        "WHERE player_key = ? AND verified = 1 ORDER BY id DESC LIMIT 10",
        (key,),
    )

    return {
        "ok": True,
        "player": stored or canonical,
        "runs": totals["runs"],
        "total": totals["total"],
        "best": totals["best"],
        "bugs": totals["bugs"],
        "rescues": totals["rescues"],
        "furthest_wave": totals["furthest"],
        "best_season": best_in(_bounds_for("season")),
        "best_today": best_in(_bounds_for("today")),
        "ranks": ranks,
        "recent": recent,
    }


# --------------------------------------------------------------------------- #
# The simulation, ported from patchaga/sim.mjs
# --------------------------------------------------------------------------- #
#
# This is the same game the browser runs, in Python, tick for tick. It exists so
# that a submitted score is something the server works out rather than something
# the client asserts. Everything below is a direct translation; if it needs to
# change, change patchaga/sim.mjs first and keep the two in step.

# --- Angles ---------------------------------------------------------------- #

SIN_STEPS = 1024
SIN_HALF = SIN_STEPS // 2
SIN_QUARTER = SIN_STEPS // 4
SIN_SCALE = 4096


def _build_sine_table():
    """Bhaskara I's sine approximation, in integers.

    Mirrors buildSineTable() in patchaga/config.mjs. Expressed in steps rather
    than radians, pi cancels out of the ratio, so the table is built without a
    single irrational number and without calling math.sin -- which is the one
    function the two languages are not obliged to agree about.
    """
    table = []
    for i in range(SIN_STEPS):
        j = i if i < SIN_HALF else i - SIN_HALF
        p = j * (SIN_HALF - j)
        value = (16 * p * SIN_SCALE) // (5 * SIN_HALF * SIN_HALF - 4 * p)
        table.append(value if i < SIN_HALF else -value)
    return table


SIN_TABLE = _build_sine_table()


def isin(steps):
    return SIN_TABLE[steps % SIN_STEPS]


def icos(steps):
    return isin(steps + SIN_QUARTER)


# --- Tunables, mirroring patchaga/config.mjs ------------------------------- #

WIDTH = 432
HEIGHT = 560
UNIT = 64
HUD_TOP = 34
FLOOR_Y = 540

STEP_MS = 1000.0 / 120.0

DUCK_Y = 502
DUCK_SPEED = 56
DUCK_HALF_W = 7
DUCK_HALF_H = 6
DUCK_MARGIN = 14
MERGED_OFFSET = 11

PATCH_SPEED = 416
PATCH_HALF_W = 4
PATCH_HALF_H = 6
MAX_PATCHES = 3
MAX_PATCHES_MERGED = 6
PATCH_COOLDOWN = 13

BUG_SHOT_SPEED = 68
BUG_SHOT_HALF_W = 3
BUG_SHOT_HALF_H = 6
MAX_BUG_SHOTS = 10
BUG_SHOT_SPREAD = 96

FORM_COLS = 10
FORM_ROWS = 5
COL_STEP = 36
ROW_STEP = 30
FORM_TOP = 92
SWAY_AMP = 11
SWAY_PERIOD = 540
BREATHE_PERIOD = 900
BREATHE_AMP = 3

BUG_HALF_W = 9
BUG_HALF_H = 8

ROOTKIT_COLS = (3, 4, 5, 6)
ENTRY_TICKS = 150
ENTRY_STAGGER = 16
ENTRY_SETTLE = 90
READY_TICKS = 200
CLEAR_TICKS = 210
DEATH_TICKS = 200
RESPAWN_TICKS = 90

DIVE_TICKS = 330
DIVE_FALL = 132
DIVE_EASE_TICKS = 40
DIVE_SWING_SPEED = 98
DIVE_SWING_PERIOD = 240
DIVE_HOME_PULL = 26
DIVE_HOME_AFTER = 120
REENTRY_TICKS = 96
DIVE_GAP_MIN = 150
DIVE_GAP_SPREAD = 120
MAX_DIVERS_CAP = 6
FIRE_CHANCE = 22
FIRE_EVERY = 40
ROOTKIT_EVERY = 5

BEAM_TICKS = 200
BEAM_WINDUP = 46
BEAM_HALF_W = 22
BEAM_HOVER_Y = 300
FORK_CHANCE = 45
RESCUE_DROP_SPEED = 62
MERGE_BONUS = 1000

SWEEP_EVERY = 4
SWEEP_GROUPS = 5
SWEEP_GROUP_SIZE = 8
SWEEP_GAP = 130
SWEEP_PERFECT = 3000
SWEEP_PER_BUG = 120

POINTS = {
    0: {"still": 50, "diving": 100},    # drone
    1: {"still": 80, "diving": 160},    # weevil
    2: {"still": 150, "diving": 400},   # rootkit
}
KIND_DRONE, KIND_WEEVIL, KIND_ROOTKIT = 0, 1, 2

WAVE_BONUS = 200
EXTRA_LIFE_AT = 20000
EXTRA_LIFE_EVERY = 60000
MAX_LIVES = 5
LIVES = 3

MAX_INPUT_TRACE = 20000
ABSOLUTE_MAX_TICKS = 120 * 60 * 12
TAIL_TICKS = 120 * 90
MAX_SCORE = 10000000

FORM_LEFT = (WIDTH - (FORM_COLS - 1) * COL_STEP) // 2

WAVE_TIERS = (
    {"dive_gap": 70, "speed": 88, "fire": 55, "divers": 1},
    {"dive_gap": 40, "speed": 94, "fire": 70, "divers": 2},
    {"dive_gap": 10, "speed": 100, "fire": 85, "divers": 2},
    {"dive_gap": -10, "speed": 108, "fire": 100, "divers": 3},
    {"dive_gap": -22, "speed": 116, "fire": 115, "divers": 4},
    {"dive_gap": -32, "speed": 126, "fire": 130, "divers": 5},
    {"dive_gap": -42, "speed": 138, "fire": 150, "divers": 6},
)

# States, matching the string and integer values in sim.mjs.
S_READY, S_PLAYING, S_DYING, S_CLEAR, S_DEAD = (
    "ready", "playing", "dying", "clear", "dead")
B_WAITING, B_ENTERING, B_SLOT, B_DIVING, B_BEAMING, B_RETURNING, B_SWEEPING, B_DEAD = range(8)
A_LEFT, A_RIGHT, A_NEUTRAL, A_FIRE = 0, 1, 2, 3


def tier_for(wave):
    return WAVE_TIERS[min(max(wave - 1, 0), len(WAVE_TIERS) - 1)]


def is_sweep_wave(wave):
    return wave % SWEEP_EVERY == 0


def _u32(x):
    return x & 0xFFFFFFFF


def _make_rng(seed):
    """mulberry32, matching patchaga/rng.mjs.

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


def _px(v):
    return v * UNIT


def _clamp(v, lo, hi):
    return lo if v < lo else (hi if v > hi else v)


def _sign(v):
    return 1 if v > 0 else (-1 if v < 0 else 0)


def _hits(ax, ay, ahw, ahh, bx, by, bhw, bhh):
    return abs(ax - bx) <= _px(ahw + bhw) and abs(ay - by) <= _px(ahh + bhh)


def form_x(col, t):
    sway = (_px(SWAY_AMP) * isin(t * SIN_STEPS // SWAY_PERIOD)) // SIN_SCALE
    breathe = (_px(BREATHE_AMP) * isin(t * SIN_STEPS // BREATHE_PERIOD)) // SIN_SCALE
    offset = col * 2 - (FORM_COLS - 1)
    return _px(FORM_LEFT + col * COL_STEP) + sway + (offset * breathe) // (FORM_COLS - 1)


def form_y(row):
    return _px(FORM_TOP + row * ROW_STEP)


def _kind_for(row, col):
    if row == 0:
        return KIND_ROOTKIT if col in ROOTKIT_COLS else -1
    return KIND_WEEVIL if row <= 2 else KIND_DRONE


class _Bug:
    __slots__ = (
        "kind", "col", "row", "order", "state", "x", "y", "entry_x", "entry_y",
        "bulge_x", "bulge_y", "vx", "vy", "t", "dive_side", "dive_phase",
        "fire_timer", "beam_open", "wants_fork", "holds_duck", "return_x",
        "return_y", "is_sweep", "sweep_from_left", "sweep_lane", "sweep_phase",
    )

    def __init__(self, kind, col, row, order):
        route = order % 4
        from_left = route in (0, 2)
        from_top = route < 2
        self.kind = kind
        self.col = col
        self.row = row
        self.order = order
        self.state = B_WAITING
        self.x = _px(-30 if from_left else WIDTH + 30)
        self.y = _px(-24 if from_top else HEIGHT + 24)
        self.entry_x = self.x
        self.entry_y = self.y
        self.bulge_x = _px(96 if from_left else -96)
        self.bulge_y = _px(130 if from_top else -130)
        self.vx = 0
        self.vy = 0
        self.t = 0
        self.dive_side = 0
        self.dive_phase = 0
        self.fire_timer = 0
        self.beam_open = False
        self.wants_fork = False
        self.holds_duck = False
        self.return_x = 0
        self.return_y = 0
        self.is_sweep = False
        self.sweep_from_left = False
        self.sweep_lane = 0
        self.sweep_phase = 0


class _Duck:
    __slots__ = ("x", "dir", "alive", "merged", "cooldown", "invuln")

    def __init__(self):
        self.x = _px(WIDTH // 2)
        self.dir = 0
        self.alive = True
        self.merged = False
        self.cooldown = 0
        self.invuln = 0


class Sim:
    """The Patchaga world. One instance is one run."""

    def __init__(self, seed):
        self.seed = _u32(seed)
        self._rng = _make_rng(seed)
        self.tick = 0
        self.state = S_READY
        self.state_tick = 0

        self.score = 0
        self.lives = LIVES
        self.wave = 1
        self.next_extra_life = EXTRA_LIFE_AT

        self.duck = _Duck()
        self.bugs = []
        self.patches = []      # each a [x, y]
        self.bug_shots = []    # each a [x, y, vx, vy]
        self.rescue = None     # [x, y] of the freed duck falling home

        self.launch_index = 0
        self.launch_timer = 0
        self.dive_timer = 0
        self.dives_since_rootkit = 0
        self.sweep_group = 0
        self.sweep_timer = 0
        self.sweep_hits = 0
        self.sweep_total = 0

        self.bugs_patched = 0
        self.waves_cleared = 0
        self.forks = 0
        self.rescues = 0
        self.shots_fired = 0

        self.play_start_tick = -1
        self.end_tick = -1

        self.pending = []
        self.inputs = []

        self._build_wave()

    # --- Setup ------------------------------------------------------------ #

    def _build_wave(self):
        self.bugs = []
        self.launch_index = 0
        self.launch_timer = 0
        self.dive_timer = ENTRY_SETTLE
        self.dives_since_rootkit = ROOTKIT_EVERY
        self.sweep_group = 0
        self.sweep_timer = 0
        self.sweep_hits = 0
        self.sweep_total = 0

        if is_sweep_wave(self.wave):
            self.sweep_total = SWEEP_GROUPS * SWEEP_GROUP_SIZE
            return

        order = 0
        for row in range(FORM_ROWS - 1, -1, -1):
            for col in range(FORM_COLS):
                kind = _kind_for(row, col)
                if kind < 0:
                    continue
                self.bugs.append(_Bug(kind, col, row, order))
                order += 1

    # --- Input ------------------------------------------------------------ #

    def queue_input(self, at_tick, action):
        t = max(self.tick, int(math.floor(at_tick)))
        if t > ABSOLUTE_MAX_TICKS:
            return
        if len(self.inputs) + len(self.pending) >= MAX_INPUT_TRACE:
            return
        self.pending.append(t * 4 + action)

    def _drain_input(self):
        i = 0
        while i < len(self.pending):
            code = self.pending[i]
            if code // 4 > self.tick:
                i += 1
                continue
            self.pending.pop(i)
            self.inputs.append(self.tick * 4 + (code % 4))
            self._apply_action(code % 4)

    def _apply_action(self, action):
        duck = self.duck
        if action == A_LEFT:
            duck.dir = -1
        elif action == A_RIGHT:
            duck.dir = 1
        elif action == A_NEUTRAL:
            duck.dir = 0
        elif action == A_FIRE:
            self._fire()

        if self.state == S_READY and self.state_tick >= READY_TICKS:
            self.state = S_PLAYING
            self.state_tick = 0

    def _fire(self):
        duck = self.duck
        if not duck.alive or self.state != S_PLAYING:
            return
        if duck.cooldown > 0:
            return
        cap = MAX_PATCHES_MERGED if duck.merged else MAX_PATCHES
        if len(self.patches) >= cap:
            return

        duck.cooldown = PATCH_COOLDOWN
        y = _px(DUCK_Y - DUCK_HALF_H)
        if duck.merged:
            self.patches.append([duck.x - _px(MERGED_OFFSET), y])
            self.patches.append([duck.x + _px(MERGED_OFFSET), y])
            self.shots_fired += 2
        else:
            self.patches.append([duck.x, y])
            self.shots_fired += 1

    # --- The step --------------------------------------------------------- #

    def step(self):
        if self.state == S_DEAD:
            return

        self._drain_input()
        self.state_tick += 1

        if self.state == S_READY:
            if self.state_tick > READY_TICKS * 4:
                self.state = S_PLAYING
                self.state_tick = 0
        elif self.state == S_PLAYING:
            if self.play_start_tick < 0:
                self.play_start_tick = self.tick
            self._step_playing()
        elif self.state == S_DYING:
            self._step_bugs()
            if self.state_tick >= DEATH_TICKS:
                self._respawn()
        elif self.state == S_CLEAR:
            if self.state_tick >= CLEAR_TICKS:
                self._next_wave()

        self.tick += 1

        if self.tick >= ABSOLUTE_MAX_TICKS and self.state != S_DEAD:
            self._end_run()

    def _step_playing(self):
        self._step_duck()
        self._step_patches()
        if is_sweep_wave(self.wave):
            self._step_sweep()
        else:
            self._step_formation()
        self._step_bugs()
        self._step_bug_shots()
        self._step_rescue()
        self._collide()
        self._check_wave_over()

    def _step_duck(self):
        duck = self.duck
        if duck.cooldown > 0:
            duck.cooldown -= 1
        if duck.invuln > 0:
            duck.invuln -= 1
        if not duck.alive:
            return
        half = DUCK_HALF_W + (MERGED_OFFSET if duck.merged else 0)
        lo = _px(DUCK_MARGIN + half)
        hi = _px(WIDTH - DUCK_MARGIN - half)
        duck.x = _clamp(duck.x + duck.dir * DUCK_SPEED, lo, hi)

    def _step_patches(self):
        for i in range(len(self.patches) - 1, -1, -1):
            p = self.patches[i]
            p[1] -= PATCH_SPEED
            if p[1] < _px(HUD_TOP - 8):
                self.patches.pop(i)

    def _step_bug_shots(self):
        for i in range(len(self.bug_shots) - 1, -1, -1):
            s = self.bug_shots[i]
            s[0] += s[2]
            s[1] += s[3]
            if s[1] > _px(FLOOR_Y) or s[0] < -_px(12) or s[0] > _px(WIDTH + 12):
                self.bug_shots.pop(i)

    # --- Wave pacing ------------------------------------------------------ #

    def _step_formation(self):
        if self.launch_index < len(self.bugs):
            if self.launch_timer <= 0:
                bug = self.bugs[self.launch_index]
                bug.state = B_ENTERING
                bug.t = 0
                self.launch_index += 1
                self.launch_timer = ENTRY_STAGGER
            else:
                self.launch_timer -= 1
            return

        tier = tier_for(self.wave)
        if self.dive_timer > 0:
            self.dive_timer -= 1
            return

        diving = sum(1 for b in self.bugs if b.state in (B_DIVING, B_BEAMING))
        if diving >= min(tier["divers"], MAX_DIVERS_CAP):
            return

        ready = [b for b in self.bugs if b.state == B_SLOT]
        if not ready:
            return

        pool = ready
        if self.dives_since_rootkit >= ROOTKIT_EVERY:
            rootkits = [b for b in ready if b.kind == KIND_ROOTKIT]
            if rootkits:
                pool = rootkits

        bug = pool[self._rng_int(len(pool))]
        if bug.kind == KIND_ROOTKIT:
            self.dives_since_rootkit = 0
        else:
            self.dives_since_rootkit += 1

        self._launch_dive(bug)
        self.dive_timer = max(
            30, DIVE_GAP_MIN + tier["dive_gap"] + self._rng_int(DIVE_GAP_SPREAD))

    def _rng_int(self, n):
        """Matches rngInt() in patchaga/rng.mjs."""
        return int(self._rng() * n) % n

    def _launch_dive(self, bug):
        bug.state = B_DIVING
        bug.t = 0
        bug.dive_side = -1 if self._rng_int(2) == 0 else 1
        bug.dive_phase = self._rng_int(SIN_STEPS)
        bug.fire_timer = FIRE_EVERY
        bug.vx = 0
        bug.vy = 0
        bug.beam_open = False
        bug.wants_fork = (bug.kind == KIND_ROOTKIT
                          and not bug.holds_duck
                          and self.duck.alive
                          and self._rng_int(100) < FORK_CHANCE)

    def _step_sweep(self):
        if self.sweep_group >= SWEEP_GROUPS:
            return
        if self.sweep_timer > 0:
            self.sweep_timer -= 1
            return

        from_left = self.sweep_group % 2 == 0
        lane = 120 + self._rng_int(120)
        for i in range(SWEEP_GROUP_SIZE):
            bug = _Bug(self.sweep_group % 3, i, 0, i)
            bug.state = B_SWEEPING
            bug.t = -i * 14
            bug.is_sweep = True
            bug.sweep_from_left = from_left
            bug.sweep_lane = lane
            bug.sweep_phase = self._rng_int(SIN_STEPS)
            bug.x = _px(-24 if from_left else WIDTH + 24)
            bug.y = _px(lane)
            self.bugs.append(bug)
        self.sweep_group += 1
        self.sweep_timer = SWEEP_GAP

    # --- Bug motion ------------------------------------------------------- #

    def _step_bugs(self):
        # Backwards, and it matters. A beaming bug can capture the duck from
        # inside this loop, and losing the duck sends every diver home -- so the
        # bugs already stepped this tick keep the state they were given, and the
        # ones not yet reached are stepped again in their new state. Iterating
        # the other way splits that set differently and quietly produces a
        # different game. sim.mjs iterates backwards, so this does too.
        for i in range(len(self.bugs) - 1, -1, -1):
            bug = self.bugs[i]
            bug.t += 1
            if bug.state == B_ENTERING:
                self._step_entering(bug)
            elif bug.state == B_SLOT:
                self._step_slot(bug)
            elif bug.state == B_DIVING:
                self._step_diving(bug)
            elif bug.state == B_BEAMING:
                self._step_beaming(bug)
            elif bug.state == B_RETURNING:
                self._step_returning(bug)
            elif bug.state == B_SWEEPING:
                self._step_sweeping(bug)

        for i in range(len(self.bugs) - 1, -1, -1):
            if self.bugs[i].state == B_DEAD and self.bugs[i].is_sweep:
                self.bugs.pop(i)

    def _step_entering(self, bug):
        total = ENTRY_TICKS
        t = min(bug.t, total)
        f = (t * SIN_SCALE) // total
        bulge = isin((f * SIN_HALF) // SIN_SCALE)
        tx = form_x(bug.col, self.tick)
        ty = form_y(bug.row)
        bug.x = bug.entry_x + ((tx - bug.entry_x) * f) // SIN_SCALE \
            + (bug.bulge_x * bulge) // SIN_SCALE
        bug.y = bug.entry_y + ((ty - bug.entry_y) * f) // SIN_SCALE \
            + (bug.bulge_y * bulge) // SIN_SCALE
        if bug.t >= total:
            bug.state = B_SLOT
            bug.t = 0

    def _step_slot(self, bug):
        bug.x = form_x(bug.col, self.tick)
        bug.y = form_y(bug.row)

    def _step_diving(self, bug):
        tier = tier_for(self.wave)
        fall = (DIVE_FALL * tier["speed"]) // 100
        bug.vy = (fall * bug.t) // DIVE_EASE_TICKS if bug.t < DIVE_EASE_TICKS else fall

        angle = (bug.t * SIN_STEPS) // DIVE_SWING_PERIOD + bug.dive_phase
        bug.vx = ((DIVE_SWING_SPEED * isin(angle)) // SIN_SCALE) * bug.dive_side
        if bug.t > DIVE_HOME_AFTER and self.duck.alive:
            bug.vx += _sign(self.duck.x - bug.x) * ((DIVE_HOME_PULL * tier["speed"]) // 100)

        bug.x += bug.vx
        bug.y += bug.vy

        if bug.wants_fork and bug.y >= _px(BEAM_HOVER_Y):
            bug.state = B_BEAMING
            bug.t = 0
            bug.beam_open = True
            bug.wants_fork = False
            return

        self._maybe_fire(bug, tier)

        if bug.y > _px(HEIGHT + 30):
            bug.state = B_RETURNING
            bug.t = 0
            bug.return_x = _clamp(bug.x, _px(20), _px(WIDTH - 20))
            bug.return_y = _px(-26)
            bug.x = bug.return_x
            bug.y = bug.return_y

    def _step_beaming(self, bug):
        bug.vx = (DIVE_SWING_SPEED * isin((bug.t * SIN_STEPS) // 360)) // (SIN_SCALE * 3)
        bug.x = _clamp(bug.x + bug.vx, _px(24), _px(WIDTH - 24))

        duck = self.duck
        if bug.t > BEAM_WINDUP and duck.alive and not bug.holds_duck:
            if abs(duck.x - bug.x) <= _px(BEAM_HALF_W):
                self._fork_duck(bug)

        if bug.t >= BEAM_TICKS:
            bug.beam_open = False
            bug.state = B_DIVING
            bug.t = DIVE_EASE_TICKS

    def _step_returning(self, bug):
        total = REENTRY_TICKS
        t = min(bug.t, total)
        f = (t * SIN_SCALE) // total
        tx = form_x(bug.col, self.tick)
        ty = form_y(bug.row)
        bug.x = bug.return_x + ((tx - bug.return_x) * f) // SIN_SCALE
        bug.y = bug.return_y + ((ty - bug.return_y) * f) // SIN_SCALE
        if bug.t >= total:
            bug.state = B_SLOT
            bug.t = 0

    def _step_sweeping(self, bug):
        if bug.t < 0:
            return
        tier = tier_for(self.wave)
        speed = (150 * tier["speed"]) // 100
        bug.vx = speed if bug.sweep_from_left else -speed
        bug.vy = (46 * isin((bug.t * SIN_STEPS) // 200 + bug.sweep_phase)) // SIN_SCALE
        bug.x += bug.vx
        bug.y += bug.vy
        if bug.x < -_px(40) or bug.x > _px(WIDTH + 40):
            bug.state = B_DEAD

    def _maybe_fire(self, bug, tier):
        if is_sweep_wave(self.wave):
            return
        if not self.duck.alive:
            return
        if len(self.bug_shots) >= MAX_BUG_SHOTS:
            return
        if bug.fire_timer > 0:
            bug.fire_timer -= 1
            return

        bug.fire_timer = FIRE_EVERY
        chance = (FIRE_CHANCE * tier["fire"]) // 100
        if self._rng_int(100) >= chance:
            return

        lean = _clamp((self.duck.x - bug.x) // 96, -BUG_SHOT_SPREAD, BUG_SHOT_SPREAD)
        self.bug_shots.append([
            bug.x,
            bug.y + _px(BUG_HALF_H),
            (BUG_SHOT_SPEED * isin(lean)) // SIN_SCALE,
            (BUG_SHOT_SPEED * icos(lean)) // SIN_SCALE,
        ])

    # --- The fork and the rescue ------------------------------------------ #

    def _fork_duck(self, bug):
        bug.holds_duck = True
        bug.beam_open = False
        bug.state = B_DIVING
        bug.t = 0
        self.forks += 1
        self._lose_duck(True)

    def _step_rescue(self):
        r = self.rescue
        if r is None:
            return
        r[1] += RESCUE_DROP_SPEED
        if r[1] >= _px(DUCK_Y) and self.duck.alive:
            self.rescue = None
            if not self.duck.merged:
                self.duck.merged = True
                self._add_score(MERGE_BONUS)
                self.rescues += 1
            return
        if r[1] > _px(HEIGHT + 20):
            self.rescue = None

    # --- Damage ----------------------------------------------------------- #

    def _lose_duck(self, forked):
        duck = self.duck
        if not duck.alive or duck.invuln > 0:
            return

        if duck.merged and not forked:
            duck.merged = False
            duck.invuln = 90
            return

        duck.alive = False
        duck.merged = False
        duck.dir = 0
        self.lives -= 1
        self.bug_shots = []

        for b in self.bugs:
            if b.state in (B_DIVING, B_BEAMING):
                b.beam_open = False
                b.wants_fork = False
                b.state = B_RETURNING
                b.t = 0
                b.return_x = _clamp(b.x, _px(20), _px(WIDTH - 20))
                b.return_y = b.y

        if self.lives <= 0:
            self._end_run()
        else:
            self.state = S_DYING
            self.state_tick = 0

    def _respawn(self):
        self.duck = _Duck()
        self.duck.invuln = RESPAWN_TICKS
        self.state = S_PLAYING
        self.state_tick = 0

    def _end_run(self):
        if self.state == S_DEAD:
            return
        self.state = S_DEAD
        self.state_tick = 0
        self.end_tick = self.tick

    # --- Collisions -------------------------------------------------------- #

    def _collide(self):
        duck = self.duck

        for pi in range(len(self.patches) - 1, -1, -1):
            p = self.patches[pi]
            hit = -1
            for bi, b in enumerate(self.bugs):
                if b.state in (B_DEAD, B_WAITING):
                    continue
                if b.state == B_SWEEPING and b.t < 0:
                    continue
                if _hits(p[0], p[1], PATCH_HALF_W, PATCH_HALF_H,
                         b.x, b.y, BUG_HALF_W, BUG_HALF_H):
                    hit = bi
                    break
            if hit < 0:
                continue
            self.patches.pop(pi)
            self._kill_bug(self.bugs[hit])

        if not duck.alive:
            return

        half = DUCK_HALF_W + (MERGED_OFFSET if duck.merged else 0)

        for i in range(len(self.bug_shots) - 1, -1, -1):
            s = self.bug_shots[i]
            if _hits(s[0], s[1], BUG_SHOT_HALF_W, BUG_SHOT_HALF_H,
                     duck.x, _px(DUCK_Y), half, DUCK_HALF_H):
                self.bug_shots.pop(i)
                self._lose_duck(False)
                return

        for b in self.bugs:
            if b.state not in (B_DIVING, B_BEAMING, B_SWEEPING):
                continue
            if b.state == B_SWEEPING and b.t < 0:
                continue
            if _hits(b.x, b.y, BUG_HALF_W, BUG_HALF_H,
                     duck.x, _px(DUCK_Y), half, DUCK_HALF_H):
                self._lose_duck(False)
                return

    def _kill_bug(self, bug):
        diving = bug.state in (B_DIVING, B_BEAMING, B_SWEEPING)
        table = POINTS[bug.kind]
        self._add_score(table["diving"] if diving else table["still"])
        self.bugs_patched += 1

        if bug.state == B_SWEEPING:
            self.sweep_hits += 1

        if bug.holds_duck:
            if diving:
                self.rescue = [bug.x, bug.y]
            bug.holds_duck = False

        bug.state = B_DEAD
        bug.beam_open = False

    def _add_score(self, points):
        self.score = min(self.score + points, MAX_SCORE)
        while self.score >= self.next_extra_life and self.lives < MAX_LIVES:
            self.lives += 1
            self.next_extra_life += EXTRA_LIFE_EVERY
        while self.score >= self.next_extra_life:
            self.next_extra_life += EXTRA_LIFE_EVERY

    # --- Wave completion --------------------------------------------------- #

    def _check_wave_over(self):
        if is_sweep_wave(self.wave):
            done = (self.sweep_group >= SWEEP_GROUPS
                    and not any(b.state == B_SWEEPING for b in self.bugs))
            if not done:
                return
            self._add_score(self.sweep_hits * SWEEP_PER_BUG)
            if self.sweep_total > 0 and self.sweep_hits >= self.sweep_total:
                self._add_score(SWEEP_PERFECT)
            self.waves_cleared += 1
            self.state = S_CLEAR
            self.state_tick = 0
            return

        if any(b.state != B_DEAD for b in self.bugs):
            return
        self._add_score(WAVE_BONUS * self.wave)
        self.waves_cleared += 1
        self.state = S_CLEAR
        self.state_tick = 0

    def _next_wave(self):
        self.wave += 1
        self.patches = []
        self.bug_shots = []
        self.rescue = None
        self._build_wave()
        self.state = S_PLAYING
        self.state_tick = 0

    # --- Readings ---------------------------------------------------------- #

    def duration_ms(self):
        """Simulated milliseconds, measured from the first input.

        Math.floor(x + 0.5) is what Math.round does, and unlike Python's
        round() it does not turn a halfway value into the nearest even one.
        """
        if self.play_start_tick < 0:
            return 0
        end = self.end_tick if self.end_tick >= 0 else self.tick
        return int(math.floor((end - self.play_start_tick) * STEP_MS + 0.5))


def replay(seed, inputs, max_ticks=None):
    """Re-run a submitted trace and report what actually happened.

    The tail exists because a run does not end on its last input: the final life
    still has to be lost, and the bugs are perfectly capable of doing that with
    nobody touching the controls.
    """
    sim = Sim(seed)
    for code in inputs:
        sim.queue_input(code // 4, code % 4)

    last = (inputs[-1] // 4) if inputs else 0
    ceiling = max_ticks if max_ticks is not None else min(
        last + TAIL_TICKS, ABSOLUTE_MAX_TICKS)

    while sim.state != S_DEAD and sim.tick < ceiling:
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

SESSION_TTL_MS = 45 * 60 * 1000   # a seed goes stale rather than waiting forever
CLOCK_SLACK_MS = 3000             # request latency and a second of clock drift
BEAT_INTERVAL_MS = 5000           # matches the client's heartbeat timer
MIN_BEAT_RATIO = 0.4              # a dropped beat or two is normal
BEAT_FLOOR = 2                    # below this a run is too short to judge


def _now_ms():
    return int(time.time() * 1000)


def issue_session(ip):
    """Mint a seed and start its clock."""
    sid = secrets.token_hex(16)
    # Matches randomSeed() in patchaga/rng.mjs, from a source worth trusting.
    seed = secrets.randbelow(0x7FFFFFFF) + 1
    _write(
        "INSERT INTO patchaga_sessions (id, seed, issued_at, issued_ip) "
        "VALUES (?, ?, ?, ?)",
        (sid, seed, _now_ms(), ip),
    )
    return {"session": sid, "seed": seed}


def claim_session(sid, ip):
    """Look up a session for submission. Returns ``(row, error)``."""
    if not sid:
        return None, ("That run did not come with a session. Reload the page.", 400)
    rows = _rows("SELECT * FROM patchaga_sessions WHERE id = ?", (sid,))
    if not rows:
        return None, ("That run's session is not one this server issued.", 400)
    row = rows[0]
    if row["consumed_at"] is not None:
        return None, ("That run has already been posted.", 409)
    if _now_ms() - int(row["issued_at"]) > SESSION_TTL_MS:
        return None, ("That run took too long to post. Start a fresh one.", 400)
    return row, None


def consume_session(sid, now_ms):
    _write("UPDATE patchaga_sessions SET consumed_at = ? WHERE id = ?", (now_ms, sid))


def record_beat(sid, tick):
    """Note that a session was still being played, and how far it had got.

    The tick matters as much as the timestamp. Comparing how far the simulation
    advanced against how much real time passed is what catches a run that was
    computed rather than played.
    """
    rows = _rows(
        "SELECT first_beat, beats, consumed_at FROM patchaga_sessions WHERE id = ?",
        (sid,))
    if not rows or rows[0]["consumed_at"] is not None:
        return False
    now = _now_ms()
    if rows[0]["first_beat"] is None:
        _write(
            "UPDATE patchaga_sessions SET first_beat = ?, first_tick = ?, "
            "last_beat = ?, last_tick = ?, beats = 1 WHERE id = ?",
            (now, tick, now, tick, sid),
        )
    else:
        _write(
            "UPDATE patchaga_sessions SET last_beat = ?, last_tick = ?, "
            "beats = beats + 1 WHERE id = ?",
            (now, tick, sid),
        )
    return True


def sweep_sessions():
    """Drop sessions too old to be redeemed. Cheap, and keeps the table small."""
    _write("DELETE FROM patchaga_sessions WHERE issued_at < ?",
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
#   the hand        steering lands on a spread of intervals because hands are
#                   not clocks, and machine timing shows up as a spike on one
#                   exact value
#   the aim         a solver does not miss, and a hand does
#
# On separating steering from fire. This is the one place Patchaga has to differ
# from PatchMan, and getting it wrong would flag every honest player. PatchMan
# has a single input stream and every input is hand timed, so the spread of
# gaps between them is meaningful. Fire here is not, even though the client
# fires one patch per press and never repeats a held key.
#
# The reason is that a fire press is not recorded when the hand made it. It is
# recorded at the first tick the duck was actually allowed to shoot, because
# the client only queues a press that will produce a patch. So the cooldown and
# the cap on patches in the air together quantise the fire stream onto a grid
# nobody chose, and a run of evenly spaced fire ticks is evidence about how fast
# patches clear the screen rather than about whose hand pressed the key.
# Measuring it for regularity would find the cooldown, on every run, for
# everyone. Steering carries no such gate: a steer is recorded at the moment the
# key moved, so its spacing is the hand's.
#
# So the timing checks read the steering stream only, and the fire stream is
# judged on its result instead: accuracy. A solver picks the tick that hits, so
# it hits with nearly everything it fires. That tell does not exist in a maze
# chase and is the strongest one available here.
#
# What this deliberately does not claim: a patient attacker who paces a forged
# run in real time, scatters its steering, and deliberately misses often enough
# can still get through. For an office side game that is far enough.

# Steering that is too evenly spaced to be a hand. A solver turns on exact tick
# boundaries, so its gaps pile up on a handful of values.
#
# These thresholds are deliberately loose. Flappy Duck had 800 measured human
# runs to calibrate against; this game has none yet, and the cost of a false
# positive -- telling someone their real run does not count -- is much worse
# than the cost of letting a careful forgery through on an office game. Tighten
# them once there is a corpus to tighten them against.
MODAL_SHARE_LIMIT = 0.35
MODAL_MIN_INTERVALS = 30

# Six direction changes a second, held for half a minute, is not a hand. Short
# bursts while dodging are, so this only applies once a run is long enough that
# a burst cannot be the explanation. A shooter dodges more than a maze chase
# turns, so this is looser than PatchMan's equivalent.
MAX_STEERS_PER_SEC = 6.0
RATE_MIN_DURATION_MS = 20000

# Two inputs a 40th of a second apart are one press as far as a hand is
# concerned. A few are a fumbled dodge; dozens are a machine.
MIN_HUMAN_GAP_TICKS = 3
MAX_SHORT_GAPS = 20

# Accuracy. A patch that misses flies off the top of the screen and is wasted,
# so hit rate is a real measure of aim rather than of persistence. The floor on
# shots keeps a lucky opening burst from being judged.
#
# This is deliberately far above anything observed. The reference bot in
# tools/patchaga_bot.mjs -- which aims at a chosen target and fires when it is
# lined up -- lands 38 to 75 percent across 30 runs, median 56. A solver that
# only fires shots it has already proven will connect sits near 100, because
# nothing about the game forces a wasted shot. The gap between those two is
# where the threshold goes, and it is placed near the top of it on purpose:
# there is no corpus of human runs to calibrate against yet, so the cost of
# guessing low is telling an honest player their run does not count.
MAX_HUMAN_ACCURACY = 0.92
ACCURACY_MIN_SHOTS = 60

FLAG_TEXT = {
    "faster_than_real_time": "That run finished sooner than it could have been played.",
    "not_enough_beats": "That run was not in contact while it was being played.",
    "beats_outran_clock": "That run reported progress faster than time passed.",
    "machine_timing": "That run's steering is too evenly spaced to be hand timed.",
    "steer_rate": "That run holds a steering rate a hand cannot keep up.",
    "double_inputs": "That run has inputs too close together to be separate presses.",
    "inhuman_accuracy": "That run lands more of its shots than a hand can aim.",
    "replay_mismatch": "That run does not replay to the score it was posted with.",
    "unreadable_trace": "That run's input trace could not be read.",
    "scored_without_playing": "That run scored without recording any input.",
    "no_trace": "That run's input trace is no longer stored.",
    "trace_trimmed": "That run's input trace was longer than the stored limit.",
}

# The flags that came from comparing the run against real time. They are raised
# once, at submission, from the session's heartbeats -- and the heartbeats are
# not kept forever. So nothing can decide these again after the fact, and
# anything that re-judges a stored run has to carry them forward rather than
# recompute them. audit_run() deliberately does not return them.
CLOCK_FLAGS = ("faster_than_real_time", "not_enough_beats", "beats_outran_clock")


def steering_codes(inputs):
    """The inputs a hand timed, reduced to one entry per motion.

    Two things are dropped. Fire, because a press is recorded at the first tick
    the duck was allowed to shoot rather than when the hand made it, so the
    cooldown and the cap on patches in the air quantise its timing onto a grid
    the player has no say in. It measures the gun, not the player.

    And the release half of a roll. Moving from left to right is one motion of
    one hand, but the browser reports it as two events: the left key coming up
    and the right key going down, microseconds apart. The client faithfully
    records both, so the trace shows a neutral and a direction on the same tick.
    Counted as two inputs that is a hand pressing twice in under a hundredth of
    a second, which is exactly the thing ``double_inputs`` exists to catch --
    and it would catch every player who ever changed direction quickly.

    Note what this does not do: it removes the neutral, never the direction that
    follows it. A solver cannot use it to launder its rate, because the presses
    it makes stay on the ticks it made them on, and their gaps are unchanged.
    """
    codes = [c for c in inputs if c % 4 != A_FIRE]
    motions = []
    for i, code in enumerate(codes):
        if code % 4 == A_NEUTRAL and i + 1 < len(codes):
            nxt = codes[i + 1]
            if nxt % 4 != A_NEUTRAL \
                    and (nxt // 4) - (code // 4) < MIN_HUMAN_GAP_TICKS:
                continue
        motions.append(code)
    return motions


def interval_stats(inputs):
    """Shape of the gaps between steering inputs, where a machine gives itself away."""
    ticks = [c // 4 for c in steering_codes(inputs)]
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


def accuracy_of(sim):
    """Fraction of patches fired that hit something. None when too few to judge."""
    if sim.shots_fired < ACCURACY_MIN_SHOTS:
        return None
    return sim.bugs_patched / float(sim.shots_fired)


def hand_flags(sim):
    """The checks that need only the trace, so a stored run can be re-judged."""
    flags = []
    stats = interval_stats(sim.inputs)
    duration = sim.duration_ms()

    if stats["intervals"] >= MODAL_MIN_INTERVALS \
            and stats["modal_share"] >= MODAL_SHARE_LIMIT:
        flags.append("machine_timing")

    if duration >= RATE_MIN_DURATION_MS:
        steers = len(steering_codes(sim.inputs))
        if steers / (duration / 1000.0) > MAX_STEERS_PER_SEC:
            flags.append("steer_rate")

    if stats["short"] > MAX_SHORT_GAPS:
        flags.append("double_inputs")

    hit_rate = accuracy_of(sim)
    if hit_rate is not None and hit_rate > MAX_HUMAN_ACCURACY:
        flags.append("inhuman_accuracy")

    return flags


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

    return flags + hand_flags(sim)


def read_trace(raw):
    """Validate the submitted trace enough to replay it safely.

    Deliberately not a plausibility check. The only questions here are whether
    it is a list of ascending tick numbers carrying a real action, and whether
    replaying it will cost the server a bounded amount of work.

    Two inputs can legitimately land on the same tick -- steering away from a
    dive and firing at it are one motion to the player -- so ticks must not
    decrease rather than must increase.
    """
    if len(raw) > MAX_INPUT_TRACE:
        return None, "That run recorded more inputs than a run can contain."
    inputs = []
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
        inputs.append(code)
        last = tick
    return inputs, None


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
        # firing, so a score here was typed, not played.
        if claimed_score > 0:
            return 0, ["scored_without_playing"]
        return 1, []

    inputs, error = read_trace(raw[:MAX_INPUT_TRACE])
    if error:
        return 0, ["unreadable_trace"]

    sim = replay(seed, inputs)

    trimmed = len(raw) >= MAX_INPUT_TRACE
    if not trimmed and sim.score != claimed_score:
        return 0, ["replay_mismatch"]

    # The clock checks need a session, so only the hand checks apply here.
    flags = hand_flags(sim)
    if trimmed and not flags:
        flags.append("trace_trimmed")
        return 1, flags

    return (0 if flags else 1), flags


def audit_pending_runs():
    """Judge every run that has not been judged yet. Safe to run twice."""
    pending = _rows(
        "SELECT id, score, seed, inputs FROM patchaga_runs "
        "WHERE flags IS NULL ORDER BY id LIMIT ?",
        (AUDIT_LIMIT,),
    )
    if not pending:
        return {"checked": 0, "voided": 0}

    voided = 0
    for row in pending:
        verified, flags = audit_run(row["seed"], row["score"], row["inputs"])
        if not verified:
            voided += 1
        _write(
            "UPDATE patchaga_runs SET verified = ?, flags = ? WHERE id = ?",
            (verified, json.dumps(flags), row["id"]),
        )

    log.info("patchaga: audited %d runs, %d no longer count", len(pending), voided)
    return {"checked": len(pending), "voided": voided}


def clear_board(player=None):
    """Delete runs. Everything, or one player.

    This lives here rather than only in the admin script because the admin
    script is not in the container: the compose file mounts ./app and ./data and
    nothing else. Clearing the board on the server therefore has to go through
    this module, and the alternative is someone hand typing DELETE against the
    database the real leaderboard's buffer also lives in. Keeping the statement
    here is what makes "only patchaga_ tables are touched" a property of the
    code instead of a promise about a copied command.

        docker exec <container> python -c \\
            "import sys; sys.path.insert(0, '/app'); \\
             import patchaga; print(patchaga.clear_board())"
    """
    if player:
        key = name_key(player)
        if not key:
            return {"deleted": 0, "player": player}
        before = _rows("SELECT COUNT(*) AS n FROM patchaga_runs WHERE player_key = ?",
                       (key,))[0]["n"]
        _write("DELETE FROM patchaga_runs WHERE player_key = ?", (key,))
        log.info("patchaga: cleared %d runs for %s", before, player)
        return {"deleted": before, "player": player}

    before = _rows("SELECT COUNT(*) AS n FROM patchaga_runs")[0]["n"]
    _write("DELETE FROM patchaga_runs")
    _write("DELETE FROM patchaga_sessions")
    log.info("patchaga: cleared the board, %d runs deleted", before)
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
        "SELECT created_at FROM patchaga_runs WHERE player_key = ? "
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
        "SELECT COUNT(*) AS n FROM patchaga_runs "
        "WHERE player_key = ? AND created_at >= ? AND created_at < ?",
        (player_key, start, end))[0]["n"]
    if today >= MAX_RUNS_PER_DAY:
        return ("That is %d runs today. The board will still be here tomorrow."
                % today, 429)

    if ip:
        from_ip = _rows(
            "SELECT COUNT(*) AS n FROM patchaga_runs "
            "WHERE session_id IN (SELECT id FROM patchaga_sessions WHERE issued_ip = ?) "
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
            "UPDATE patchaga_runs SET inputs = NULL "
            "WHERE inputs IS NOT NULL AND created_at < ?", (cutoff,))
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
    inputs: list[int] = Field(default_factory=list)


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
    inputs, trace_error = read_trace(payload.inputs)
    if trace_error:
        return _reject(trace_error)

    session, session_error = claim_session(payload.session, ip)
    if session_error:
        return _reject(session_error[0], status=session_error[1])

    sim = replay(session["seed"], inputs)
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
        "SELECT COALESCE(MAX(score), 0) AS best FROM patchaga_runs "
        "WHERE player_key = ? AND verified = 1",
        (key,),
    )[0]["best"]

    _write(
        "INSERT INTO patchaga_runs "
        "(player, player_key, score, seed, duration_ms, inputs, created_at, "
        " verified, session_id, elapsed_ms, input_count, wave, bugs, shots, "
        " rescues, flags) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (canonical, key, score, int(session["seed"]), duration,
         json.dumps(sim.inputs[:MAX_INPUT_TRACE]), _utc_stamp(), verified,
         session["id"], elapsed, len(sim.inputs), sim.wave, sim.bugs_patched,
         sim.shots_fired, sim.rescues, json.dumps(flags)),
    )
    prune_traces()

    if not verified:
        return JSONResponse(status_code=202, content={
            "ok": True,
            "counted": False,
            "player": canonical,
            "score": score,
            "wave": sim.wave,
            "bugs": sim.bugs_patched,
            "reason": FLAG_TEXT.get(flags[0], "That run could not be verified."),
        })

    rank = next((r["rank"] for r in board_rows("alltime") if r["player_key"] == key), None)
    return {
        "ok": True,
        "counted": True,
        "player": canonical,
        "score": score,
        "wave": sim.wave,
        "bugs": sim.bugs_patched,
        "rescues": sim.rescues,
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
            "SELECT COUNT(*) AS n FROM patchaga_sessions "
            "WHERE issued_ip = ? AND issued_at >= ?", (ip, cutoff))[0]["n"]
        if minted >= SESSION_MINT_PER_HOUR:
            return _reject("Starting runs a little fast. Try again shortly.", status=429)
        open_now = _rows(
            "SELECT COUNT(*) AS n FROM patchaga_sessions "
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
    log.exception("patchaga: startup audit failed, board left as it was")
