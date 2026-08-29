"""Patch Trail: a side game that shares the app and nothing else.

Isolation is the whole point of this module, so it is worth being explicit
about what that means:

* It never opens ``leaderboard.json`` or ``history.json`` at all, for reading or
  for writing. Players type their own name, so there is no roster to consult.
* It never touches a daily rank, penalty, streak, excused day, weekly total or
  win counter. Nothing in here knows those concepts exist.
* It adds no scheduled job. Seasons are derived from ``created_at`` when a board
  is queried, so there is no rollover to run and nothing to get out of step.
* Its tables are prefixed ``trail_`` and live in the SQLite file the app
  already keeps on the data volume, so a deploy stays "upload files, restart".

The rules of the game itself live in ``trail/sim.mjs`` and every tuning value
lives in ``trail/config.mjs``. That simulation is mirrored here, exactly, so
the server can replay a submitted run rather than take its word for the score.

On trusting submissions. A deterministic game is replayable, and a replayable
run is forgeable: anyone can import the client's own rules, search for a good
input trace offline, and hand over a trace that replays perfectly. Replay proves
a trace is self-consistent, never that a person produced it. So the seed is
issued by the server and spent once, the run is paced against the server's own
clock through heartbeats, and the trace is measured for input a human hand
cannot produce. Replay is the floor here, not the ceiling.

On matching the JavaScript exactly. Nothing in this simulation moves by less
than a whole grid cell, so positions are plain column/row integers rather
than sub-units -- the only timing question is how many ticks make up one
step, which is an integer by construction. The one place the two engines
have to agree on more than arithmetic is where the next patch spawns:
spawnPatch()'s retry-then-scan shape is ported line for line, because which
branch runs and how many draws it costs the generator has to match or the
stream desyncs. The generator itself is held here as an unsigned Python int
and masked after every operation, because JavaScript's bitwise operators
coerce to 32 bits.

None of the artwork or the names come from any existing arcade game. It is a
growing-trail game, which is a genre, built out of Patch My PC's own
material: a duck towing a lengthening chain of deployed patches.
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


router = APIRouter(prefix="/trail", tags=["trail"])

log = logging.getLogger("trail")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "trail")

BUFFER_DB = os.environ.get("ZS_BUFFER_DB", "/home/zipscores_buffer.db")
TIMEZONE = os.environ.get("ZS_TIMEZONE", "America/Chicago")

SEASON_HISTORY_LIMIT = 12
SEASON_SEARCH_LIMIT = 60
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
    when = when or datetime.now(timezone.utc)
    return when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_stamp(text):
    try:
        return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _day_bounds(local_day):
    tz = _tz()
    start = datetime(local_day.year, local_day.month, local_day.day, tzinfo=tz)
    return _utc_stamp(start), _utc_stamp(start + timedelta(days=1))


def _month_bounds(year, month):
    tz = _tz()
    start = datetime(year, month, 1, tzinfo=tz)
    end = datetime(year + (month == 12), 1 if month == 12 else month + 1, 1, tzinfo=tz)
    return _utc_stamp(start), _utc_stamp(end)


def _season_of(stamp):
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

_conn = sqlite3.connect(BUFFER_DB, check_same_thread=False, timeout=10.0)
_conn.row_factory = sqlite3.Row
_conn.execute(
    """
    CREATE TABLE IF NOT EXISTS trail_runs (
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
        patches     INTEGER,
        flags       TEXT
    )
    """
)

_conn.execute(
    """
    CREATE TABLE IF NOT EXISTS trail_sessions (
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

_conn.execute("CREATE INDEX IF NOT EXISTS idx_trail_created ON trail_runs(created_at)")
_conn.execute("CREATE INDEX IF NOT EXISTS idx_trail_player ON trail_runs(player_key)")
_conn.execute("CREATE INDEX IF NOT EXISTS idx_trail_score ON trail_runs(score DESC, id ASC)")
_conn.execute("CREATE INDEX IF NOT EXISTS idx_trail_sess ON trail_sessions(issued_at)")
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
        FROM trail_runs
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
    FROM trail_runs
    WHERE score > 0 AND verified = 1 %s
    GROUP BY player_key
    ORDER BY total DESC, last_at ASC, player_key ASC
"""


def _range_clause(bounds, column="created_at"):
    if not bounds:
        return "", ()
    return " AND %s >= ? AND %s < ?" % (column, column), tuple(bounds)


def _display_names():
    rows = _rows(
        "SELECT player_key, player FROM trail_runs "
        "WHERE id IN (SELECT MAX(id) FROM trail_runs "
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
    first = _rows("SELECT MIN(created_at) AS m FROM trail_runs "
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
        "COALESCE(MAX(score), 0) AS best, COALESCE(SUM(patches), 0) AS patches, "
        "COALESCE(MAX(level), 0) AS furthest FROM trail_runs "
        "WHERE player_key = ? AND verified = 1",
        (key,),
    )[0]

    def best_in(bounds):
        clause, params = _range_clause(bounds)
        sql = ("SELECT COALESCE(MAX(score), 0) AS best FROM trail_runs "
               "WHERE player_key = ? AND verified = 1" + clause)
        return _rows(sql, (key,) + params)[0]["best"]

    ranks = {}
    for view in VIEWS:
        ranks[view] = next(
            (r["rank"] for r in board_rows(view) if r["player_key"] == key), None)

    recent = _rows(
        "SELECT score, duration_ms, level, patches, created_at "
        "FROM trail_runs "
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
        "furthest_level": totals["furthest"],
        "best_season": best_in(_bounds_for("season")),
        "best_today": best_in(_bounds_for("today")),
        "ranks": ranks,
        "recent": recent,
    }


# --------------------------------------------------------------------------- #
# The simulation, ported from trail/sim.mjs
# --------------------------------------------------------------------------- #

def _u32(x):
    return x & 0xFFFFFFFF


def _make_rng(seed):
    """mulberry32, matching trail/rng.mjs."""
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


COLS = 24
ROWS = 16

MOVE_TICKS_BASE = 14
MIN_MOVE_TICKS = 5
START_LENGTH = 4

READY_TICKS = 60
DYING_TICKS = 45
CLEAR_TICKS = 100

POINTS_PER_PATCH = 20
PATCHES_TO_LEVEL_UP = 8
LEVEL_CLEAR_BONUS = 200
EXTRA_LIFE_AT = 500
EXTRA_LIFE_EVERY = 1000
MAX_LIVES = 6
LIVES = 4

LEVEL_SPEED_PCT = (100, 115, 130, 145, 160, 175, 190)

STEP_MS = 1000.0 / 120.0
MAX_INPUT_TRACE = 8000
ABSOLUTE_MAX_TICKS = 120 * 60 * 12
TAIL_TICKS = 120 * 20
MAX_SCORE = 10000000
PATCH_SPAWN_RETRIES = 60

S_READY, S_PLAYING, S_DYING, S_CLEAR, S_DEAD = (
    "ready", "playing", "dying", "clear", "dead")

A_UP, A_DOWN, A_LEFT, A_RIGHT = 0, 1, 2, 3
DX = (0, 0, -1, 1)
DY = (-1, 1, 0, 0)
OPPOSITE = (1, 0, 3, 2)


def _tier_speed_pct(level):
    i = min(max(level - 1, 0), len(LEVEL_SPEED_PCT) - 1)
    return LEVEL_SPEED_PCT[i]


def _move_ticks(level):
    return max(MIN_MOVE_TICKS, fdiv(MOVE_TICKS_BASE * 100, _tier_speed_pct(level)))


def _occupied(segments, col, row, limit=None):
    n = len(segments) if limit is None else limit
    for i in range(n):
        if segments[i][0] == col and segments[i][1] == row:
            return True
    return False


def _start_segments():
    head_col = fdiv(COLS, 2)
    head_row = fdiv(ROWS, 2)
    return [(head_col - i, head_row) for i in range(START_LENGTH)]


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

        self.segments = _start_segments()
        self.dir = A_RIGHT
        self.pending_dir = A_RIGHT
        self.move_timer = _move_ticks(1)
        self.patch = (-1, -1)

        self.patches_this_level = 0
        self.patches_eaten = 0
        self.levels_cleared = 0

        self.play_start_tick = -1
        self.end_tick = -1

        self.pending = []
        self.inputs = []

        self._spawn_patch()

    def _spawn_patch(self):
        for _ in range(PATCH_SPAWN_RETRIES):
            col = _rng_int(self._rng, COLS)
            row = _rng_int(self._rng, ROWS)
            if not _occupied(self.segments, col, row):
                self.patch = (col, row)
                return
        empties = []
        for r in range(ROWS):
            for c in range(COLS):
                if not _occupied(self.segments, c, r):
                    empties.append((c, r))
        if not empties:
            self.patch = (-1, -1)
            return
        self.patch = empties[_rng_int(self._rng, len(empties))]

    def _add_score(self, points):
        self.score = min(MAX_SCORE, self.score + points)
        if self.score >= self.next_extra_life and self.lives < MAX_LIVES:
            self.lives += 1
            self.next_extra_life += EXTRA_LIFE_EVERY

    def _kill_trail(self):
        self.lives -= 1
        self.state = S_DYING
        self.state_tick = 0

    def _respawn(self):
        self.segments = _start_segments()
        self.dir = A_RIGHT
        self.pending_dir = A_RIGHT
        self.move_timer = _move_ticks(self.level)
        self._spawn_patch()

    def _apply_action(self, action):
        if self.state == S_READY:
            if self.state_tick < READY_TICKS:
                return
            self.state = S_PLAYING
            self.state_tick = 0
            self.play_start_tick = self.tick
        if self.state != S_PLAYING:
            return
        if action == OPPOSITE[self.dir]:
            return
        self.pending_dir = action

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

    def _step_move(self):
        self.dir = self.pending_dir
        head = self.segments[0]
        new_head = (head[0] + DX[self.dir], head[1] + DY[self.dir])

        if new_head[0] < 0 or new_head[0] >= COLS or new_head[1] < 0 or new_head[1] >= ROWS:
            self._kill_trail()
            return

        ate = new_head == self.patch
        body_limit = len(self.segments) if ate else len(self.segments) - 1
        if _occupied(self.segments, new_head[0], new_head[1], body_limit):
            self._kill_trail()
            return

        self.segments.insert(0, new_head)
        if ate:
            self.patches_this_level += 1
            self.patches_eaten += 1
            self._add_score(POINTS_PER_PATCH)
            self._spawn_patch()
            if self.patches_this_level >= PATCHES_TO_LEVEL_UP:
                self._add_score(LEVEL_CLEAR_BONUS * self.level)
                self.levels_cleared += 1
                self.state = S_CLEAR
                self.state_tick = 0
        else:
            self.segments.pop()

    def step(self):
        if self.state == S_DEAD:
            return

        self._drain_input()

        if self.state == S_READY:
            self.state_tick += 1
        elif self.state == S_PLAYING:
            if self.move_timer > 0:
                self.move_timer -= 1
            else:
                self._step_move()
                if self.state == S_PLAYING:
                    self.move_timer = _move_ticks(self.level)
        elif self.state == S_DYING:
            self.state_tick += 1
            if self.state_tick >= DYING_TICKS:
                if self.lives <= 0:
                    self.state = S_DEAD
                    self.end_tick = self.tick
                else:
                    self._respawn()
                    self.state = S_PLAYING
                    self.state_tick = 0
        elif self.state == S_CLEAR:
            self.state_tick += 1
            if self.state_tick >= CLEAR_TICKS:
                self.level += 1
                self.patches_this_level = 0
                self.move_timer = _move_ticks(self.level)
                self.state = S_PLAYING
                self.state_tick = 0

        self.tick += 1

    def duration_ms(self):
        if self.play_start_tick < 0:
            return 0
        end = self.end_tick if self.end_tick >= 0 else self.tick
        return round((end - self.play_start_tick) * STEP_MS)


def replay(seed, inputs, max_ticks=None):
    """Re-run a submitted trace and report what actually happened."""
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

SESSION_TTL_MS = 45 * 60 * 1000
CLOCK_SLACK_MS = 3000
BEAT_INTERVAL_MS = 5000
MIN_BEAT_RATIO = 0.4
BEAT_FLOOR = 2


def _now_ms():
    return int(time.time() * 1000)


def issue_session(ip):
    sid = secrets.token_hex(16)
    seed = secrets.randbelow(0x7FFFFFFF) + 1
    _write(
        "INSERT INTO trail_sessions (id, seed, issued_at, issued_ip) "
        "VALUES (?, ?, ?, ?)",
        (sid, seed, _now_ms(), ip),
    )
    return {"session": sid, "seed": seed}


def claim_session(sid, ip):
    if not sid:
        return None, ("That run did not come with a session. Reload the page.", 400)
    rows = _rows("SELECT * FROM trail_sessions WHERE id = ?", (sid,))
    if not rows:
        return None, ("That run's session is not one this server issued.", 400)
    row = rows[0]
    if row["consumed_at"] is not None:
        return None, ("That run has already been posted.", 409)
    if _now_ms() - int(row["issued_at"]) > SESSION_TTL_MS:
        return None, ("That run took too long to post. Start a fresh one.", 400)
    return row, None


def consume_session(sid, now_ms):
    _write("UPDATE trail_sessions SET consumed_at = ? WHERE id = ?", (now_ms, sid))


def record_beat(sid, tick):
    rows = _rows(
        "SELECT first_beat, beats, consumed_at FROM trail_sessions WHERE id = ?",
        (sid,))
    if not rows or rows[0]["consumed_at"] is not None:
        return False
    now = _now_ms()
    if rows[0]["first_beat"] is None:
        _write(
            "UPDATE trail_sessions SET first_beat = ?, first_tick = ?, "
            "last_beat = ?, last_tick = ?, beats = 1 WHERE id = ?",
            (now, tick, now, tick, sid),
        )
    else:
        _write(
            "UPDATE trail_sessions SET last_beat = ?, last_tick = ?, "
            "beats = beats + 1 WHERE id = ?",
            (now, tick, sid),
        )
    return True


def sweep_sessions():
    _write("DELETE FROM trail_sessions WHERE issued_at < ?",
           (_now_ms() - SESSION_TTL_MS * 2,))


# --------------------------------------------------------------------------- #
# Deciding whether a run happened
# --------------------------------------------------------------------------- #
#
# One input stream -- a turn -- and every entry in it is hand timed.

MODAL_SHARE_LIMIT = 0.35
MODAL_MIN_INTERVALS = 30

# A hand steering a trail turns less often than one dodging live traffic, so
# this is close to Ducker's equivalent: a handful of turns a second in a
# tight corner is normal, six a second sustained for half a minute is not.
MAX_TURNS_PER_SEC = 5.0
RATE_MIN_DURATION_MS = 20000

MIN_HUMAN_GAP_TICKS = 2
MAX_SHORT_GAPS = 20

FLAG_TEXT = {
    "faster_than_real_time": "That run finished sooner than it could have been played.",
    "not_enough_beats": "That run was not in contact while it was being played.",
    "beats_outran_clock": "That run reported progress faster than time passed.",
    "machine_timing": "That run's turns are too evenly spaced to be hand timed.",
    "turn_rate": "That run holds a turn rate a hand cannot keep up.",
    "double_inputs": "That run has inputs too close together to be separate presses.",
    "replay_mismatch": "That run does not replay to the score it was posted with.",
    "unreadable_trace": "That run's input trace could not be read.",
    "scored_without_playing": "That run scored without recording any input.",
    "no_trace": "That run's input trace is no longer stored.",
    "trace_trimmed": "That run's input trace was longer than the stored limit.",
}

CLOCK_FLAGS = ("faster_than_real_time", "not_enough_beats", "beats_outran_clock")


def interval_stats(inputs):
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
    flags = []
    stats = interval_stats(sim.inputs)
    duration = sim.duration_ms()

    if stats["intervals"] >= MODAL_MIN_INTERVALS \
            and stats["modal_share"] >= MODAL_SHARE_LIMIT:
        flags.append("machine_timing")

    if duration >= RATE_MIN_DURATION_MS:
        turns = len(sim.inputs)
        if turns / (duration / 1000.0) > MAX_TURNS_PER_SEC:
            flags.append("turn_rate")

    if stats["short"] > MAX_SHORT_GAPS:
        flags.append("double_inputs")

    return flags


def judge_run(sim, session, elapsed_ms, now_ms):
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
    pending = _rows(
        "SELECT id, score, seed, inputs FROM trail_runs "
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
            "UPDATE trail_runs SET verified = ?, flags = ? WHERE id = ?",
            (verified, json.dumps(flags), row["id"]),
        )

    log.info("trail: audited %d runs, %d no longer count", len(pending), voided)
    return {"checked": len(pending), "voided": voided}


def clear_board(player=None):
    if player:
        key = name_key(player)
        if not key:
            return {"deleted": 0, "player": player}
        before = _rows("SELECT COUNT(*) AS n FROM trail_runs WHERE player_key = ?",
                       (key,))[0]["n"]
        _write("DELETE FROM trail_runs WHERE player_key = ?", (key,))
        log.info("trail: cleared %d runs for %s", before, player)
        return {"deleted": before, "player": player}

    before = _rows("SELECT COUNT(*) AS n FROM trail_runs")[0]["n"]
    _write("DELETE FROM trail_runs")
    _write("DELETE FROM trail_sessions")
    log.info("trail: cleared the board, %d runs deleted", before)
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
    now = _utc_stamp()
    recent = _rows(
        "SELECT created_at FROM trail_runs WHERE player_key = ? "
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
        "SELECT COUNT(*) AS n FROM trail_runs "
        "WHERE player_key = ? AND created_at >= ? AND created_at < ?",
        (player_key, start, end))[0]["n"]
    if today >= MAX_RUNS_PER_DAY:
        return ("That is %d runs today. The board will still be here tomorrow."
                % today, 429)

    if ip:
        from_ip = _rows(
            "SELECT COUNT(*) AS n FROM trail_runs "
            "WHERE session_id IN (SELECT id FROM trail_sessions WHERE issued_ip = ?) "
            "AND created_at >= ? AND created_at < ?",
            (ip, start, end))[0]["n"]
        if from_ip >= MAX_RUNS_PER_DAY_IP:
            return ("That is a lot of runs from one place today.", 429)

    return None


def prune_traces():
    today = _local_now().strftime("%Y-%m-%d")
    if _last_prune[0] == today:
        return 0
    _last_prune[0] = today
    cutoff = _utc_stamp(datetime.now(timezone.utc)
                        - timedelta(days=TRACE_RETENTION_DAYS))
    with _db_lock:
        cur = _conn.execute(
            "UPDATE trail_runs SET inputs = NULL "
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
        "SELECT COALESCE(MAX(score), 0) AS best FROM trail_runs "
        "WHERE player_key = ? AND verified = 1",
        (key,),
    )[0]["best"]

    _write(
        "INSERT INTO trail_runs "
        "(player, player_key, score, seed, duration_ms, inputs, created_at, "
        " verified, session_id, elapsed_ms, input_count, level, patches, flags) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (canonical, key, score, int(session["seed"]), duration,
         json.dumps(sim.inputs[:MAX_INPUT_TRACE]), _utc_stamp(), verified,
         session["id"], elapsed, len(sim.inputs), sim.level, sim.patches_eaten,
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
            "patches": sim.patches_eaten,
            "reason": FLAG_TEXT.get(flags[0], "That run could not be verified."),
        })

    rank = next((r["rank"] for r in board_rows("alltime") if r["player_key"] == key), None)
    return {
        "ok": True,
        "counted": True,
        "player": canonical,
        "score": score,
        "level": sim.level,
        "patches": sim.patches_eaten,
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
    ip = _client_ip(request)
    if ip:
        cutoff = _now_ms() - 60 * 60 * 1000
        minted = _rows(
            "SELECT COUNT(*) AS n FROM trail_sessions "
            "WHERE issued_ip = ? AND issued_at >= ?", (ip, cutoff))[0]["n"]
        if minted >= SESSION_MINT_PER_HOUR:
            return _reject("Starting runs a little fast. Try again shortly.", status=429)
        open_now = _rows(
            "SELECT COUNT(*) AS n FROM trail_sessions "
            "WHERE issued_ip = ? AND consumed_at IS NULL AND issued_at >= ?",
            (ip, _now_ms() - SESSION_TTL_MS))[0]["n"]
        if open_now >= MAX_OPEN_SESSIONS:
            return _reject("Too many runs open at once. Finish one first.", status=429)

    sweep_sessions()
    return issue_session(ip)


@router.post("/api/beat")
def api_beat(payload: BeatIn):
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
