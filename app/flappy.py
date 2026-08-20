"""Flappy Duck: a side game that shares the app and nothing else.

Isolation is the whole point of this module, so it is worth being explicit
about what that means:

* It never opens ``leaderboard.json`` or ``history.json`` at all, for reading or
  for writing. Players type their own name, so there is no roster to consult.
* It never touches a daily rank, penalty, streak, excused day, weekly total or
  win counter. Nothing in here knows those concepts exist.
* It adds no scheduled job. Seasons are derived from ``created_at`` when a board
  is queried, so there is no rollover to run and nothing to get out of step.
* Its tables are prefixed ``flappy_`` and live in the SQLite file the app
  already keeps on the data volume, so a deploy stays "upload files, restart".

The rules of the game itself live in ``flappy/sim.mjs`` and every tuning value
lives in ``flappy/config.mjs``. That simulation is mirrored here, exactly, so
the server can replay a submitted run rather than take its word for the score.
``tools/check_sim_parity.py`` fails the moment the two engines disagree.

On trusting submissions. A deterministic game is replayable, and a replayable
run is forgeable: anyone can import the client's own physics, search for a good
input trace offline in milliseconds, and hand over a trace that replays
perfectly. Replay proves a trace is self-consistent, never that a person
produced it. So the seed is issued by the server and spent once, the run is
paced against the server's own clock through heartbeats, and the trace is
measured for input a human hand cannot produce. Replay is the floor here, not
the ceiling.
"""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
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


router = APIRouter(prefix="/flappy", tags=["flappy"])

log = logging.getLogger("flappy")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "flappy")

# Read-only, and only the buffer database. This module deliberately does not
# open the real leaderboard state file, because names here are free text.
BUFFER_DB = os.environ.get("ZS_BUFFER_DB", "/home/zipscores_buffer.db")
TIMEZONE = os.environ.get("ZS_TIMEZONE", "America/Chicago")

# Mirrors of flappy/config.mjs. The server replays every submitted run, so it
# needs the whole simulation, not just the few numbers a sanity check wanted.
# tools/check_sim_parity.py fails if these ever drift from the JS.
WIDTH = 288.0
GROUND_Y = 448.0
STEP_MS = 1000.0 / 120.0
SIM_DT = STEP_MS / 1000.0

DUCK_X = 62.0
DUCK_W = 34.0
DUCK_H = 24.0
HIT_W = 30.0
HIT_H = 20.0
START_Y = 214.0
GRAVITY = 1200.0
FLAP_IMPULSE = -350.0
TERMINAL_FALL = 400.0
CEILING_Y = -12.0

SCROLL_SPEED = 110.0
GAP_HEIGHT = 100.0
SPACING = 160.0
FIRST_OBSTACLE_X = 340.0
GAP_CENTER_MIN = 110
GAP_CENTER_MAX = 370
GAP_CENTER_MAX_DELTA = 130
TILE_W = 52.0
CAP_W = 60.0

MAX_FLAP_TRACE = 5000
MAX_SCORE = 100000
MAX_DURATION_MS = 6 * 60 * 60 * 1000

SEASON_HISTORY_LIMIT = 12  # past seasons kept in the hall of fame
SEASON_SEARCH_LIMIT = 60   # how far back to look for them, in months
BOARD_LIMIT = 10

