"""Flappy Duck: a side game that shares the app and nothing else.

Isolation is the whole point of this module, so it is worth being explicit
about what that means:

* It never opens ``leaderboard.json`` or ``history.json`` for writing. It reads
  the roster, and only to check that a submitted name belongs to a real player.
* It never touches a daily rank, penalty, streak, excused day, weekly total or
  win counter. Nothing in here knows those concepts exist.
* It adds no scheduled job. Seasons are derived from ``created_at`` when a board
  is queried, so there is no rollover to run and nothing to get out of step.
* Its tables are prefixed ``flappy_`` and live in the SQLite file the app
  already keeps on the data volume, so a deploy stays "upload files, restart".

The rules of the game itself live in ``flappy/sim.mjs`` and every tuning value
lives in ``flappy/config.mjs``. The few constants repeated here are the ones the
server needs in order to judge whether a submitted run was physically possible,
and they are checked against that file by ``tools/check_flappy_api.py``.
"""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from pydantic import BaseModel, Field
import difflib
import json
import os
import re
import sqlite3
import threading
from datetime import datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo  # Python 3.9+ (needs tzdata on slim images)
except ImportError:  # pragma: no cover
    ZoneInfo = None


router = APIRouter(prefix="/flappy", tags=["flappy"])

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "flappy")

# Read-only. The roster is the only thing this module borrows from the real
# leaderboard, and it borrows it to reject typos.
STATE_FILE = os.environ.get("ZS_STATE_FILE", "/home/leaderboard.json")
BUFFER_DB = os.environ.get("ZS_BUFFER_DB", "/home/zipscores_buffer.db")
TIMEZONE = os.environ.get("ZS_TIMEZONE", "America/Chicago")

# Mirrors of the values in flappy/config.mjs that the server needs.
SCROLL_SPEED = 110.0
SPACING = 160.0
FIRST_OBSTACLE_X = 340.0
TILE_W = 52.0
DUCK_X = 62.0
DUCK_W = 34.0
STEP_MS = 1000.0 / 120.0

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
_conn.execute("CREATE INDEX IF NOT EXISTS idx_flappy_created ON flappy_runs(created_at)")
_conn.execute("CREATE INDEX IF NOT EXISTS idx_flappy_player ON flappy_runs(player_key)")
_conn.execute("CREATE INDEX IF NOT EXISTS idx_flappy_score ON flappy_runs(score DESC, id ASC)")
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
# Roster, read-only
# --------------------------------------------------------------------------- #

def _load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def name_key(name):
    return re.sub(r"\s+", " ", str(name or "")).strip().lower()


def known_players():
    return sorted(_load_state().keys())


def resolve_player(name):
    """Bind a hand-typed name to the roster, or explain what was probably meant.

    Returns ``(canonical_name, error_message)``. The same containment-then-fuzzy
    order the accommodation form uses: typing only a first name is the common
    miss, and difflib scores that badly against a full name.
    """
    cleaned = re.sub(r"\s+", " ", str(name or "")).strip()
    if not cleaned:
        return "", "Enter your name so the run can be posted."
    known = known_players()
    if not known:
        # An empty roster means the leaderboard has not been seeded yet. Refusing
        # every name in that state would make the game unplayable for no gain.
        return cleaned, None
    lowered = cleaned.lower()
    for existing in known:
        if existing.lower() == lowered:
            return existing, None

    partial = [k for k in known if lowered in k.lower()]
    match = partial[0] if len(partial) == 1 else None
    if not match:
        close = difflib.get_close_matches(cleaned, known, n=1, cutoff=0.5)
        match = close[0] if close else None
    if match:
        return match, None
    return "", ('No leaderboard player named "' + cleaned + '". '
                "Use your name exactly as it appears on the board.")


# --------------------------------------------------------------------------- #
# Board queries
# --------------------------------------------------------------------------- #

