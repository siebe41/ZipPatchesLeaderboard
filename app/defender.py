"""PatchDefender: a side game that shares the app and nothing else.

Isolation is the whole point of this module, so it is worth being explicit
about what that means:

* It never opens ``leaderboard.json`` or ``history.json`` at all, for reading or
  for writing. Players type their own name, so there is no roster to consult.
* It never touches a daily rank, penalty, streak, excused day, weekly total or
  win counter. Nothing in here knows those concepts exist.
* It adds no scheduled job. Seasons are derived from ``created_at`` when a board
  is queried, so there is no rollover to run and nothing to get out of step.
* Its tables are prefixed ``defender_`` and live in the SQLite file the app
  already keeps on the data volume, so a deploy stays "upload files, restart".

The rules of the game itself live in ``defender/sim.mjs`` and every tuning
value lives in ``defender/config.mjs``. That simulation is mirrored here,
exactly, so the server can replay a submitted run rather than take its word
for the score.

On trusting submissions. A deterministic game is replayable, and a replayable
run is forgeable: anyone can import the client's own rules, search for a good
input trace offline, and hand over a trace that replays perfectly. Replay proves
a trace is self-consistent, never that a person produced it. So the seed is
issued by the server and spent once, the run is paced against the server's own
clock through heartbeats, and the trace is measured for input a human hand
cannot produce. Replay is the floor here, not the ceiling.

On matching the JavaScript exactly. Positions are integers in sub-units and
every division floors. Nothing here calls a square root: a zero-day's fall
and an interceptor's flight are both computed as "how many ticks to arrive,
then move linearly over that many ticks" rather than as a normalised
direction times a speed, which is what a square root would otherwise be
needed for. The generator is held here as an unsigned Python int and masked
after every operation, because JavaScript's bitwise operators coerce to 32
bits.

On this game's trace encoding. Every other game here packs an action into
``tick * 4 + action``, because the whole input is a handful of enum values.
This game's only input is "aim here", which is a point, not an enum -- so the
trace is a flat list of ``[tick, x, y]`` triplets instead of one packed
integer per event.

None of the artwork or the names come from any existing arcade game. It is a
point-defence game, which is a genre, built out of Patch My PC's own
material: intercepting zero-days before they land on an endpoint.
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


router = APIRouter(prefix="/defender", tags=["defender"])

log = logging.getLogger("defender")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "defender")

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
    CREATE TABLE IF NOT EXISTS defender_runs (
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
        intercepts  INTEGER,
        shots       INTEGER,
        flags       TEXT
    )
    """
)

