"""Ducker: a side game that shares the app and nothing else.

Isolation is the whole point of this module, so it is worth being explicit
about what that means:

* It never opens ``leaderboard.json`` or ``history.json`` at all, for reading or
  for writing. Players type their own name, so there is no roster to consult.
* It never touches a daily rank, penalty, streak, excused day, weekly total or
  win counter. Nothing in here knows those concepts exist.
* It adds no scheduled job. Seasons are derived from ``created_at`` when a board
  is queried, so there is no rollover to run and nothing to get out of step.
* Its tables are prefixed ``ducker_`` and live in the SQLite file the app
  already keeps on the data volume, so a deploy stays "upload files, restart".

The rules of the game itself live in ``ducker/sim.mjs`` and every tuning value
lives in ``ducker/config.mjs``. That simulation is mirrored here, exactly, so
the server can replay a submitted run rather than take its word for the score.

On trusting submissions. A deterministic game is replayable, and a replayable
run is forgeable: anyone can import the client's own rules, search for a good
input trace offline, and hand over a trace that replays perfectly. Replay proves
a trace is self-consistent, never that a person produced it. So the seed is
issued by the server and spent once, the run is paced against the server's own
clock through heartbeats, and the trace is measured for input a human hand
cannot produce. Replay is the floor here, not the ceiling.

On matching the JavaScript exactly. This game holds every position that can
move by less than a pixel in a tick as an integer number of sub-units rather
than as a float, which removes the usual source of drift between two ports.
Lane speed is scaled from an integer base by an integer percent with a floor
division, never by multiplying a float and rounding -- rounding a float is
the one piece of arithmetic a browser and a Python port are not guaranteed to
agree on at an exact .5 boundary. The generator is the only remaining hazard,
because JavaScript's bitwise operators coerce to 32 bits, so its state is
held here as an unsigned Python int and masked after every operation.

None of the artwork, the lanes or the names come from any existing arcade
game. It is a lane-crossing game, which is a genre, built out of Patch My
PC's own material: a rubber duck crossing a highway of vulnerabilities to
reach the patch notes on the other side.
"""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, FileResponse
from pydantic import BaseModel, Field
import json
import logging
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


router = APIRouter(prefix="/ducker", tags=["ducker"])