# One row per player, their best qualifying run, earliest first on a tie. The
# id is the final tiebreak because it is monotonic, so two runs written in the
# same second still have a defined order.
_BEST_PER_PLAYER = """
    WITH ranked AS (
        SELECT id, player, player_key, score, seed, duration_ms, created_at,
               ROW_NUMBER() OVER (
                   PARTITION BY player_key
                   ORDER BY score DESC, created_at ASC, id ASC
               ) AS rn
        FROM flappy_runs
        WHERE score > 0 %s
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
    WHERE score > 0 %s
    GROUP BY player_key
    ORDER BY total DESC, last_at ASC, player_key ASC
"""


def _range_clause(bounds, column="created_at"):
    if not bounds:
        return "", ()
    return " AND %s >= ? AND %s < ?" % (column, column), tuple(bounds)


def _display_names():
    """Latest spelling seen for each player key."""
    rows = _rows(
        "SELECT player_key, player FROM flappy_runs "
        "WHERE id IN (SELECT MAX(id) FROM flappy_runs GROUP BY player_key)"
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
    first = _rows("SELECT MIN(created_at) AS m FROM flappy_runs WHERE score > 0")
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

    totals = _rows(
        "SELECT COUNT(*) AS runs, COALESCE(SUM(score), 0) AS total, "
        "COALESCE(MAX(score), 0) AS best FROM flappy_runs WHERE player_key = ?",
        (key,),
    )[0]

    def best_in(bounds):
        clause, params = _range_clause(bounds)
        sql = "SELECT COALESCE(MAX(score), 0) AS best FROM flappy_runs WHERE player_key = ?" + clause
        return _rows(sql, (key,) + params)[0]["best"]

    ranks = {}
    for view in VIEWS:
        ranks[view] = next(
            (r["rank"] for r in board_rows(view) if r["player_key"] == key), None)

    recent = _rows(
        "SELECT score, duration_ms, created_at FROM flappy_runs "
        "WHERE player_key = ? ORDER BY id DESC LIMIT 10",
        (key,),
    )

    return {
        "ok": True,
        "player": canonical,
        "runs": totals["runs"],
        "total": totals["total"],
        "best": totals["best"],
        "best_season": best_in(_bounds_for("season")),
        "best_today": best_in(_bounds_for("today")),
        "ranks": ranks,
        "recent": recent,
    }


# --------------------------------------------------------------------------- #
# Submission
# --------------------------------------------------------------------------- #

def min_duration_ms(score):
    """The fastest a run can legitimately reach a score.

    The duck scores when its centre passes an obstacle's centre, so obstacle
    ``i`` is scored once the world has scrolled ``firstObstacleX + i * spacing
    - (duckX + duckW / 2) + tileW / 2`` pixels. The scroll speed is constant by
    design, which is what makes this a hard floor rather than a guess.
    """
    if score <= 0:
        return 0
    distance = (FIRST_OBSTACLE_X + (score - 1) * SPACING
                + TILE_W / 2 - (DUCK_X + DUCK_W / 2))
    return int(distance / SCROLL_SPEED * 1000)


class ScoreIn(BaseModel):
    player: str = Field(default="", max_length=120)
    score: int = 0
    seed: int = 0
    duration_ms: int = 0
    flaps: list[int] = Field(default_factory=list)


def _reject(message, status=400):
    return JSONResponse(status_code=status, content={"ok": False, "error": message})


@router.post("/api/score")
def submit_score(payload: ScoreIn, request: Request):
    canonical, error = resolve_player(payload.player)
    if error:
        return _reject(error)

    if payload.score <= 0:
        return _reject("A run of zero is not posted to the board.")
    if payload.score > MAX_SCORE or payload.duration_ms <= 0 \
            or payload.duration_ms > MAX_DURATION_MS:
        return _reject("That run does not look like a real one.")

    key = name_key(canonical)
    guard = check_submission(key, payload)
    if guard:
        return _reject(guard[0], status=guard[1])

    flaps = clean_trace(payload.flaps, payload.duration_ms)
    previous = _rows(
        "SELECT COALESCE(MAX(score), 0) AS best FROM flappy_runs WHERE player_key = ?",
        (key,),
    )[0]["best"]

    _write(
        "INSERT INTO flappy_runs "
        "(player, player_key, score, seed, duration_ms, flaps, created_at, verified) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, 0)",
        (canonical, key, int(payload.score), int(payload.seed) & 0xFFFFFFFF,
         int(payload.duration_ms), json.dumps(flaps), _utc_stamp()),
    )
    prune_traces()

    rank = next((r["rank"] for r in board_rows("alltime") if r["player_key"] == key), None)
    return {
        "ok": True,
        "player": canonical,
        "score": payload.score,
        "rank": rank,
        "personal_best": payload.score > previous,
        "previous_best": previous,
    }


# --------------------------------------------------------------------------- #
# Plausibility and rate limiting
# --------------------------------------------------------------------------- #
#
# None of this proves a run happened. Proving it means replaying the seed and
# the flap trace, which is why both are stored and why the simulation was
# written to be deterministic; the `verified` column is where that verdict will
# go. What these checks do is make the cheap attacks cost something: posting a
# score that no amount of skill could reach in the time claimed, or holding a
# submit button down. That is the right amount of effort for a side game whose
# leaderboard decides nothing.

MIN_SUBMIT_GAP_S = 3          # two runs cannot finish in the same breath
GAP_FRACTION_OF_RUN = 0.75    # nor can a 60 second run be posted 5 seconds apart
MAX_RUNS_PER_DAY = 300        # a busy day is maybe 50 runs
DURATION_SLACK_MS = 250       # rounding, plus the tick the death is noticed on
TRACE_RETENTION_DAYS = 30     # matches ZS_RETENTION_DAYS in spirit

_last_prune = [""]


def _seconds_between(older, newer):
    a, b = _parse_stamp(older), _parse_stamp(newer)
    if a is None or b is None:
        return None
    return (b - a).total_seconds()


def check_submission(player_key, payload):
    """Returns ``(message, status)`` when a run should be refused."""
    floor_ms = min_duration_ms(payload.score)
    if payload.duration_ms + DURATION_SLACK_MS < floor_ms:
        return ("That run is faster than the obstacles arrive. "
                "%d patches takes at least %.1f seconds."
                % (payload.score, floor_ms / 1000.0), 400)

    if len(payload.flaps) > MAX_FLAP_TRACE:
        return ("That run recorded more inputs than a run can contain.", 400)
    if not trace_is_ordered(payload.flaps, payload.duration_ms):
        return ("That run's input trace does not line up with its length.", 400)

    now = _utc_stamp()
    recent = _rows(
        "SELECT created_at FROM flappy_runs WHERE player_key = ? "
        "ORDER BY id DESC LIMIT 1", (player_key,))
    if recent:
        gap = _seconds_between(recent[0]["created_at"], now)
        if gap is not None and gap < MIN_SUBMIT_GAP_S:
            return ("Posting runs a little fast. Try again in a moment.", 429)
        needed = payload.duration_ms / 1000.0 * GAP_FRACTION_OF_RUN
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

    return None


def trace_is_ordered(flaps, duration_ms):
    """A trace has to be ascending tick numbers inside the run it describes."""
    if not flaps:
        return True
    limit = int(duration_ms / STEP_MS) + 240  # the ready state and the death fall
    last = -1
    for tick in flaps:
        if not isinstance(tick, int) or tick < 0 or tick > limit or tick < last:
            return False
        last = tick
    return True


def clean_trace(flaps, duration_ms):
    trimmed = [int(t) for t in flaps[:MAX_FLAP_TRACE] if isinstance(t, int)]
    return json.dumps(trimmed)


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

@router.get("/api/roster")
def api_roster():
    """Names for the input's datalist. The page is a static file, so it cannot
    have the roster templated into it the way the accommodation form does."""
    return {"players": known_players()}


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