_conn.execute(
    """
    CREATE TABLE IF NOT EXISTS defender_sessions (
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

_conn.execute("CREATE INDEX IF NOT EXISTS idx_defender_created ON defender_runs(created_at)")
_conn.execute("CREATE INDEX IF NOT EXISTS idx_defender_player ON defender_runs(player_key)")
_conn.execute("CREATE INDEX IF NOT EXISTS idx_defender_score ON defender_runs(score DESC, id ASC)")
_conn.execute("CREATE INDEX IF NOT EXISTS idx_defender_sess ON defender_sessions(issued_at)")
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
        FROM defender_runs
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
    FROM defender_runs
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
        "SELECT player_key, player FROM defender_runs "
        "WHERE id IN (SELECT MAX(id) FROM defender_runs "
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
    first = _rows("SELECT MIN(created_at) AS m FROM defender_runs "
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
        "COALESCE(MAX(score), 0) AS best, COALESCE(SUM(intercepts), 0) AS intercepts, "
        "COALESCE(MAX(level), 0) AS furthest FROM defender_runs "
        "WHERE player_key = ? AND verified = 1",
        (key,),
    )[0]

    def best_in(bounds):
        clause, params = _range_clause(bounds)
        sql = ("SELECT COALESCE(MAX(score), 0) AS best FROM defender_runs "
               "WHERE player_key = ? AND verified = 1" + clause)
        return _rows(sql, (key,) + params)[0]["best"]

    ranks = {}
    for view in VIEWS:
        ranks[view] = next(
            (r["rank"] for r in board_rows(view) if r["player_key"] == key), None)

    recent = _rows(
        "SELECT score, duration_ms, level, intercepts, created_at "
        "FROM defender_runs "
        "WHERE player_key = ? AND verified = 1 ORDER BY id DESC LIMIT 10",
        (key,),
    )

    return {
        "ok": True,
        "player": stored or canonical,
        "runs": totals["runs"],
        "total": totals["total"],
        "best": totals["best"],
        "intercepts": totals["intercepts"],
        "furthest_level": totals["furthest"],
        "best_season": best_in(_bounds_for("season")),
        "best_today": best_in(_bounds_for("today")),
        "ranks": ranks,
        "recent": recent,
    }


# --------------------------------------------------------------------------- #
# The simulation, ported from defender/sim.mjs
# --------------------------------------------------------------------------- #

def _u32(x):
    return x & 0xFFFFFFFF


def _make_rng(seed):
    """mulberry32, matching defender/rng.mjs."""
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


UNIT = 64
WIDTH = 720
HEIGHT = 480
GROUND_Y = 460


def _px(v):
    return v * UNIT


WIDTH_SU = _px(WIDTH)
GROUND_SU = _px(GROUND_Y)
SILO_X = 360
SILO_Y = 470
SILO_X_SU = _px(SILO_X)
SILO_Y_SU = _px(SILO_Y)

ENDPOINT_COUNT = 5
ENDPOINT_HALF_W = 22

INTERCEPTOR_SPEED_SU = 340
FIRE_COOLDOWN_TICKS = 18
MAX_INTERCEPTORS = 6
BLAST_RADIUS_PX = 34
BLAST_TICKS = 26

MISSILE_SPEED_MIN_SU = 22
MISSILE_SPEED_MAX_SU = 42
MISSILES_BASE = 6
MISSILES_PER_LEVEL = 2
MISSILES_CAP = 20
SPAWN_STAGGER_TICKS = 600

READY_TICKS = 90
CLEAR_TICKS = 120

MISSILE_POINTS = 25
ENDPOINT_BONUS = 100
EXTRA_ENDPOINT_AT = 1200
EXTRA_ENDPOINT_EVERY = 2000

LEVEL_SPEED_PCT = (100, 118, 136, 154, 172, 190, 210)

STEP_MS = 1000.0 / 120.0
MAX_SHOTS = 4000
ABSOLUTE_MAX_TICKS = 120 * 60 * 12
TAIL_TICKS = 120 * 15
MAX_SCORE = 10000000

S_READY, S_PLAYING, S_CLEAR, S_DEAD = ("ready", "playing", "clear", "dead")


def _clamp(v, lo, hi):
    return lo if v < lo else (hi if v > hi else v)


def _dist_sq(ax, ay, bx, by):
    dx = ax - bx
    dy = ay - by
    return dx * dx + dy * dy


def _tier_speed_pct(level):
    i = min(max(level - 1, 0), len(LEVEL_SPEED_PCT) - 1)
    return LEVEL_SPEED_PCT[i]


def endpoint_x(i):
    return fdiv(WIDTH * (i + 1), ENDPOINT_COUNT + 1)


class Sim:
    """One run. Advance it with step() and nothing else."""

    def __init__(self, seed):
        self.seed = _u32(seed)
        self._rng = _make_rng(seed)
        self.tick = 0
        self.state = S_READY
        self.state_tick = 0

        self.score = 0
        self.level = 1
        self.next_extra_endpoint = EXTRA_ENDPOINT_AT
        self.endpoints = [True] * ENDPOINT_COUNT

        self.missiles = []
        self.interceptors = []
        self.cooldown = 0

        self.missiles_intercepted = 0
        self.missiles_landed = 0
        self.shots_fired = 0
        self.levels_cleared = 0

        self.play_start_tick = -1
        self.end_tick = -1

        self.pending = []
        self.inputs = []

        self._spawn_wave()

    def _spawn_wave(self):
        count = min(MISSILES_CAP, MISSILES_BASE + (self.level - 1) * MISSILES_PER_LEVEL)
        pct = _tier_speed_pct(self.level)
        for _ in range(count):
            start_x = _rng_int(self._rng, WIDTH) * UNIT
            target_idx = _rng_int(self._rng, ENDPOINT_COUNT)
            target_x = _px(endpoint_x(target_idx))
            speed_range = MISSILE_SPEED_MAX_SU - MISSILE_SPEED_MIN_SU + 1
            vy = fdiv((MISSILE_SPEED_MIN_SU + _rng_int(self._rng, speed_range)) * pct, 100)
            ticks_to_ground = max(1, fdiv(GROUND_SU, vy))
            vx = fdiv(target_x - start_x, ticks_to_ground)
            self.missiles.append({
                "x": start_x, "y": 0, "vx": vx, "vy": vy,
                "delay": _rng_int(self._rng, SPAWN_STAGGER_TICKS),
            })

    def _add_score(self, points):
        self.score = min(MAX_SCORE, self.score + points)
        if self.score < self.next_extra_endpoint:
            return
        dead_idx = None
        for i, alive in enumerate(self.endpoints):
            if not alive:
                dead_idx = i
                break
        if dead_idx is None:
            return
        self.endpoints[dead_idx] = True
        self.next_extra_endpoint += EXTRA_ENDPOINT_EVERY

    def _fire(self, target_x_px, target_y_px):
        if self.cooldown > 0 or len(self.interceptors) >= MAX_INTERCEPTORS:
            return
        self.cooldown = FIRE_COOLDOWN_TICKS
        tx = _px(_clamp(target_x_px, 0, WIDTH))
        ty = _px(_clamp(target_y_px, 0, GROUND_Y))
        dx = tx - SILO_X_SU
        dy = ty - SILO_Y_SU
        spread = max(abs(dx), abs(dy))
        steps = max(1, fdiv(spread, INTERCEPTOR_SPEED_SU))
        self.interceptors.append({
            "x": SILO_X_SU, "y": SILO_Y_SU,
            "vx": fdiv(dx, steps), "vy": fdiv(dy, steps),
            "ticks_left": steps, "exploded": False, "blast_ticks_left": 0,
        })
        self.shots_fired += 1

    def _apply_aim(self, x, y):
        if self.state == S_READY:
            if self.state_tick < READY_TICKS:
                return
            self.state = S_PLAYING
            self.state_tick = 0
            self.play_start_tick = self.tick
        if self.state != S_PLAYING:
            return
        self._fire(x, y)

    def _drain_input(self):
        i = 0
        while i < len(self.pending):
            t = self.pending[i]
            if t > self.tick:
                i += 3
                continue
            x = self.pending[i + 1]
            y = self.pending[i + 2]
            del self.pending[i:i + 3]
            self.inputs.extend([self.tick, x, y])
            self._apply_aim(x, y)

    def _update_missiles(self):
        for i in range(len(self.missiles) - 1, -1, -1):
            m = self.missiles[i]
            if m["delay"] > 0:
                m["delay"] -= 1
                continue
            m["x"] += m["vx"]
            m["y"] += m["vy"]
            if m["y"] < GROUND_SU:
                continue

            del self.missiles[i]
            self.missiles_landed += 1
            hit_radius = _px(ENDPOINT_HALF_W)
            for e in range(ENDPOINT_COUNT):
                if not self.endpoints[e]:
                    continue
                if abs(m["x"] - _px(endpoint_x(e))) <= hit_radius:
                    self.endpoints[e] = False
                    break
            if not any(self.endpoints):
                self.state = S_DEAD
                self.end_tick = self.tick
                return

    def _update_interceptors(self):
        blast_radius_su = _px(BLAST_RADIUS_PX)
        blast_sq = blast_radius_su * blast_radius_su
        for i in range(len(self.interceptors) - 1, -1, -1):
            p = self.interceptors[i]
            if not p["exploded"]:
                p["x"] += p["vx"]
                p["y"] += p["vy"]
                p["ticks_left"] -= 1
                if p["ticks_left"] <= 0:
                    p["exploded"] = True
                    p["blast_ticks_left"] = BLAST_TICKS
                continue
            for mi in range(len(self.missiles) - 1, -1, -1):
                m = self.missiles[mi]
                if m["delay"] > 0:
                    continue
                if _dist_sq(p["x"], p["y"], m["x"], m["y"]) <= blast_sq:
                    del self.missiles[mi]
                    self.missiles_intercepted += 1
                    self._add_score(MISSILE_POINTS)
            p["blast_ticks_left"] -= 1
            if p["blast_ticks_left"] <= 0:
                del self.interceptors[i]

    def step(self):
        if self.state == S_DEAD:
            return

        self._drain_input()

        if self.state == S_READY:
            self.state_tick += 1
        elif self.state == S_PLAYING:
            if self.cooldown > 0:
                self.cooldown -= 1
            self._update_missiles()
            if self.state == S_DEAD:
                self.tick += 1
                return
            self._update_interceptors()
            if not self.missiles:
                bonus = sum(ENDPOINT_BONUS for alive in self.endpoints if alive)
                self._add_score(bonus)
                self.levels_cleared += 1
                self.interceptors = []
                self.state = S_CLEAR
                self.state_tick = 0
        elif self.state == S_CLEAR:
            self.state_tick += 1
            if self.state_tick >= CLEAR_TICKS:
                self.level += 1
                self._spawn_wave()
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

    last = 0
    for i in range(0, len(inputs), 3):
        last = inputs[i]
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
        "INSERT INTO defender_sessions (id, seed, issued_at, issued_ip) "
        "VALUES (?, ?, ?, ?)",
        (sid, seed, _now_ms(), ip),
    )
    return {"session": sid, "seed": seed}


def claim_session(sid, ip):
    if not sid:
        return None, ("That run did not come with a session. Reload the page.", 400)
    rows = _rows("SELECT * FROM defender_sessions WHERE id = ?", (sid,))
    if not rows:
        return None, ("That run's session is not one this server issued.", 400)
    row = rows[0]
    if row["consumed_at"] is not None:
        return None, ("That run has already been posted.", 409)
    if _now_ms() - int(row["issued_at"]) > SESSION_TTL_MS:
        return None, ("That run took too long to post. Start a fresh one.", 400)
    return row, None


def consume_session(sid, now_ms):
    _write("UPDATE defender_sessions SET consumed_at = ? WHERE id = ?", (now_ms, sid))


def record_beat(sid, tick):
    rows = _rows(
        "SELECT first_beat, beats, consumed_at FROM defender_sessions WHERE id = ?",
        (sid,))
    if not rows or rows[0]["consumed_at"] is not None:
        return False
    now = _now_ms()
    if rows[0]["first_beat"] is None:
        _write(
            "UPDATE defender_sessions SET first_beat = ?, first_tick = ?, "
            "last_beat = ?, last_tick = ?, beats = 1 WHERE id = ?",
            (now, tick, now, tick, sid),
        )
    else:
        _write(
            "UPDATE defender_sessions SET last_beat = ?, last_tick = ?, "
            "beats = beats + 1 WHERE id = ?",
            (now, tick, sid),
        )
    return True


def sweep_sessions():
    _write("DELETE FROM defender_sessions WHERE issued_at < ?",
           (_now_ms() - SESSION_TTL_MS * 2,))


# --------------------------------------------------------------------------- #
# Deciding whether a run happened
# --------------------------------------------------------------------------- #
#
# There is one input stream here -- a shot, at a point -- and every entry is
# hand timed, same reasoning as Ducker's hop stream: nothing about a shot's
# tick is dictated by a cooldown the player does not control, since the
# cooldown only gates whether it *fires*, not whether the press is recorded.

MODAL_SHARE_LIMIT = 0.35
MODAL_MIN_INTERVALS = 30

MAX_SHOTS_PER_SEC = 6.0
RATE_MIN_DURATION_MS = 20000

MIN_HUMAN_GAP_TICKS = 2
MAX_SHORT_GAPS = 20

# A shot that never explodes near anything is not a shot that ever had a
# chance to score, so accuracy is measured against explosions that scored at
# least one kill, not against every press. A solver that only fires shots
# already proven to land sits near 100%; a hand aiming freehand at a moving
# target does not.
MAX_HUMAN_ACCURACY = 0.85
ACCURACY_MIN_SHOTS = 30

FLAG_TEXT = {
    "faster_than_real_time": "That run finished sooner than it could have been played.",
    "not_enough_beats": "That run was not in contact while it was being played.",
    "beats_outran_clock": "That run reported progress faster than time passed.",
    "machine_timing": "That run's shots are too evenly spaced to be hand timed.",
    "shot_rate": "That run holds a firing rate a hand cannot keep up.",
    "double_inputs": "That run has inputs too close together to be separate presses.",
    "inhuman_accuracy": "That run lands more of its shots than a hand can aim.",
    "replay_mismatch": "That run does not replay to the score it was posted with.",
    "unreadable_trace": "That run's input trace could not be read.",
    "scored_without_playing": "That run scored without recording any input.",
    "no_trace": "That run's input trace is no longer stored.",
    "trace_trimmed": "That run's input trace was longer than the stored limit.",
}

CLOCK_FLAGS = ("faster_than_real_time", "not_enough_beats", "beats_outran_clock")


def interval_stats(inputs):
    ticks = [inputs[i] for i in range(0, len(inputs), 3)]
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
    if sim.shots_fired < ACCURACY_MIN_SHOTS:
        return None
    return sim.missiles_intercepted / float(sim.shots_fired)


def hand_flags(sim):
    flags = []
    stats = interval_stats(sim.inputs)
    duration = sim.duration_ms()

    if stats["intervals"] >= MODAL_MIN_INTERVALS \
            and stats["modal_share"] >= MODAL_SHARE_LIMIT:
        flags.append("machine_timing")

    if duration >= RATE_MIN_DURATION_MS:
        shots = len(sim.inputs) // 3
        if shots / (duration / 1000.0) > MAX_SHOTS_PER_SEC:
            flags.append("shot_rate")

    if stats["short"] > MAX_SHORT_GAPS:
        flags.append("double_inputs")

    hit_rate = accuracy_of(sim)
    if hit_rate is not None and hit_rate > MAX_HUMAN_ACCURACY:
        flags.append("inhuman_accuracy")

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
    if len(raw) % 3 != 0:
        return None, "That run's input trace is not a trace."
    if len(raw) // 3 > MAX_SHOTS:
        return None, "That run recorded more shots than a run can contain."
    inputs = []
    last = -1
    for i in range(0, len(raw), 3):
        tick = int(raw[i])
        x = int(raw[i + 1])
        y = int(raw[i + 2])
        if tick < 0:
            return None, "That run's input trace is not a trace."
        if tick > ABSOLUTE_MAX_TICKS:
            return None, "That run's input trace is outside the length of a run."
        if tick < last:
            return None, "That run's input trace is out of order."
        inputs.extend([tick, x, y])
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

    capped = raw[:MAX_SHOTS * 3]
    inputs, error = read_trace(capped)
    if error:
        return 0, ["unreadable_trace"]

    sim = replay(seed, inputs)

    trimmed = len(raw) >= MAX_SHOTS * 3
    if not trimmed and sim.score != claimed_score:
        return 0, ["replay_mismatch"]

    flags = hand_flags(sim)
    if trimmed and not flags:
        flags.append("trace_trimmed")
        return 1, flags

    return (0 if flags else 1), flags


def audit_pending_runs():
    pending = _rows(
        "SELECT id, score, seed, inputs FROM defender_runs "
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
            "UPDATE defender_runs SET verified = ?, flags = ? WHERE id = ?",
            (verified, json.dumps(flags), row["id"]),
        )

    log.info("defender: audited %d runs, %d no longer count", len(pending), voided)
    return {"checked": len(pending), "voided": voided}


def clear_board(player=None):
    if player:
        key = name_key(player)
        if not key:
            return {"deleted": 0, "player": player}
        before = _rows("SELECT COUNT(*) AS n FROM defender_runs WHERE player_key = ?",
                       (key,))[0]["n"]
        _write("DELETE FROM defender_runs WHERE player_key = ?", (key,))
        log.info("defender: cleared %d runs for %s", before, player)
        return {"deleted": before, "player": player}

    before = _rows("SELECT COUNT(*) AS n FROM defender_runs")[0]["n"]
    _write("DELETE FROM defender_runs")
    _write("DELETE FROM defender_sessions")
    log.info("defender: cleared the board, %d runs deleted", before)
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
        "SELECT created_at FROM defender_runs WHERE player_key = ? "
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
        "SELECT COUNT(*) AS n FROM defender_runs "
        "WHERE player_key = ? AND created_at >= ? AND created_at < ?",
        (player_key, start, end))[0]["n"]
    if today >= MAX_RUNS_PER_DAY:
        return ("That is %d runs today. The board will still be here tomorrow."
                % today, 429)

    if ip:
        from_ip = _rows(
            "SELECT COUNT(*) AS n FROM defender_runs "
            "WHERE session_id IN (SELECT id FROM defender_sessions WHERE issued_ip = ?) "
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
            "UPDATE defender_runs SET inputs = NULL "
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
        "SELECT COALESCE(MAX(score), 0) AS best FROM defender_runs "
        "WHERE player_key = ? AND verified = 1",
        (key,),
    )[0]["best"]

    _write(
        "INSERT INTO defender_runs "
        "(player, player_key, score, seed, duration_ms, inputs, created_at, "
        " verified, session_id, elapsed_ms, input_count, level, intercepts, shots, flags) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (canonical, key, score, int(session["seed"]), duration,
         json.dumps(sim.inputs[:MAX_SHOTS * 3]), _utc_stamp(), verified,
         session["id"], elapsed, len(sim.inputs) // 3, sim.level,
         sim.missiles_intercepted, sim.shots_fired, json.dumps(flags)),
    )
    prune_traces()

    if not verified:
        return JSONResponse(status_code=202, content={
            "ok": True,
            "counted": False,
            "player": canonical,
            "score": score,
            "level": sim.level,
            "intercepts": sim.missiles_intercepted,
            "reason": FLAG_TEXT.get(flags[0], "That run could not be verified."),
        })

    rank = next((r["rank"] for r in board_rows("alltime") if r["player_key"] == key), None)
    return {
        "ok": True,
        "counted": True,
        "player": canonical,
        "score": score,
        "level": sim.level,
        "intercepts": sim.missiles_intercepted,
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
            "SELECT COUNT(*) AS n FROM defender_sessions "
            "WHERE issued_ip = ? AND issued_at >= ?", (ip, cutoff))[0]["n"]
        if minted >= SESSION_MINT_PER_HOUR:
            return _reject("Starting runs a little fast. Try again shortly.", status=429)
        open_now = _rows(
            "SELECT COUNT(*) AS n FROM defender_sessions "
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