VIEWS = ("alltime", "season", "today", "volume")
VIEW_TITLES = {
    "alltime": "All time",
    "season": "This season",
    "today": "Today",
    "volume": "Most deployed",
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
    CREATE TABLE IF NOT EXISTS flappy_runs (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        player      TEXT    NOT NULL,
        player_key  TEXT    NOT NULL,
        score       INTEGER NOT NULL,
        seed        INTEGER NOT NULL,
        duration_ms INTEGER NOT NULL,
        flaps       TEXT,
        created_at  TEXT    NOT NULL,
        verified    INTEGER NOT NULL DEFAULT 0
    )
    """
)

# A run is now issued before it is played, so the server knows which world the
# player was given and when the clock started. One row per attempt, spent once.
_conn.execute(
    """
    CREATE TABLE IF NOT EXISTS flappy_sessions (
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

# Added after the first release. Existing databases get them here rather than
# through a migration step, because the deploy is "upload files, restart" and
# there is nowhere for a migration step to run.
_EXTRA_RUN_COLUMNS = (
    ("session_id", "TEXT"),
    ("elapsed_ms", "INTEGER"),
    ("flap_count", "INTEGER"),
    ("flags", "TEXT"),
)
_have = {r["name"] for r in _conn.execute("PRAGMA table_info(flappy_runs)")}
for _name, _type in _EXTRA_RUN_COLUMNS:
    if _name not in _have:
        _conn.execute("ALTER TABLE flappy_runs ADD COLUMN %s %s" % (_name, _type))

_conn.execute("CREATE INDEX IF NOT EXISTS idx_flappy_created ON flappy_runs(created_at)")
_conn.execute("CREATE INDEX IF NOT EXISTS idx_flappy_player ON flappy_runs(player_key)")
_conn.execute("CREATE INDEX IF NOT EXISTS idx_flappy_score ON flappy_runs(score DESC, id ASC)")
_conn.execute("CREATE INDEX IF NOT EXISTS idx_flappy_sess ON flappy_sessions(issued_at)")
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
               ROW_NUMBER() OVER (
                   PARTITION BY player_key
                   ORDER BY score DESC, created_at ASC, id ASC
               ) AS rn
        FROM flappy_runs
        WHERE score > 0 AND verified = 1 %s
    )
    SELECT id, player, player_key, score, seed, duration_ms, created_at
    FROM ranked
    WHERE rn = 1
    ORDER BY score DESC, created_at ASC, id ASC
"""

# Cumulative patches. The tiebreak is the earliest *last* run, because the
# player who got to the total first is the one who stopped needing runs first.
_VOLUME = """
    SELECT player_key,
           SUM(score)      AS total,
           COUNT(*)        AS runs,
           MAX(score)      AS best,
           MAX(created_at) AS last_at
    FROM flappy_runs
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
        "SELECT player_key, player FROM flappy_runs "
        "WHERE id IN (SELECT MAX(id) FROM flappy_runs "
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
    first = _rows("SELECT MIN(created_at) AS m FROM flappy_runs "
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
        "COALESCE(MAX(score), 0) AS best FROM flappy_runs "
        "WHERE player_key = ? AND verified = 1",
        (key,),
    )[0]

    def best_in(bounds):
        clause, params = _range_clause(bounds)
        sql = ("SELECT COALESCE(MAX(score), 0) AS best FROM flappy_runs "
               "WHERE player_key = ? AND verified = 1" + clause)
        return _rows(sql, (key,) + params)[0]["best"]

    ranks = {}
    for view in VIEWS:
        ranks[view] = next(
            (r["rank"] for r in board_rows(view) if r["player_key"] == key), None)

    recent = _rows(
        "SELECT score, duration_ms, created_at FROM flappy_runs "
        "WHERE player_key = ? AND verified = 1 ORDER BY id DESC LIMIT 10",
        (key,),
    )

    return {
        "ok": True,
        "player": stored or canonical,
        "runs": totals["runs"],
        "total": totals["total"],
        "best": totals["best"],
        "best_season": best_in(_bounds_for("season")),
        "best_today": best_in(_bounds_for("today")),
        "ranks": ranks,
        "recent": recent,
    }


# --------------------------------------------------------------------------- #
# The simulation, ported from flappy/sim.mjs
# --------------------------------------------------------------------------- #
#
# This is the same game the browser runs, in Python, tick for tick. It exists so
# that a submitted score is something the server works out rather than something
# the client asserts. Everything below is a direct translation; if it needs to
# change, change flappy/sim.mjs first and keep the two in step.
#
# On matching JavaScript exactly. Both languages do arithmetic on IEEE 754
# doubles and both round the same way, so +, -, * and / agree bit for bit.
# math.floor and math.ceil match Math.floor and Math.ceil. The generator is the
# only real hazard, because JS bitwise operators coerce to 32 bits, so its state
# is held here as an unsigned Python int and masked after every operation.

READY, PLAYING, DYING, DEAD = "ready", "playing", "dying", "dead"
OBSTACLE, GROUND = "obstacle", "ground"

# A run cannot outlive the last flap by much: with nothing holding it up the
# duck reaches the ground from the ceiling in about 160 ticks. The margin is
# generous, and the absolute cap keeps a hostile trace from costing real CPU.
FALL_TICKS = 400
ABSOLUTE_MAX_TICKS = 120 * 60 * 10  # ten minutes of play


def _u32(x):
    return x & 0xFFFFFFFF


def _make_rng(seed):
    """mulberry32, matching flappy/rng.mjs.

    Every step in the JS is a 32-bit bit pattern, and whether that pattern is
    read as signed or unsigned never changes the bits themselves, only how they
    print. Holding the state unsigned and masking after each operation is
    therefore the same generator, not an approximation of it.
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


def _overlaps(bx, by, bw, bh, x, y, w, h):
    return bx < x + w and bx + bw > x and by < y + h and by + bh > y


class Sim:
    """One run. Advance it with step() and nothing else."""

    def __init__(self, seed):
        self.seed = _u32(seed)
        self._rng = _make_rng(seed)
        self.gaps = []
        self.tick = 0
        self.state = READY
        self.duck_y = START_Y
        self.duck_vy = 0.0
        self.scroll_ticks = 0
        self.scroll_x = 0.0
        self.score = 0
        self.next_score_index = 0
        self.play_start_tick = -1
        self.death_tick = -1
        self.cause = None
        self.pending = []
        self.flaps = []
        # Read-only telemetry for the plausibility checks. Recorded from values
        # the physics has already produced, so it can never feed back into them.
        self.flap_marks = []

    def gap_center(self, index):
        while len(self.gaps) <= index:
            lo = GAP_CENTER_MIN
            hi = GAP_CENTER_MAX
            if self.gaps:
                prev = self.gaps[-1]
                lo = max(lo, prev - GAP_CENTER_MAX_DELTA)
                hi = min(hi, prev + GAP_CENTER_MAX_DELTA)
            self.gaps.append(lo + math.floor(self._rng() * (hi - lo + 1)))
        return self.gaps[index]

    def obstacle_screen_x(self, index):
        return FIRST_OBSTACLE_X + index * SPACING - self.scroll_x

    def queue_flap(self, at_tick):
        self.pending.append(max(self.tick, math.floor(at_tick)))
        self.pending.sort()

    def _apply_flap(self):
        self.duck_vy = FLAP_IMPULSE
        self.flaps.append(self.tick)
        self.flap_marks.append((self.tick, self.duck_y))

    def _die(self, cause):
        self.state = DYING
        self.death_tick = self.tick
        self.cause = cause
        if cause == OBSTACLE:
            self.duck_vy = min(self.duck_vy, -120.0)

    def step(self):
        if self.state == DEAD:
            return

        flapped = False
        while self.pending and self.pending[0] <= self.tick:
            self.pending.pop(0)
            if self.state == READY:
                self.state = PLAYING
                self.play_start_tick = self.tick
                self._apply_flap()
                flapped = True
            elif self.state == PLAYING and not flapped:
                self._apply_flap()
                flapped = True

        if self.state == READY:
            self.tick += 1
            return

        self.duck_vy = min(self.duck_vy + GRAVITY * SIM_DT, TERMINAL_FALL)
        self.duck_y += self.duck_vy * SIM_DT

        if self.duck_y < CEILING_Y:
            self.duck_y = CEILING_Y
            if self.duck_vy < 0:
                self.duck_vy = 0.0

        if self.state == PLAYING:
            self.scroll_ticks += 1
            self.scroll_x = self.scroll_ticks * SIM_DT * SCROLL_SPEED

            duck_center = DUCK_X + DUCK_W / 2
            while self.obstacle_screen_x(self.next_score_index) + TILE_W / 2 <= duck_center:
                self.next_score_index += 1
                self.score += 1

            box_x = DUCK_X + (DUCK_W - HIT_W) / 2
            box_y = self.duck_y + (DUCK_H - HIT_H) / 2
            base = FIRST_OBSTACLE_X - self.scroll_x
            first = max(0, math.ceil((-CAP_W - base) / SPACING))
            last = math.floor((WIDTH + CAP_W - base) / SPACING)
            for i in range(first, last + 1):
                center = self.gap_center(i)
                ox = FIRST_OBSTACLE_X + i * SPACING - self.scroll_x
                if ox > box_x + HIT_W or ox + TILE_W < box_x:
                    continue
                gap_top = center - GAP_HEIGHT / 2
                gap_bottom = center + GAP_HEIGHT / 2
                hit_top = _overlaps(box_x, box_y, HIT_W, HIT_H,
                                    ox, -200.0, TILE_W, gap_top + 200)
                hit_bottom = _overlaps(box_x, box_y, HIT_W, HIT_H,
                                       ox, gap_bottom, TILE_W, GROUND_Y - gap_bottom)
                if hit_top or hit_bottom:
                    self._die(OBSTACLE)
                    break

        box_y = self.duck_y + (DUCK_H - HIT_H) / 2
        if box_y + HIT_H >= GROUND_Y:
            self.duck_y = GROUND_Y - DUCK_H + (DUCK_H - HIT_H) / 2
            self.duck_vy = 0.0
            if self.state == PLAYING:
                self._die(GROUND)
                self.state = DEAD
            elif self.state == DYING:
                self.state = DEAD

        self.tick += 1

    def duration_ms(self):
        if self.play_start_tick < 0:
            return 0
        end = self.death_tick if self.death_tick >= 0 else self.tick
        return int(round((end - self.play_start_tick) * STEP_MS))


def replay(seed, flaps):
    """Run a recorded trace and return the sim it produced.

    The tick budget comes from the trace itself, because a run ends shortly
    after its final flap no matter what the submission claims.
    """
    last = flaps[-1] if flaps else 0
    max_ticks = min(last, ABSOLUTE_MAX_TICKS) + FALL_TICKS

    sim = Sim(seed)
    nxt = 0
    total = len(flaps)
    while sim.state != DEAD and sim.tick < max_ticks:
        while nxt < total and flaps[nxt] <= sim.tick:
            sim.queue_flap(flaps[nxt])
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
    # Matches randomSeed() in flappy/rng.mjs, from a source worth trusting.
    seed = secrets.randbelow(0x7FFFFFFF) + 1
    _write(
        "INSERT INTO flappy_sessions (id, seed, issued_at, issued_ip) VALUES (?, ?, ?, ?)",
        (sid, seed, _now_ms(), ip),
    )
    return {"session": sid, "seed": seed}


def claim_session(sid, ip):
    """Look up a session for submission. Returns ``(row, error)``."""
    if not sid:
        return None, ("That run did not come with a session. Reload the page.", 400)
    rows = _rows("SELECT * FROM flappy_sessions WHERE id = ?", (sid,))
    if not rows:
        return None, ("That run's session is not one this server issued.", 400)
    row = rows[0]
    if row["consumed_at"] is not None:
        return None, ("That run has already been posted.", 409)
    if _now_ms() - int(row["issued_at"]) > SESSION_TTL_MS:
        return None, ("That run took too long to post. Start a fresh one.", 400)
    return row, None


def consume_session(sid, now_ms):
    _write("UPDATE flappy_sessions SET consumed_at = ? WHERE id = ?", (now_ms, sid))


def record_beat(sid, tick):
    """Note that a session was still being played, and how far it had got.

    The tick matters as much as the timestamp. Comparing how far the simulation
    advanced against how much real time passed is what catches a run that was
    computed rather than played.
    """
    rows = _rows("SELECT first_beat, beats, consumed_at FROM flappy_sessions WHERE id = ?",
                 (sid,))
    if not rows or rows[0]["consumed_at"] is not None:
        return False
    now = _now_ms()
    if rows[0]["first_beat"] is None:
        _write(
            "UPDATE flappy_sessions SET first_beat = ?, first_tick = ?, "
            "last_beat = ?, last_tick = ?, beats = 1 WHERE id = ?",
            (now, tick, now, tick, sid),
        )
    else:
        _write(
            "UPDATE flappy_sessions SET last_beat = ?, last_tick = ?, "
            "beats = beats + 1 WHERE id = ?",
            (now, tick, sid),
        )
    return True


def sweep_sessions():
    """Drop sessions too old to be redeemed. Cheap, and keeps the table small."""
    _write("DELETE FROM flappy_sessions WHERE issued_at < ?",
           (_now_ms() - SESSION_TTL_MS * 2,))


# --------------------------------------------------------------------------- #
# Submission
# --------------------------------------------------------------------------- #

def min_duration_ms(score):
    """The fastest a run can legitimately reach a score.

    The replay makes this redundant as a gate, but it is still the clearest way
    to explain a rejection to a player, and ``tools/check_flappy_api.py`` uses
    it to confirm the constants here still match ``flappy/config.mjs``.
    """
    if score <= 0:
        return 0
    distance = (FIRST_OBSTACLE_X + (score - 1) * SPACING
                + TILE_W / 2 - (DUCK_X + DUCK_W / 2))
    return int(distance / SCROLL_SPEED * 1000)


class ScoreIn(BaseModel):
    session: str = Field(default="", max_length=64)
    player: str = Field(default="", max_length=120)
    score: int = 0
    duration_ms: int = 0
    flaps: list[int] = Field(default_factory=list)


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
    flaps, trace_error = read_trace(payload.flaps)
    if trace_error:
        return _reject(trace_error)

    session, session_error = claim_session(payload.session, ip)
    if session_error:
        return _reject(session_error[0], status=session_error[1])

    sim = replay(session["seed"], flaps)
    score = sim.score
    duration = sim.duration_ms()

    if score <= 0:
        return _reject("A run of zero is not posted to the board.")
    if payload.score != score:
        # Either the client is lying or the two engines have drifted apart.
        # Both are worth refusing rather than quietly recording the truth.
        return _reject("That run replays to %d patches, not %d." % (score, payload.score))

    guard = check_rate(key, ip, duration)
    if guard:
        return _reject(guard[0], status=guard[1])

    now_ms = _now_ms()
    elapsed = now_ms - int(session["issued_at"])
    flags = judge_run(sim, session, elapsed, now_ms)
    verified = 0 if flags else 1

    consume_session(session["id"], now_ms)

    previous = _rows(
        "SELECT COALESCE(MAX(score), 0) AS best FROM flappy_runs "
        "WHERE player_key = ? AND verified = 1",
        (key,),
    )[0]["best"]

    _write(
        "INSERT INTO flappy_runs "
        "(player, player_key, score, seed, duration_ms, flaps, created_at, "
        " verified, session_id, elapsed_ms, flap_count, flags) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (canonical, key, score, int(session["seed"]), duration,
         json.dumps(sim.flaps[:MAX_FLAP_TRACE]), _utc_stamp(), verified,
         session["id"], elapsed, len(sim.flaps),
         json.dumps(flags)),
    )
    prune_traces()

    if not verified:
        return JSONResponse(status_code=202, content={
            "ok": True,
            "counted": False,
            "player": canonical,
            "score": score,
            "reason": FLAG_TEXT.get(flags[0], "That run could not be verified."),
        })

    rank = next((r["rank"] for r in board_rows("alltime") if r["player_key"] == key), None)
    return {
        "ok": True,
        "counted": True,
        "player": canonical,
        "score": score,
        "rank": rank,
        "personal_best": score > previous,
        "previous_best": previous,
    }


# --------------------------------------------------------------------------- #
# Deciding whether a run happened
# --------------------------------------------------------------------------- #
#
# Replaying a trace proves the trace is consistent. It cannot prove a person
# produced it, and on a deterministic game that difference is the whole problem:
# the client's own physics can be imported and searched for a perfect trace in
# milliseconds, and the result replays exactly like a real run because it is a
# real run, just not one anybody played.
#
# So the checks below are not about the score. They are about whether the run
# cost what a run costs:
#
#   the world       the seed comes from here and is spent once, so a run cannot
#                   be shopped for offline and cannot be handed in twice
#   the clock       a run that simulates 147 seconds has to have taken 147
#                   seconds of the server's own time, which is what turns an
#                   instant forgery back into a two and a half minute wait
#   the hand        taps land on a spread of intervals because hands are not
#                   clocks, and machine timing shows up as a spike on one exact
#                   value that no measured human run comes close to
#
# What this deliberately does not claim: a patient attacker who paces a forged
# run in real time and scatters its timing can still get through. That is a
# genuinely different piece of work from the one-line replay this replaces, and
# for an office side game it is far enough.

SESSION_TTL_MS = 45 * 60 * 1000   # a seed goes stale rather than waiting forever
CLOCK_SLACK_MS = 3000             # request latency and a second of clock drift
BEAT_INTERVAL_MS = 5000           # matches the client's heartbeat timer
MIN_BEAT_RATIO = 0.4              # a dropped beat or two is normal
BEAT_FLOOR = 2                    # below this a run is too short to judge

# Calibrated in tools/check_flappy_api.py against both kinds of trace. Across
# 800 simulated runs with human noise the worst modal share was 0.197; across
# 286 runs from bots with no noise at all the lowest was 0.324. The line sits
# in the empty space between them.
MODAL_SHARE_LIMIT = 0.28
MODAL_MIN_INTERVALS = 25

# A hand cannot hold five taps a second for half a minute, and does not need to:
# hovering takes under two. Short bursts are normal, so this only applies once a
# run is long enough that a burst is not the explanation.
MAX_FLAPS_PER_SEC = 4.5
RATE_MIN_DURATION_MS = 20000

# Two inputs a 25th of a second apart are one press as far as a hand is
# concerned. A few are a double tap; dozens are a machine.
MIN_HUMAN_GAP_TICKS = 4
MAX_SHORT_GAPS = 8

FLAG_TEXT = {
    "faster_than_real_time": "That run finished sooner than it could have been played.",
    "not_enough_beats": "That run was not in contact while it was being played.",
    "beats_outran_clock": "That run reported progress faster than time passed.",
    "machine_timing": "That run's inputs are too evenly spaced to be hand timed.",
    "flap_rate": "That run holds a tapping rate a hand cannot keep up.",
    "double_inputs": "That run has inputs too close together to be separate taps.",
}


def interval_stats(flaps):
    """Shape of the gaps between taps, which is where a machine gives itself away."""
    gaps = [flaps[i + 1] - flaps[i] for i in range(len(flaps) - 1)]
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
    stats = interval_stats(sim.flaps)
    if stats["intervals"] >= MODAL_MIN_INTERVALS \
            and stats["modal_share"] >= MODAL_SHARE_LIMIT:
        flags.append("machine_timing")

    if duration >= RATE_MIN_DURATION_MS:
        rate = len(sim.flaps) / (duration / 1000.0)
        if rate > MAX_FLAPS_PER_SEC:
            flags.append("flap_rate")

    if stats["short"] > MAX_SHORT_GAPS:
        flags.append("double_inputs")

    return flags


def read_trace(raw):
    """Validate the submitted trace enough to replay it safely.

    Deliberately not a plausibility check. The only questions here are whether
    it is a list of ascending tick numbers and whether replaying it will cost
    the server a bounded amount of work.
    """
    if len(raw) > MAX_FLAP_TRACE:
        return None, "That run recorded more inputs than a run can contain."
    flaps = []
    last = -1
    for tick in raw:
        tick = int(tick)
        if tick < 0 or tick > ABSOLUTE_MAX_TICKS:
            return None, "That run's input trace is outside the length of a run."
        if tick < last:
            return None, "That run's input trace is out of order."
        flaps.append(tick)
        last = tick
    return flaps, None


# --------------------------------------------------------------------------- #
# Re-judging the runs that were recorded before any of this existed
# --------------------------------------------------------------------------- #
#
# The board only counts verified runs, and before this release nothing was ever
# verified, so this is not housekeeping. It is what decides which of the scores
# already on the board survive.
#
# Two questions, in order. Does the stored trace still produce the score it was
# recorded with? A trace that does not reproduce its own score describes a run
# that never happened. And if it does replay, does it look hand timed? That
# second question is the one that matters here, because a trace computed offline
# replays perfectly: it is a real run, just not one anybody played.
#
# Runs with no trace left to check are kept. They are unjudgeable rather than
# suspect, and throwing away honest history to be seen doing something about a
# forgery would be the worse trade.
#
# Two shapes of stored trace have to be read here. The release that shipped the
# game encoded it twice, because clean_trace() returned json.dumps(list) and the
# insert called json.dumps() on that string again, so every row written before
# this release holds a JSON string containing a JSON array. Reading only the
# current shape would quietly file the entire existing board under "no trace"
# and clear nothing, which is exactly what happened the first time.

LEGACY_AUDIT_LIMIT = 20000


def decode_trace(raw_trace):
    """Read a stored trace in either encoding.

    Returns (ticks, stored). ticks is None when there is nothing readable.
    stored is False only when the column is empty, which is what pruning leaves
    behind and is the one case that means "cannot be judged" rather than "did
    not happen".
    """
    if raw_trace is None or raw_trace == "":
        return None, False

    value = raw_trace
    for _ in range(2):          # one hop for the current shape, two for legacy
        if not isinstance(value, str):
            break
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            return None, True

    if not isinstance(value, list):
        return None, True
    return value, True


def audit_run(seed, claimed_score, raw_trace):
    """Judge one stored run. Returns (verified, flags)."""
    raw, stored = decode_trace(raw_trace)

    if not stored:
        # Pruning clears the column, so there is nothing left to test this
        # against and it keeps the benefit of the doubt.
        return 1, ["legacy_no_trace"]

    if raw is None:
        return 0, ["unreadable_trace"]

    if not raw:
        # An empty trace that is still stored is not a pruned run, because
        # pruning empties the column rather than writing "[]". A run cannot
        # score without flapping, so a score here was typed, not played.
        if claimed_score > 0:
            return 0, ["scored_without_flapping"]
        return 1, []

    flaps, error = read_trace(raw[:MAX_FLAP_TRACE])
    if error:
        return 0, ["unreadable_trace"]

    sim = replay(seed, flaps)

    # A trace at the cap was trimmed on the way in, so the tail of the run is
    # missing and the replay will legitimately fall short of the recorded score.
    # The timing of what survives is still worth reading, so the score check is
    # skipped rather than the whole run being waved through.
    trimmed = len(raw) >= MAX_FLAP_TRACE
    if not trimmed and sim.score != claimed_score:
        return 0, ["replay_mismatch"]

    # The clock checks need a session, and these runs predate sessions, so only
    # the hand checks apply. That is enough for a computed trace: it has to tap
    # on a schedule, and hands do not.
    flags = []
    stats = interval_stats(sim.flaps)
    duration = sim.duration_ms()
    if stats["intervals"] >= MODAL_MIN_INTERVALS \
            and stats["modal_share"] >= MODAL_SHARE_LIMIT:
        flags.append("machine_timing")
    if duration >= RATE_MIN_DURATION_MS \
            and len(sim.flaps) / (duration / 1000.0) > MAX_FLAPS_PER_SEC:
        flags.append("flap_rate")
    if stats["short"] > MAX_SHORT_GAPS:
        flags.append("double_inputs")
    if trimmed and not flags:
        flags.append("legacy_trace_trimmed")
        return 1, flags

    return (0 if flags else 1), flags


def audit_legacy_runs():
    """Re-judge every run that has not been judged yet. Safe to run twice."""
    pending = _rows(
        "SELECT id, score, seed, flaps FROM flappy_runs "
        "WHERE flags IS NULL ORDER BY id LIMIT ?",
        (LEGACY_AUDIT_LIMIT,),
    )
    if not pending:
        return {"checked": 0, "voided": 0}

    voided = 0
    for row in pending:
        verified, flags = audit_run(row["seed"], row["score"], row["flaps"])
        if not verified:
            voided += 1
        _write(
            "UPDATE flappy_runs SET verified = ?, flags = ? WHERE id = ?",
            (verified, json.dumps(flags), row["id"]),
        )

    log.info("flappy: audited %d existing runs, %d no longer count",
             len(pending), voided)
    return {"checked": len(pending), "voided": voided}


def clear_board(player=None):
    """Delete runs. Everything, or one player.

    This lives here rather than only in the admin script because the admin
    script is not in the container: the compose file mounts ./app and ./data and
    nothing else. Clearing the board on the server therefore has to go through
    this module, and the alternative is someone hand typing DELETE against the
    database the real leaderboard's buffer also lives in. Keeping the statement
    here is what makes "only flappy_ tables are touched" a property of the code
    instead of a promise about a copied command.

        docker exec <container> python -c \\
            "import sys; sys.path.insert(0, '/app'); \\
             import flappy; print(flappy.clear_board())"
    """
    if player:
        key = name_key(player)
        if not key:
            return {"deleted": 0, "player": player}
        before = _rows("SELECT COUNT(*) AS n FROM flappy_runs WHERE player_key = ?",
                       (key,))[0]["n"]
        _write("DELETE FROM flappy_runs WHERE player_key = ?", (key,))
        log.info("flappy: cleared %d runs for %s", before, player)
        return {"deleted": before, "player": player}

    before = _rows("SELECT COUNT(*) AS n FROM flappy_runs")[0]["n"]
    _write("DELETE FROM flappy_runs")
    _write("DELETE FROM flappy_sessions")
    log.info("flappy: cleared the board, %d runs deleted", before)
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
        "SELECT created_at FROM flappy_runs WHERE player_key = ? "
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
        "SELECT COUNT(*) AS n FROM flappy_runs "
        "WHERE player_key = ? AND created_at >= ? AND created_at < ?",
        (player_key, start, end))[0]["n"]
    if today >= MAX_RUNS_PER_DAY:
        return ("That is %d runs today. The board will still be here tomorrow."
                % today, 429)

    if ip:
        from_ip = _rows(
            "SELECT COUNT(*) AS n FROM flappy_runs "
            "WHERE session_id IN (SELECT id FROM flappy_sessions WHERE issued_ip = ?) "
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
            "UPDATE flappy_runs SET flaps = NULL "
            "WHERE flaps IS NOT NULL AND created_at < ?", (cutoff,))
        _conn.commit()
        return cur.rowcount


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
            "SELECT COUNT(*) AS n FROM flappy_sessions "
            "WHERE issued_ip = ? AND issued_at >= ?", (ip, cutoff))[0]["n"]
        if minted >= SESSION_MINT_PER_HOUR:
            return _reject("Starting runs a little fast. Try again shortly.", status=429)
        open_now = _rows(
            "SELECT COUNT(*) AS n FROM flappy_sessions "
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
    audit_legacy_runs()
    sweep_sessions()
except Exception:  # pragma: no cover - a side game must never block a restart
    log.exception("flappy: startup audit failed, board left as it was")