log = logging.getLogger("ducker")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "ducker")

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
    "volume": "Most crossed",
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
    CREATE TABLE IF NOT EXISTS ducker_runs (
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
        level       INTEGER,
        slots       INTEGER,
        hops        INTEGER,
        flags       TEXT
    )
    """
)

# A run is issued before it is played, so the server knows which world the
# player was given and when the clock started. One row per attempt, spent once.
_conn.execute(
    """
    CREATE TABLE IF NOT EXISTS ducker_sessions (
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

_conn.execute("CREATE INDEX IF NOT EXISTS idx_ducker_created ON ducker_runs(created_at)")
_conn.execute("CREATE INDEX IF NOT EXISTS idx_ducker_player ON ducker_runs(player_key)")
_conn.execute("CREATE INDEX IF NOT EXISTS idx_ducker_score ON ducker_runs(score DESC, id ASC)")
_conn.execute("CREATE INDEX IF NOT EXISTS idx_ducker_sess ON ducker_sessions(issued_at)")
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

_BEST_PER_PLAYER = """
    WITH ranked AS (
        SELECT id, player, player_key, score, seed, duration_ms, created_at, level,
               ROW_NUMBER() OVER (
                   PARTITION BY player_key
                   ORDER BY score DESC, created_at ASC, id ASC
               ) AS rn
        FROM ducker_runs
        WHERE score > 0 AND verified = 1 %s
    )
    SELECT id, player, player_key, score, seed, duration_ms, created_at, level
    FROM ranked
    WHERE rn = 1
    ORDER BY score DESC, created_at ASC, id ASC
"""

_VOLUME = """
    SELECT player_key,
           SUM(score)      AS total,
           COUNT(*)        AS runs,
           MAX(score)      AS best,
           MAX(created_at) AS last_at
    FROM ducker_runs
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
        "SELECT player_key, player FROM ducker_runs "
        "WHERE id IN (SELECT MAX(id) FROM ducker_runs "
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
            "created_at": r["created_at"],
            "season": _season_of(r["created_at"]),
        })
    return out


def board_view(view, player_key=None, limit=BOARD_LIMIT):
    """The top slice, plus the asking player's own row when it falls outside it."""
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
    """Winners of finished seasons, newest first."""
    first = _rows("SELECT MIN(created_at) AS m FROM ducker_runs "
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

    stored = _display_names().get(key)

    totals = _rows(
        "SELECT COUNT(*) AS runs, COALESCE(SUM(score), 0) AS total, "
        "COALESCE(MAX(score), 0) AS best, COALESCE(SUM(slots), 0) AS slots, "
        "COALESCE(MAX(level), 0) AS furthest FROM ducker_runs "
        "WHERE player_key = ? AND verified = 1",
        (key,),
    )[0]

    def best_in(bounds):
        clause, params = _range_clause(bounds)
        sql = ("SELECT COALESCE(MAX(score), 0) AS best FROM ducker_runs "
               "WHERE player_key = ? AND verified = 1" + clause)
        return _rows(sql, (key,) + params)[0]["best"]

    ranks = {}
    for view in VIEWS:
        ranks[view] = next(
            (r["rank"] for r in board_rows(view) if r["player_key"] == key), None)

    recent = _rows(
        "SELECT score, duration_ms, level, slots, created_at "
        "FROM ducker_runs "
        "WHERE player_key = ? AND verified = 1 ORDER BY id DESC LIMIT 10",
        (key,),
    )

    return {
        "ok": True,
        "player": stored or canonical,
        "runs": totals["runs"],
        "total": totals["total"],
        "best": totals["best"],
        "slots": totals["slots"],
        "furthest_level": totals["furthest"],
        "best_season": best_in(_bounds_for("season")),
        "best_today": best_in(_bounds_for("today")),
        "ranks": ranks,
        "recent": recent,
    }


# --------------------------------------------------------------------------- #
# The simulation, ported from ducker/sim.mjs
# --------------------------------------------------------------------------- #
#
# This is the same game the browser runs, in Python, tick for tick. It exists
# so that a submitted score is something the server works out rather than
# something the client asserts. Everything below is a direct translation; if
# it needs to change, change ducker/sim.mjs first and keep the two in step.

def _u32(x):
    return x & 0xFFFFFFFF


def _make_rng(seed):
    """mulberry32, matching ducker/rng.mjs."""
    a = _u32(seed)

    def nxt():
        nonlocal a
        a = _u32(a + 0x6D2B79F5)
        t = a
        t = _u32(_u32(t ^ (t >> 15)) * _u32(t | 1))
        t = _u32(t ^ _u32(t + _u32(_u32(t ^ (t >> 7)) * _u32(t | 61))))
        return _u32(t ^ (t >> 14)) / 4294967296.0

    return nxt


def _rng_int(next_fn, n):
    import math
    return int(math.floor(next_fn() * n)) % n


def fdiv(a, b):
    return a // b


# --- Grid ------------------------------------------------------------------- #

CELL = 40
COLS = 13
UNIT = 64
GOAL_ROW = 0
RIVER_TOP = 1
RIVER_BOTTOM = 5
MEDIAN_ROW = 6
ROAD_TOP = 7
ROAD_BOTTOM = 11
START_ROW = 12
START_COL = 6
CELL_SU = CELL * UNIT
WIDTH_SU = COLS * CELL_SU
MARGIN_SU = 2 * CELL_SU
WRAP_SU = WIDTH_SU + 2 * MARGIN_SU

ROAD_ROWS = (11, 10, 9, 8, 7)
RIVER_ROWS = (5, 4, 3, 2, 1)

DUCK_HALF_W = 14
DUCK_HALF_H = 14
HOP_TICKS = 7

SLOT_COLS = (0, 3, 6, 9, 12)
# A hop always lands on a grid column (see _snap_col), and with slots three
# columns apart, every column is at most one cell from its nearest slot. A
# half-width under one cell means only the slot's own column ever counts, so
# anyone who drifted a single column off during the crossing dies at the
# hedge despite having been visibly lined up with a slot. One full cell of
# half-width is what makes every column reach its nearest slot. Matches
# CONFIG.slotHalfW in config.mjs.
SLOT_HALF_W = 40

ROAD_SPEED_SU = (24, 32, 40, 50, 60)
RIVER_SPEED_SU = (22, 30, 38, 46, 54)
ROAD_ENTITY_HALF_W = 16
# The raft is wider and there are more of them per lane than the beetle count
# would suggest, and deliberately so: a beetle missed just costs a wait for
# the next gap, but a raft missed is a life gone with no recourse, so the
# river has to be the more forgiving of the two to land at the same felt
# difficulty. At the old width and count, a raft covered roughly 30% of the
# lane at any instant; this tiling covers roughly 55-60%. Matches
# CONFIG.riverEntityHalfW / roadEntitiesPerLane / riverEntitiesPerLane in
# config.mjs.
RIVER_ENTITY_HALF_W = 50
ROAD_ENTITIES_PER_LANE = 3
RIVER_ENTITIES_PER_LANE = 4

READY_TICKS = 60
DYING_TICKS = 60
CLEAR_TICKS = 150
LIFE_TICKS = 4800

ROW_POINTS = 10
SLOT_POINTS = 50
TIME_BONUS_DIVISOR = 10
LEVEL_CLEAR_BONUS = 1000
EXTRA_LIFE_AT = 20000
EXTRA_LIFE_EVERY = 60000
MAX_LIVES = 6
LIVES = 4

LEVEL_SPEED_PCT = (100, 112, 125, 138, 150, 162, 175)

STEP_MS = 1000.0 / 120.0
MAX_INPUT_TRACE = 12000
ABSOLUTE_MAX_TICKS = 120 * 60 * 12  # twelve minutes
TAIL_TICKS = 120 * 30
MAX_SCORE = 10000000

S_READY, S_PLAYING, S_DYING, S_CLEAR, S_DEAD = (
    "ready", "playing", "dying", "clear", "dead")

A_UP, A_DOWN, A_LEFT, A_RIGHT = 0, 1, 2, 3


def _px(v):
    return v * UNIT


def _clamp(v, lo, hi):
    return lo if v < lo else (hi if v > hi else v)


def _snap_col(x):
    """The nearest column's x, in sub-units. Matches snapCol() in sim.mjs."""
    col = fdiv(x + fdiv(CELL_SU, 2), CELL_SU)
    return _clamp(col, 0, COLS - 1) * CELL_SU


def _lane_dir(index):
    return 1 if index % 2 == 0 else -1


def _tier_speed_pct(level):
    i = min(max(level - 1, 0), len(LEVEL_SPEED_PCT) - 1)
    return LEVEL_SPEED_PCT[i]


class _Entity:
    __slots__ = ("x", "half_w")

    def __init__(self, x, half_w):
        self.x = x
        self.half_w = half_w


class _Lane:
    __slots__ = ("row", "dir", "speed_su", "half_w", "entities")

    def __init__(self, row, direction, speed_su, half_w, entities):
        self.row = row
        self.dir = direction
        self.speed_su = speed_su
        self.half_w = half_w
        self.entities = entities


def _make_lane(row, direction, speed_su, half_w, count, rng):
    gap = fdiv(WRAP_SU, count)
    phase = _rng_int(rng, gap)
    entities = []
    for i in range(count):
        loop = (i * gap + phase) % WRAP_SU
        entities.append(_Entity(loop - MARGIN_SU, half_w))
    return _Lane(row, direction, speed_su, half_w, entities)


def _advance_lane(lane):
    for e in lane.entities:
        e.x += lane.dir * lane.speed_su
        if lane.dir > 0 and e.x - _px(e.half_w) > WIDTH_SU + MARGIN_SU:
            e.x -= WRAP_SU
        elif lane.dir < 0 and e.x + _px(e.half_w) < -MARGIN_SU:
            e.x += WRAP_SU


class Sim:
    """One run. Advance it with step() and nothing else."""

    def __init__(self, seed):
        self.seed = _u32(seed)
        self._rng = _make_rng(seed)
        self.tick = 0
        self.state = S_READY
        self.state_tick = 0

        self.score = 0
        self.lives = LIVES
        self.level = 1
        self.next_extra_life = EXTRA_LIFE_AT

        self.duck_x = START_COL * CELL_SU
        self.duck_row = START_ROW
        self.prev_duck_x = self.duck_x
        self.prev_duck_row = self.duck_row
        self.deepest_row = START_ROW
        self.life_ticks_left = LIFE_TICKS
        self.slots_filled = [False] * len(SLOT_COLS)
        self.hop_lock_until = 0

        self.slots_cleared = 0
        self.levels_cleared = 0
        self.hops = 0

        self.play_start_tick = -1
        self.end_tick = -1

        self.pending = []   # inputs queued but not yet reached
        self.inputs = []    # every input actually applied. This is the trace.

        self._build_lanes()

    def _build_lanes(self):
        pct = _tier_speed_pct(self.level)
        self.road_lanes = [
            _make_lane(row, _lane_dir(i), fdiv(ROAD_SPEED_SU[i] * pct, 100),
                       ROAD_ENTITY_HALF_W, ROAD_ENTITIES_PER_LANE, self._rng)
            for i, row in enumerate(ROAD_ROWS)
        ]
        self.river_lanes = [
            _make_lane(row, _lane_dir(i + 1), fdiv(RIVER_SPEED_SU[i] * pct, 100),
                       RIVER_ENTITY_HALF_W, RIVER_ENTITIES_PER_LANE, self._rng)
            for i, row in enumerate(RIVER_ROWS)
        ]

    def _lane_for_row(self, row):
        if row in ROAD_ROWS:
            return self.road_lanes[ROAD_ROWS.index(row)], "road"
        if row in RIVER_ROWS:
            return self.river_lanes[RIVER_ROWS.index(row)], "river"
        return None, None

    def _add_score(self, points):
        self.score = min(MAX_SCORE, self.score + points)
        if self.score >= self.next_extra_life and self.lives < MAX_LIVES:
            self.lives += 1
            self.next_extra_life += EXTRA_LIFE_EVERY

    def _respawn_duck(self):
        self.duck_x = START_COL * CELL_SU
        self.duck_row = START_ROW
        self.deepest_row = START_ROW
        self.life_ticks_left = LIFE_TICKS
        self.hop_lock_until = self.tick + HOP_TICKS

    def _kill_duck(self):
        self.lives -= 1
        self.state = S_DYING
        self.state_tick = 0

    def _resolve_goal_attempt(self):
        """Picks the nearest slot in range that is still open, not just the
        nearest slot -- a full house at the closest marker should not sink a
        run that could just as easily have landed at the one next door."""
        slot = -1
        best = None
        for i, c in enumerate(SLOT_COLS):
            if self.slots_filled[i]:
                continue
            dist = abs(self.duck_x - c * CELL_SU)
            if dist <= _px(SLOT_HALF_W) and (best is None or dist < best):
                best = dist
                slot = i
        if slot < 0:
            self._kill_duck()
            return
        self.slots_filled[slot] = True
        self.slots_cleared += 1
        self.duck_x = SLOT_COLS[slot] * CELL_SU
        self.duck_row = GOAL_ROW
        bonus = fdiv(self.life_ticks_left, TIME_BONUS_DIVISOR)
        self._add_score(SLOT_POINTS + bonus)

        if all(self.slots_filled):
            self._add_score(LEVEL_CLEAR_BONUS * self.level)
            self.levels_cleared += 1
            self.state = S_CLEAR
            self.state_tick = 0
        else:
            self._respawn_duck()

    def _apply_action(self, action):
        if self.state == S_READY:
            if self.state_tick < READY_TICKS:
                return
            self.state = S_PLAYING
            self.state_tick = 0
            self.play_start_tick = self.tick
        if self.state != S_PLAYING:
            return
        if self.tick < self.hop_lock_until:
            return

        self.hop_lock_until = self.tick + HOP_TICKS
        self.hops += 1

        # A raft carries the duck by a fraction of a cell every tick, so time
        # spent riding one leaves the duck's column off-grid by however much
        # it drifted. A hop snaps back to the nearest column before moving,
        # so drift only ever costs the ticks spent not hopping.
        self.duck_x = _snap_col(self.duck_x)

        if action == A_UP and self.duck_row == RIVER_TOP:
            self._resolve_goal_attempt()
            return
        if action == A_UP:
            self.duck_row = _clamp(self.duck_row - 1, RIVER_TOP, START_ROW)
        elif action == A_DOWN:
            self.duck_row = _clamp(self.duck_row + 1, RIVER_TOP, START_ROW)
        elif action == A_LEFT:
            self.duck_x = _clamp(self.duck_x - CELL_SU, 0, (COLS - 1) * CELL_SU)
        elif action == A_RIGHT:
            self.duck_x = _clamp(self.duck_x + CELL_SU, 0, (COLS - 1) * CELL_SU)

        if self.duck_row < self.deepest_row:
            self.deepest_row = self.duck_row
            self._add_score(ROW_POINTS)

    def _drain_input(self):
        i = 0
        while i < len(self.pending):
            code = self.pending[i]
            if fdiv(code, 4) > self.tick:
                i += 1
                continue
            self.pending.pop(i)
            self.inputs.append(self.tick * 4 + (code % 4))
            self._apply_action(code % 4)

    def _check_hazards(self):
        lane, kind = self._lane_for_row(self.duck_row)
        if lane is None:
            return  # median or start row: always safe

        if kind == "road":
            for e in lane.entities:
                if abs(self.duck_x - e.x) <= _px(DUCK_HALF_W) + _px(e.half_w):
                    self._kill_duck()
                    return
            return

        riding = None
        for e in lane.entities:
            if abs(self.duck_x - e.x) <= _px(e.half_w):
                riding = e
                break
        if riding is None:
            self._kill_duck()
            return
        self.duck_x += lane.dir * lane.speed_su
        if self.duck_x < 0 or self.duck_x > (COLS - 1) * CELL_SU:
            self._kill_duck()

    def step(self):
        if self.state == S_DEAD:
            return

        self.prev_duck_x = self.duck_x
        self.prev_duck_row = self.duck_row

        self._drain_input()
        for lane in self.road_lanes:
            _advance_lane(lane)
        for lane in self.river_lanes:
            _advance_lane(lane)

        if self.state == S_READY:
            self.state_tick += 1
        elif self.state == S_PLAYING:
            self._check_hazards()
            if self.state == S_PLAYING:
                self.life_ticks_left -= 1
                if self.life_ticks_left <= 0:
                    self._kill_duck()
        elif self.state == S_DYING:
            self.state_tick += 1
            if self.state_tick >= DYING_TICKS:
                if self.lives <= 0:
                    self.state = S_DEAD
                    self.end_tick = self.tick
                else:
                    self._respawn_duck()
                    self.state = S_PLAYING
                    self.state_tick = 0
        elif self.state == S_CLEAR:
            self.state_tick += 1
            if self.state_tick >= CLEAR_TICKS:
                self.level += 1
                self.slots_filled = [False] * len(SLOT_COLS)
                self._build_lanes()
                self._respawn_duck()
                self.state = S_PLAYING
                self.state_tick = 0

        self.tick += 1

    def duration_ms(self):
        if self.play_start_tick < 0:
            return 0
        end = self.end_tick if self.end_tick >= 0 else self.tick
        return round((end - self.play_start_tick) * STEP_MS)


def replay(seed, inputs, max_ticks=None):
    """Re-run a submitted trace and report what actually happened.

    The tail exists because a run does not end on its last input: a life can
    still time out, or a raft can still carry the duck off the edge, with
    nobody touching the controls.
    """
    sim = Sim(seed)
    sim.pending.extend(inputs)

    last = fdiv(inputs[-1], 4) if inputs else 0
    ceiling = max_ticks if max_ticks is not None else min(
        last + TAIL_TICKS, ABSOLUTE_MAX_TICKS)

    while sim.state != S_DEAD and sim.tick < ceiling:
        sim.step()
    return sim


# --------------------------------------------------------------------------- #
# Sessions
# --------------------------------------------------------------------------- #

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
    # Matches randomSeed() in ducker/rng.mjs, from a source worth trusting.
    seed = secrets.randbelow(0x7FFFFFFF) + 1
    _write(
        "INSERT INTO ducker_sessions (id, seed, issued_at, issued_ip) "
        "VALUES (?, ?, ?, ?)",
        (sid, seed, _now_ms(), ip),
    )
    return {"session": sid, "seed": seed}


def claim_session(sid, ip):
    """Look up a session for submission. Returns ``(row, error)``."""
    if not sid:
        return None, ("That run did not come with a session. Reload the page.", 400)
    rows = _rows("SELECT * FROM ducker_sessions WHERE id = ?", (sid,))
    if not rows:
        return None, ("That run's session is not one this server issued.", 400)
    row = rows[0]
    if row["consumed_at"] is not None:
        return None, ("That run has already been posted.", 409)
    if _now_ms() - int(row["issued_at"]) > SESSION_TTL_MS:
        return None, ("That run took too long to post. Start a fresh one.", 400)
    return row, None


def consume_session(sid, now_ms):
    _write("UPDATE ducker_sessions SET consumed_at = ? WHERE id = ?", (now_ms, sid))


def record_beat(sid, tick):
    """Note that a session was still being played, and how far it had got."""
    rows = _rows(
        "SELECT first_beat, beats, consumed_at FROM ducker_sessions WHERE id = ?",
        (sid,))
    if not rows or rows[0]["consumed_at"] is not None:
        return False
    now = _now_ms()
    if rows[0]["first_beat"] is None:
        _write(
            "UPDATE ducker_sessions SET first_beat = ?, first_tick = ?, "
            "last_beat = ?, last_tick = ?, beats = 1 WHERE id = ?",
            (now, tick, now, tick, sid),
        )
    else:
        _write(
            "UPDATE ducker_sessions SET last_beat = ?, last_tick = ?, "
            "beats = beats + 1 WHERE id = ?",
            (now, tick, sid),
        )
    return True


def sweep_sessions():
    """Drop sessions too old to be redeemed. Cheap, and keeps the table small."""
    _write("DELETE FROM ducker_sessions WHERE issued_at < ?",
           (_now_ms() - SESSION_TTL_MS * 2,))


# --------------------------------------------------------------------------- #
# Deciding whether a run happened
# --------------------------------------------------------------------------- #
#
# There is one input stream here, and every entry in it is hand timed: unlike
# a shooter, nothing about a hop's tick is dictated by a cooldown the player
# does not control, so -- unlike Patchaga -- there is no reason to separate a
# "steering" stream from a "fire" stream. The clock checks and the timing
# checks below both read the trace as it stands.

MODAL_SHARE_LIMIT = 0.35
MODAL_MIN_INTERVALS = 30

# A hand on a d-pad can sustain a few hops a second in a burst, but not six a
# second held for half a minute. Short bursts while dodging traffic are
# normal, so this only applies once a run is long enough that a burst cannot
# be the explanation.
MAX_HOPS_PER_SEC = 6.0
RATE_MIN_DURATION_MS = 20000

# Two inputs a fraction of a hop apart are one press as far as a hand is
# concerned. In practice this rarely fires: the hop lockout already spaces
# every applied input by HOP_TICKS, which is wider than the floor below. It
# stays here so a change to the lockout does not silently remove the check.
MIN_HUMAN_GAP_TICKS = 3
MAX_SHORT_GAPS = 15

FLAG_TEXT = {
    "faster_than_real_time": "That run finished sooner than it could have been played.",
    "not_enough_beats": "That run was not in contact while it was being played.",
    "beats_outran_clock": "That run reported progress faster than time passed.",
    "machine_timing": "That run's hops are too evenly spaced to be hand timed.",
    "hop_rate": "That run holds a hop rate a hand cannot keep up.",
    "double_inputs": "That run has inputs too close together to be separate presses.",
    "replay_mismatch": "That run does not replay to the score it was posted with.",
    "unreadable_trace": "That run's input trace could not be read.",
    "scored_without_playing": "That run scored without recording any input.",
    "no_trace": "That run's input trace is no longer stored.",
    "trace_trimmed": "That run's input trace was longer than the stored limit.",
}

CLOCK_FLAGS = ("faster_than_real_time", "not_enough_beats", "beats_outran_clock")


def interval_stats(inputs):
    """Shape of the gaps between hops, where a machine gives itself away."""
    ticks = [c // 4 for c in inputs]
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


def hand_flags(sim):
    """The checks that need only the trace, so a stored run can be re-judged."""
    flags = []
    stats = interval_stats(sim.inputs)
    duration = sim.duration_ms()

    if stats["intervals"] >= MODAL_MIN_INTERVALS \
            and stats["modal_share"] >= MODAL_SHARE_LIMIT:
        flags.append("machine_timing")

    if duration >= RATE_MIN_DURATION_MS:
        hops = len(sim.inputs)
        if hops / (duration / 1000.0) > MAX_HOPS_PER_SEC:
            flags.append("hop_rate")

    if stats["short"] > MAX_SHORT_GAPS:
        flags.append("double_inputs")

    return flags


def judge_run(sim, session, elapsed_ms, now_ms):
    """Returns the reasons a run should not count. Empty means it counts."""
    flags = []
    duration = sim.duration_ms()

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
    """Validate the submitted trace enough to replay it safely."""
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

AUDIT_LIMIT = 20000


def decode_trace(raw_trace):
    """Read a stored trace. Returns ``(codes, stored)``."""
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

    flags = hand_flags(sim)
    if trimmed and not flags:
        flags.append("trace_trimmed")
        return 1, flags

    return (0 if flags else 1), flags


def audit_pending_runs():
    """Judge every run that has not been judged yet. Safe to run twice."""
    pending = _rows(
        "SELECT id, score, seed, inputs FROM ducker_runs "
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
            "UPDATE ducker_runs SET verified = ?, flags = ? WHERE id = ?",
            (verified, json.dumps(flags), row["id"]),
        )

    log.info("ducker: audited %d runs, %d no longer count", len(pending), voided)
    return {"checked": len(pending), "voided": voided}


def clear_board(player=None):
    """Delete runs. Everything, or one player."""
    if player:
        key = name_key(player)
        if not key:
            return {"deleted": 0, "player": player}
        before = _rows("SELECT COUNT(*) AS n FROM ducker_runs WHERE player_key = ?",
                       (key,))[0]["n"]
        _write("DELETE FROM ducker_runs WHERE player_key = ?", (key,))
        log.info("ducker: cleared %d runs for %s", before, player)
        return {"deleted": before, "player": player}

    before = _rows("SELECT COUNT(*) AS n FROM ducker_runs")[0]["n"]
    _write("DELETE FROM ducker_runs")
    _write("DELETE FROM ducker_sessions")
    log.info("ducker: cleared the board, %d runs deleted", before)
    return {"deleted": before, "player": None}


# --------------------------------------------------------------------------- #
# Rate limiting
# --------------------------------------------------------------------------- #

MIN_SUBMIT_GAP_S = 3
GAP_FRACTION_OF_RUN = 0.75
MAX_RUNS_PER_DAY = 300
MAX_RUNS_PER_DAY_IP = 600
MAX_OPEN_SESSIONS = 30
SESSION_MINT_PER_HOUR = 400
TRACE_RETENTION_DAYS = 30

_last_prune = [""]


def _client_ip(request):
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
        "SELECT created_at FROM ducker_runs WHERE player_key = ? "
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
        "SELECT COUNT(*) AS n FROM ducker_runs "
        "WHERE player_key = ? AND created_at >= ? AND created_at < ?",
        (player_key, start, end))[0]["n"]
    if today >= MAX_RUNS_PER_DAY:
        return ("That is %d runs today. The board will still be here tomorrow."
                % today, 429)

    if ip:
        from_ip = _rows(
            "SELECT COUNT(*) AS n FROM ducker_runs "
            "WHERE session_id IN (SELECT id FROM ducker_sessions WHERE issued_ip = ?) "
            "AND created_at >= ? AND created_at < ?",
            (ip, start, end))[0]["n"]
        if from_ip >= MAX_RUNS_PER_DAY_IP:
            return ("That is a lot of runs from one place today.", 429)

    return None


def prune_traces():
    """Drop old input traces, keeping every score row."""
    today = _local_now().strftime("%Y-%m-%d")
    if _last_prune[0] == today:
        return 0
    _last_prune[0] = today
    cutoff = _utc_stamp(datetime.now(timezone.utc)
                        - timedelta(days=TRACE_RETENTION_DAYS))
    with _db_lock:
        cur = _conn.execute(
            "UPDATE ducker_runs SET inputs = NULL "
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
        "SELECT COALESCE(MAX(score), 0) AS best FROM ducker_runs "
        "WHERE player_key = ? AND verified = 1",
        (key,),
    )[0]["best"]

    _write(
        "INSERT INTO ducker_runs "
        "(player, player_key, score, seed, duration_ms, inputs, created_at, "
        " verified, session_id, elapsed_ms, input_count, level, slots, hops, flags) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (canonical, key, score, int(session["seed"]), duration,
         json.dumps(sim.inputs[:MAX_INPUT_TRACE]), _utc_stamp(), verified,
         session["id"], elapsed, len(sim.inputs), sim.level, sim.slots_cleared,
         sim.hops, json.dumps(flags)),
    )
    prune_traces()

    if not verified:
        return JSONResponse(status_code=202, content={
            "ok": True,
            "counted": False,
            "player": canonical,
            "score": score,
            "level": sim.level,
            "slots": sim.slots_cleared,
            "reason": FLAG_TEXT.get(flags[0], "That run could not be verified."),
        })

    rank = next((r["rank"] for r in board_rows("alltime") if r["player_key"] == key), None)
    return {
        "ok": True,
        "counted": True,
        "player": canonical,
        "score": score,
        "level": sim.level,
        "slots": sim.slots_cleared,
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
            "SELECT COUNT(*) AS n FROM ducker_sessions "
            "WHERE issued_ip = ? AND issued_at >= ?", (ip, cutoff))[0]["n"]
        if minted >= SESSION_MINT_PER_HOUR:
            return _reject("Starting runs a little fast. Try again shortly.", status=429)
        open_now = _rows(
            "SELECT COUNT(*) AS n FROM ducker_sessions "
            "WHERE issued_ip = ? AND consumed_at IS NULL AND issued_at >= ?",
            (ip, _now_ms() - SESSION_TTL_MS))[0]["n"]
        if open_now >= MAX_OPEN_SESSIONS:
            return _reject("Too many runs open at once. Finish one first.", status=429)

    sweep_sessions()
    return issue_session(ip)


@router.post("/api/beat")
def api_beat(payload: BeatIn):
    """Say that a run is still being played, and how far along it is."""
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
    ".mjs": "text/javascript; charset=utf-8",
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
