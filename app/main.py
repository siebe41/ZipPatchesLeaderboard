from fastapi import FastAPI, Request, Form, File, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import html
import json
import os
import re
import random
import math
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

try:
    from zoneinfo import ZoneInfo  # Python 3.9+ (needs tzdata on slim images)
except ImportError:  # pragma: no cover
    ZoneInfo = None

app = FastAPI()

# --- NAS Preparation: CORS Middleware ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

STATE_FILE = os.environ.get("ZS_STATE_FILE", "/home/leaderboard.json")
HISTORY_FILE = os.environ.get("ZS_HISTORY_FILE", "/home/history.json")

# Brand assets (favicon + logo) live alongside main.py in the image.
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FAVICON_PATH = os.path.join(BASE_DIR, "zippatchlings.ico")
LOGO_PATH = os.path.join(BASE_DIR, "zippatchlings.png")


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    if os.path.exists(FAVICON_PATH):
        return FileResponse(FAVICON_PATH, media_type="image/x-icon")
    return JSONResponse(status_code=404, content={"error": "not found"})


@app.get("/logo.png", include_in_schema=False)
def logo():
    if os.path.exists(LOGO_PATH):
        return FileResponse(LOGO_PATH, media_type="image/png")
    return JSONResponse(status_code=404, content={"error": "not found"})



class Payload(BaseModel):
    date: str
    messages: list[str]


def load_json(path):
    if os.path.exists(path):
        return json.load(open(path))
    return {}


def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)

def clean(text):
    text = re.sub(r'<.*?>', '', text)
    text = text.replace('&nbsp;', ' ').replace('&amp;', '&')
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


def parse_score(msg):
    msg = clean(msg)
    if not re.search(r'\d', msg):
        return None
        
    # --- Protections: Anti-Cheating Limits ---
    MIN_TOTAL_SCORE = 9
    MAX_SCORE = 1000000

    m = re.search(r'(\d+)\s*//\s*(\d+)', msg)
    if m:
        try:
            z, p = int(m.group(1)), int(m.group(2))
            if (z + p) < MIN_TOTAL_SCORE or z > MAX_SCORE or p > MAX_SCORE:
                return None
            return z, p
        except ValueError:
            return None
            
    m = re.match(r'^(\d+)$', msg)
    if m:
        try:
            z = int(m.group(1))
            if z < MIN_TOTAL_SCORE or z > MAX_SCORE:
                return None
            return z, 0
        except ValueError:
            return None
            
    return None


def get_player_day(history, day, player):
    if day not in history or player not in history[day]:
        return None
    v = history[day][player]
    if isinstance(v, dict):
        return v
    return {"zip": 0, "patch": 0, "total": v, "penalty": False}


def day_total_val(v):
    if isinstance(v, int):
        return v
    if isinstance(v, dict):
        return v.get("total", 0)
    return 0


def is_excused_entry(v):
    return isinstance(v, dict) and bool(v.get("excused"))


def excused_entry(kind=""):
    return {"zip": 0, "patch": 0, "total": 0, "penalty": False,
            "excused": True, "excuse_kind": kind or "Approved Time Away"}


def day_entries(history, day):
    """(player, total) pairs for a day, sorted best-first, excluding excused players.

    Excused days are stored with a total of 0, so every ranking/winner calculation has
    to drop them or an away player would silently win the day."""
    entries = [(p, day_total_val(v)) for p, v in history.get(day, {}).items()
               if not is_excused_entry(v)]
    entries.sort(key=lambda x: x[1])
    return entries


def esc(value):
    return html.escape(str(value), quote=True)


def name_key(name):
    return re.sub(r"\s+", " ", str(name or "")).strip().lower()


def canonical_player(name, state=None):
    """Map a hand-typed name onto the existing leaderboard spelling when possible."""
    cleaned = re.sub(r"\s+", " ", str(name or "")).strip()
    if state is None:
        state = load_json(STATE_FILE)
    key = cleaned.lower()
    for existing in state:
        if existing.lower() == key:
            return existing
    return cleaned


def process(payload):
    state = load_json(STATE_FILE)
    history = load_json(HISTORY_FILE)

    if payload.date in history:
        return {"status": "already_processed"}

    day_scores = {}
    participants = set(state.keys())

    for line in payload.messages:
        if ":" not in line:
            continue
        name, msg = line.split(":", 1)
        name = name.strip()
        parsed = parse_score(msg)
        if not parsed:
            continue
        z, p = parsed
        day_scores[name] = {"zip": z, "patch": p}
        participants.add(name)

    if not day_scores:
        return {"status": "no_scores_found"}

    # Players with an approved accommodation covering this date are excused instead of
    # penalized. Defined further down the module; resolved at call time.
    excused_keys = acc_excused_keys_for_date(payload.date)

    worst_zip = max(v["zip"] for v in day_scores.values())
    worst_patch = max(v["patch"] for v in day_scores.values())
    penalty_zip = worst_zip + 1
    penalty_patch = worst_patch + 1

    best_zip_player = min(day_scores.items(), key=lambda x: x[1]["zip"])[0]
    best_patch_player = min(day_scores.items(), key=lambda x: x[1]["patch"])[0]
    best_total_player = min(day_scores.items(), key=lambda x: x[1]["zip"] + x[1]["patch"])[0]

    history[payload.date] = {}

    for p in participants:
        if p not in state:
            state[p] = {
                "zip_total": 0, "patch_total": 0, "days": 0,
                "penalty_days": 0, "zip_wins": 0, "patch_wins": 0, "total_wins": 0
            }
        if "wins" in state[p] and "total_wins" not in state[p]:
            state[p]["total_wins"] = state[p].pop("wins")
        state[p].setdefault("zip_wins", 0)
        state[p].setdefault("patch_wins", 0)
        state[p].setdefault("total_wins", 0)

        if p in day_scores:
            z = day_scores[p]["zip"]
            pa = day_scores[p]["patch"]
            is_penalty = False
        elif name_key(p) in excused_keys:
            history[payload.date][p] = excused_entry(excused_keys[name_key(p)])
            continue
        else:
            z = penalty_zip
            pa = penalty_patch
            state[p]["penalty_days"] += 1
            is_penalty = True

        if p == best_zip_player:
            state[p]["zip_wins"] += 1
        if p == best_patch_player:
            state[p]["patch_wins"] += 1
        if p == best_total_player:
            state[p]["total_wins"] += 1

        state[p]["zip_total"] += z
        state[p]["patch_total"] += pa
        state[p]["days"] += 1
        history[payload.date][p] = {"zip": z, "patch": pa, "total": z + pa, "penalty": is_penalty}

    save_json(STATE_FILE, state)
    save_json(HISTORY_FILE, history)
    return {"status": "ok"}


@app.post("/ingest")
def ingest(payload: Payload):
    result = process(payload)
    caught_up = zs_catch_up()
    if caught_up:
        return {"status": result.get("status"), "result": result, "finalized": caught_up}
    return result


@app.get("/leaderboard")
def leaderboard():
    state = load_json(STATE_FILE)
    rows = []
    for name, d in state.items():
        total = d["zip_total"] + d["patch_total"]
        rows.append({
            "player": name, "total": total,
            "avg_zip": round(d["zip_total"] / d["days"], 2) if d["days"] > 0 else 0,
            "avg_patch": round(d["patch_total"] / d["days"], 2) if d["days"] > 0 else 0,
            "zip_wins": d.get("zip_wins", 0), "patch_wins": d.get("patch_wins", 0),
            "total_wins": d.get("total_wins", 0), "missed": d["penalty_days"], "days": d["days"]
        })
    return sorted(rows, key=lambda x: x["total"])


@app.get("/history")
def get_history():
    return load_json(HISTORY_FILE)


@app.post("/reset")
def reset():
    save_json(STATE_FILE, {})
    save_json(HISTORY_FILE, {})
    return {"status": "reset"}


@app.post("/adjust")
def adjust(player: str, add_zip: int = 0, add_patch: int = 0, add_days: int = 0, add_penalties: int = 0):
    state = load_json(STATE_FILE)
    if player not in state:
        return {"status": "player not found"}
    state[player]["zip_total"] += add_zip
    state[player]["patch_total"] += add_patch
    state[player]["days"] += add_days
    state[player]["penalty_days"] += add_penalties
    save_json(STATE_FILE, state)
    return {"status": "adjusted", "player": player}


# =========================================================================== #
# Zipscores collector / dedupe / next-day finalizer
# ---------------------------------------------------------------------------
# A Power Automate flow can only read ~50 Teams messages per call (no reliable
# pagination, no Graph access). So instead of pull+score-at-once, PA POSTs raw
# Teams message pages to /collect frequently. We buffer + dedupe them in SQLite,
# and a daily in-process job scores YESTERDAY's messages through the existing
# process()/parse_score() leaderboard logic (which also gives us the desired
# "hold scores back until the next day" behavior).
# =========================================================================== #

ZS_TIMEZONE = os.environ.get("ZS_TIMEZONE", "America/Chicago")
ZS_BUFFER_DB = os.environ.get("ZS_BUFFER_DB", "/home/zipscores_buffer.db")
ZS_COLLECTOR_TOKEN = os.environ.get("ZS_COLLECTOR_TOKEN", "")
ZS_RETENTION_DAYS = int(os.environ.get("ZS_RETENTION_DAYS", "30"))
ZS_FINALIZE_HOUR = int(os.environ.get("ZS_FINALIZE_HOUR", "2"))
ZS_FINALIZE_MINUTE = int(os.environ.get("ZS_FINALIZE_MINUTE", "10"))


def _zs_tz():
    if ZoneInfo is not None:
        try:
            return ZoneInfo(ZS_TIMEZONE)
        except Exception:
            pass
    return timezone.utc


_zs_db_lock = threading.Lock()
_zs_buffer_dir = os.path.dirname(os.path.abspath(ZS_BUFFER_DB))
if _zs_buffer_dir:
    os.makedirs(_zs_buffer_dir, exist_ok=True)
_zs_conn = sqlite3.connect(ZS_BUFFER_DB, check_same_thread=False)
_zs_conn.execute(
    """
    CREATE TABLE IF NOT EXISTS buffer (
        id           TEXT PRIMARY KEY,
        created_utc  TEXT,
        local_date   TEXT,
        display_name TEXT,
        content_raw  TEXT,
        message_type TEXT,
        inserted_at  TEXT
    )
    """
)
_zs_conn.execute("CREATE INDEX IF NOT EXISTS idx_buffer_local_date ON buffer(local_date)")
_zs_conn.commit()


# =========================================================================== #
# Accommodation requests + screenshot backfill
# ---------------------------------------------------------------------------
# Players heading out on PTO shouldn't have to keep Teams installed on their
# phone just to avoid the worst-score+1 penalty. They file an accommodation
# request; once the Games Commissioner approves it, every covered day is
# recorded as EXCUSED instead of penalized. They keep their promise by
# submitting screenshots of the games they played while away, which the
# commissioner reviews and backfills into the real scores.
# =========================================================================== #

ZS_COMMISSIONER_TOKEN = os.environ.get("ZS_COMMISSIONER_TOKEN", "")
ZS_PROOF_DIR = os.environ.get("ZS_PROOF_DIR", "/home/proofs")
ZS_MAX_PROOF_BYTES = int(os.environ.get("ZS_MAX_PROOF_BYTES", str(8 * 1024 * 1024)))

COMMISH_COOKIE = "zs_commish"

ACCOMMODATION_KINDS = [
    "Approved Time Away",
    "Vacation / PTO",
    "Sick Leave",
    "Business Travel",
    "Family Obligation",
    "Digital Detox",
    "Other",
]

# Magic-byte sniffing: never trust the uploaded filename or content-type header.
_IMAGE_SIGNATURES = [
    (b"\x89PNG\r\n\x1a\n", "image/png", ".png"),
    (b"\xff\xd8\xff", "image/jpeg", ".jpg"),
    (b"GIF87a", "image/gif", ".gif"),
    (b"GIF89a", "image/gif", ".gif"),
]

_zs_conn.execute(
    """
    CREATE TABLE IF NOT EXISTS accommodations (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        player        TEXT NOT NULL,
        player_key    TEXT NOT NULL,
        start_date    TEXT NOT NULL,
        end_date      TEXT NOT NULL,
        kind          TEXT NOT NULL,
        reason        TEXT,
        promise       INTEGER NOT NULL DEFAULT 0,
        signature     TEXT,
        status        TEXT NOT NULL DEFAULT 'pending',
        submitted_at  TEXT,
        decided_at    TEXT,
        decision_note TEXT
    )
    """
)
_zs_conn.execute("CREATE INDEX IF NOT EXISTS idx_acc_status ON accommodations(status)")
_zs_conn.execute("CREATE INDEX IF NOT EXISTS idx_acc_player ON accommodations(player_key)")
_zs_conn.execute(
    """
    CREATE TABLE IF NOT EXISTS proofs (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        player        TEXT NOT NULL,
        player_key    TEXT NOT NULL,
        play_date     TEXT NOT NULL,
        zip           INTEGER NOT NULL,
        patch         INTEGER NOT NULL,
        note          TEXT,
        image_name    TEXT,
        image_mime    TEXT,
        status        TEXT NOT NULL DEFAULT 'pending',
        submitted_at  TEXT,
        decided_at    TEXT,
        decision_note TEXT
    )
    """
)
_zs_conn.execute("CREATE INDEX IF NOT EXISTS idx_proof_status ON proofs(status)")
_zs_conn.commit()


def _acc_rows(sql, params=()):
    with _zs_db_lock:
        cur = _zs_conn.execute(sql, params)
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def _acc_write(sql, params=()):
    with _zs_db_lock:
        cur = _zs_conn.execute(sql, params)
        _zs_conn.commit()
        return cur


def today_local():
    return datetime.now(_zs_tz()).date()


def valid_date(value):
    try:
        return datetime.strptime(str(value).strip(), "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def date_range(start, end):
    days = []
    cur = start
    while cur <= end:
        days.append(cur.strftime("%Y-%m-%d"))
        cur += timedelta(days=1)
    return days


def acc_excused_keys_for_date(date_str):
    """{normalized player name: accommodation kind} approved to be away on date_str."""
    rows = _acc_rows(
        """
        SELECT player_key, kind FROM accommodations
        WHERE status = 'approved' AND start_date <= ? AND end_date >= ?
        """,
        (date_str, date_str),
    )
    return {r["player_key"]: r["kind"] for r in rows}


def acc_active_today():
    """Approved accommodations covering today, for the dashboard 'on leave' card."""
    today = today_local().strftime("%Y-%m-%d")
    return _acc_rows(
        """
        SELECT * FROM accommodations
        WHERE status = 'approved' AND start_date <= ? AND end_date >= ?
        ORDER BY end_date ASC
        """,
        (today, today),
    )


def _ensure_state_player(state, player):
    if player not in state:
        state[player] = {
            "zip_total": 0, "patch_total": 0, "days": 0,
            "penalty_days": 0, "zip_wins": 0, "patch_wins": 0, "total_wins": 0
        }
    entry = state[player]
    if "wins" in entry and "total_wins" not in entry:
        entry["total_wins"] = entry.pop("wins")
    for key in ("zip_total", "patch_total", "days", "penalty_days",
                "zip_wins", "patch_wins", "total_wins"):
        entry.setdefault(key, 0)
    return entry


def _day_winners(day):
    """Winners per category among the day's real posters (no penalty, no excuse)."""
    posters = {p: v for p, v in day.items()
               if isinstance(v, dict) and not v.get("penalty") and not v.get("excused")}
    winners = {"zip": set(), "patch": set(), "total": set()}
    if not posters:
        return winners
    for field in ("zip", "patch", "total"):
        best = min(v.get(field, 0) for v in posters.values())
        winners[field] = {p for p, v in posters.items() if v.get(field, 0) == best}
    return winners


def _apply_win_delta(state, before, after):
    for field, key in (("zip", "zip_wins"), ("patch", "patch_wins"), ("total", "total_wins")):
        for player in after[field] - before[field]:
            _ensure_state_player(state, player)[key] += 1
        for player in before[field] - after[field]:
            entry = _ensure_state_player(state, player)
            entry[key] = max(0, entry[key] - 1)


def excuse_recorded_days(player, start_date, end_date, kind):
    """Convert already-scored penalty days in range into excused days.

    Approval usually lands after the nightly finalizer has already stamped a
    penalty on those dates, so approving has to reach back and undo it."""
    state = load_json(STATE_FILE)
    history = load_json(HISTORY_FILE)
    player = canonical_player(player, state)
    changed = []

    start = valid_date(start_date)
    end = valid_date(end_date)
    if not start or not end:
        return changed

    for d in date_range(start, end):
        day = history.get(d)
        if not day or player not in day:
            continue
        entry = get_player_day(history, d, player)
        if entry is None or entry.get("excused") or not entry.get("penalty"):
            continue

        before = _day_winners(day)
        st = _ensure_state_player(state, player)
        st["zip_total"] -= entry.get("zip", 0)
        st["patch_total"] -= entry.get("patch", 0)
        st["days"] = max(0, st["days"] - 1)
        st["penalty_days"] = max(0, st["penalty_days"] - 1)
        day[player] = excused_entry(kind)
        _apply_win_delta(state, before, _day_winners(day))
        changed.append(d)

    if changed:
        save_json(STATE_FILE, state)
        save_json(HISTORY_FILE, history)
    return changed


def apply_backfill(player, date_str, zip_score, patch_score):
    """Write a verified screenshot score into history and reconcile the totals."""
    history = load_json(HISTORY_FILE)
    if date_str not in history:
        # Score the day from the buffer first so the other players still get their
        # normal treatment; otherwise this backfill would create a one-player day and
        # permanently block the catch-up finalizer for that date.
        try:
            zs_finalize(target_date=date_str)
        except Exception as exc:  # pragma: no cover - defensive
            print("[accommodation] finalize before backfill failed:", exc)
        history = load_json(HISTORY_FILE)

    state = load_json(STATE_FILE)
    player = canonical_player(player, state)
    day = history.setdefault(date_str, {})
    before = _day_winners(day)
    old = get_player_day(history, date_str, player)

    st = _ensure_state_player(state, player)
    if old is None or old.get("excused"):
        st["zip_total"] += zip_score
        st["patch_total"] += patch_score
        st["days"] += 1
    else:
        st["zip_total"] += zip_score - old.get("zip", 0)
        st["patch_total"] += patch_score - old.get("patch", 0)
        if old.get("penalty"):
            st["penalty_days"] = max(0, st["penalty_days"] - 1)

    day[player] = {"zip": zip_score, "patch": patch_score,
                   "total": zip_score + patch_score, "penalty": False, "backfilled": True}
    _apply_win_delta(state, before, _day_winners(day))

    save_json(STATE_FILE, state)
    save_json(HISTORY_FILE, history)
    return {"player": player, "date": date_str, "zip": zip_score, "patch": patch_score,
            "replaced": "excused" if (old or {}).get("excused")
            else "penalty" if (old or {}).get("penalty")
            else "score" if old else "none"}


def sniff_image(data):
    for signature, mime, ext in _IMAGE_SIGNATURES:
        if data.startswith(signature):
            return mime, ext
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp", ".webp"
    return None, None


def store_proof_image(data):
    os.makedirs(ZS_PROOF_DIR, exist_ok=True)
    mime, ext = sniff_image(data)
    if not mime:
        return None, None
    name = uuid.uuid4().hex + ext
    with open(os.path.join(ZS_PROOF_DIR, name), "wb") as fh:
        fh.write(data)
    return name, mime


def is_commissioner(request):
    """Empty token == open access, so a missing env var can never lock everyone out."""
    if not ZS_COMMISSIONER_TOKEN:
        return True
    supplied = request.cookies.get(COMMISH_COOKIE) or request.query_params.get("token")
    return supplied == ZS_COMMISSIONER_TOKEN


def known_players():
    return sorted(load_json(STATE_FILE).keys())


def _zs_extract_messages(payload):
    """Be liberal: accept a bare list, {"value":[...]}, or {"body":{"value":[...]}}."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        if isinstance(payload.get("value"), list):
            return payload["value"]
        body = payload.get("body")
        if isinstance(body, dict) and isinstance(body.get("value"), list):
            return body["value"]
        if isinstance(body, list):
            return body
        if "id" in payload and "createdDateTime" in payload:
            return [payload]
    return []


def _zs_upsert(messages):
    """Insert new messages (dedupe on Teams id). Returns # newly inserted."""
    tz = _zs_tz()
    now = datetime.now(timezone.utc).isoformat()
    new_count = 0
    with _zs_db_lock:
        for msg in messages:
            if not isinstance(msg, dict):
                continue
            msg_id = msg.get("id")
            created = msg.get("createdDateTime")
            if not msg_id or not created:
                continue
            try:
                created_dt = datetime.fromisoformat(str(created).replace("Z", "+00:00"))
            except ValueError:
                continue
            local_date = created_dt.astimezone(tz).strftime("%Y-%m-%d")
            name = ((msg.get("from") or {}).get("user") or {}).get("displayName")
            content = (msg.get("body") or {}).get("content", "")
            mtype = msg.get("messageType", "message")
            cur = _zs_conn.execute(
                """
                INSERT OR IGNORE INTO buffer
                    (id, created_utc, local_date, display_name, content_raw,
                     message_type, inserted_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (msg_id, created_dt.astimezone(timezone.utc).isoformat(),
                 local_date, name, content, mtype, now),
            )
            new_count += cur.rowcount
        _zs_conn.commit()
    return new_count


def _zs_prune():
    tz = _zs_tz()
    cutoff = (datetime.now(tz).date() - timedelta(days=ZS_RETENTION_DAYS)).strftime("%Y-%m-%d")
    with _zs_db_lock:
        cur = _zs_conn.execute("DELETE FROM buffer WHERE local_date < ?", (cutoff,))
        _zs_conn.commit()
    return cur.rowcount


def _zs_looks_score_ish(text):
    """Keep a line only if it looks like a score: contains '//' OR starts with a digit."""
    if "//" in text:
        return True
    if re.match(r"^\d", text.strip()):
        return True
    return False


def zs_finalize(target_date=None):
    """Score the buffered messages for target_date (default = yesterday local) through
    the existing process()/parse_score() logic. Idempotent: re-running a date that was
    already processed returns already_processed."""
    tz = _zs_tz()
    if not target_date:
        target_date = (datetime.now(tz).date() - timedelta(days=1)).strftime("%Y-%m-%d")

    with _zs_db_lock:
        rows = _zs_conn.execute(
            """
            SELECT display_name, content_raw, message_type
            FROM buffer
            WHERE local_date = ?
            ORDER BY created_utc ASC
            """,
            (target_date,),
        ).fetchall()

    lines = []
    for display_name, content_raw, mtype in rows:
        if mtype and mtype != "message":
            continue
        cleaned = clean(content_raw or "")
        if not cleaned or not _zs_looks_score_ish(cleaned):
            continue
        lines.append(f"{display_name or 'Unknown'}: {cleaned}")

    result = process(Payload(date=target_date, messages=lines))
    _zs_prune()
    return {"date": target_date, "count": len(lines), "result": result}


def zs_catch_up():
    """Finalize any buffered past days (strictly before today, local time) that are
    not yet present in history. Catches days the daily scheduler missed (container
    downtime, late-arriving messages). Safe to call often: process() is idempotent,
    so days already scored are skipped."""
    tz = _zs_tz()
    today_local = datetime.now(tz).date().strftime("%Y-%m-%d")
    history = load_json(HISTORY_FILE)
    with _zs_db_lock:
        rows = _zs_conn.execute(
            "SELECT DISTINCT local_date FROM buffer WHERE local_date < ?",
            (today_local,),
        ).fetchall()
    pending = sorted(d[0] for d in rows if d[0] and d[0] not in history)
    finalized = []
    for d in pending:
        res = zs_finalize(target_date=d)
        if res.get("result", {}).get("status") == "ok":
            finalized.append(d)
    return finalized


@app.post("/collect")
async def collect(request: Request):
    if ZS_COLLECTOR_TOKEN and request.headers.get("X-Token") != ZS_COLLECTOR_TOKEN:
        return JSONResponse(status_code=401, content={"error": "unauthorized"})
    try:
        payload = await request.json()
    except Exception as exc:
        return JSONResponse(status_code=400, content={"error": f"bad json: {exc}"})
    messages = _zs_extract_messages(payload)
    new_count = _zs_upsert(messages)
    caught_up = zs_catch_up()
    return {"received": len(messages), "new": new_count, "finalized": caught_up}


@app.post("/finalize")
def finalize(date: str = None):
    return zs_finalize(target_date=date)


@app.on_event("startup")
def _zs_start_scheduler():
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
    except Exception:
        print("[zipscores] APScheduler not available; daily finalizer disabled.")
        return
    scheduler = BackgroundScheduler(timezone=ZS_TIMEZONE)
    scheduler.add_job(
        lambda: zs_finalize(),
        "cron",
        hour=ZS_FINALIZE_HOUR,
        minute=ZS_FINALIZE_MINUTE,
        id="zs_daily_finalize",
        replace_existing=True,
    )
    scheduler.start()
    print(f"[zipscores] Daily finalizer scheduled at "
          f"{ZS_FINALIZE_HOUR:02d}:{ZS_FINALIZE_MINUTE:02d} {ZS_TIMEZONE}")


def compute_filtered_stats(history, filtered_days):
    players = set()
    for d in filtered_days:
        if d in history:
            for p in history[d]:
                players.add(p)

    results = {}
    for player in players:
        real_scores = []
        all_scores = []
        daily_ranks = []
        zip_wins = 0
        patch_wins = 0
        total_wins = 0
        penalty_count = 0
        penalty_dates = []
        away_count = 0
        away_dates = []

        for d in filtered_days:
            if d not in history:
                continue
            pdata = get_player_day(history, d, player)
            if pdata is None:
                continue
            if pdata.get("excused"):
                away_count += 1
                away_dates.append(d)
                continue

            all_scores.append(pdata)
            if not pdata.get("penalty", False):
                real_scores.append(pdata)
            else:
                penalty_count += 1
                penalty_dates.append(d)

            # Daily rank
            entries = day_entries(history, d)
            rank = next((i+1 for i, (pp, _) in enumerate(entries) if pp == player), len(entries))
            daily_ranks.append(rank)

            # Daily winners (only among non-penalty posters)
            actual_posters = {}
            for p2, v2 in history[d].items():
                pd2 = get_player_day(history, d, p2)
                if pd2 and not pd2.get("penalty", False) and not pd2.get("excused"):
                    actual_posters[p2] = pd2

            if actual_posters and not pdata.get("penalty", False):
                best_z = min(v["zip"] for v in actual_posters.values())
                best_p = min(v["patch"] for v in actual_posters.values())
                best_t = min(v["total"] for v in actual_posters.values())
                if pdata["zip"] == best_z:
                    zip_wins += 1
                if pdata["patch"] == best_p:
                    patch_wins += 1
                if pdata["total"] == best_t:
                    total_wins += 1

        if not all_scores:
            continue

        all_totals = [s["total"] for s in all_scores]
        real_zips = [s["zip"] for s in real_scores] if real_scores else [0]
        real_patches = [s["patch"] for s in real_scores] if real_scores else [0]
        real_totals = [s["total"] for s in real_scores] if real_scores else [0]

        zt = sum(s["zip"] for s in all_scores)
        pt = sum(s["patch"] for s in all_scores)
        active = len(all_scores)

        mean_t = sum(all_totals) / len(all_totals)
        variance = sum((x - mean_t) ** 2 for x in all_totals) / len(all_totals)
        std_dev = round(math.sqrt(variance), 1)

        posted = len(real_scores)
        participation = round(posted / active * 100, 1) if active > 0 else 0
        avg_rank = round(sum(daily_ranks) / len(daily_ranks), 1) if daily_ranks else 0
        podium = sum(1 for r in daily_ranks if r <= 3)

        last_place = 0
        close_calls = 0
        for d in filtered_days:
            pdata = get_player_day(history, d, player)
            if pdata is None or pdata.get("excused"):
                continue
            entries = day_entries(history, d)
            if entries and entries[-1][0] == player:
                last_place += 1
            if not pdata.get("penalty", False) and entries:
                best = entries[0][1]
                if 0 < pdata["total"] - best <= 5:
                    close_calls += 1

        streak = 0
        for d in reversed(filtered_days):
            pdata = get_player_day(history, d, player)
            if pdata and pdata.get("excused"):
                continue  # approved leave is neutral, it neither builds nor breaks a streak
            if pdata and not pdata.get("penalty", False):
                streak += 1
            else:
                break

        results[player] = {
            "zip_total": zt, "patch_total": pt, "total": zt + pt,
            "avg_zip": round(zt / active, 2) if active > 0 else 0,
            "avg_patch": round(pt / active, 2) if active > 0 else 0,
            "zip_wins": zip_wins, "patch_wins": patch_wins, "total_wins": total_wins,
            "missed": penalty_count, "days": active,
            "missed_dates": penalty_dates,
            "away": away_count, "away_dates": away_dates,
            "best_zip": min(real_zips), "worst_zip": max(real_zips),
            "best_patch": min(real_patches), "worst_patch": max(real_patches),
            "best_day": min(real_totals), "worst_day": max(real_totals),
            "consistency": std_dev, "participation": participation,
            "avg_rank": avg_rank, "podium": podium,
            "basement": last_place, "close_calls": close_calls, "streak": streak
        }

    return results


def filter_days_by_mode(all_sorted_days, mode):
    """Return the subset of days for a given mode (week/month/year/ytd/all),
    anchored to the most recent day in the data. Shared by the dashboard and
    the per-player history page so both stay in sync."""
    if not all_sorted_days:
        return []
    latest_day = all_sorted_days[-1]
    latest_dt = datetime.strptime(latest_day, "%Y-%m-%d")
    if mode == "week":
        week_start = latest_dt - timedelta(days=latest_dt.weekday())
        week_end = week_start + timedelta(days=6)
        fd = [d for d in all_sorted_days
              if week_start.strftime("%Y-%m-%d") <= d <= week_end.strftime("%Y-%m-%d")]
    elif mode == "month":
        fd = [d for d in all_sorted_days if d[:7] == latest_day[:7]]
    elif mode in ("year", "ytd"):
        fd = [d for d in all_sorted_days if d[:4] == latest_day[:4]]
    else:
        fd = list(all_sorted_days)
    if not fd:
        fd = list(all_sorted_days)
    return fd


@app.get("/", response_class=HTMLResponse)
def dashboard(mode: str = "week"):
    state = load_json(STATE_FILE)
    history = load_json(HISTORY_FILE)

    if not state or not history:
        return HTMLResponse(content='<html><body style="background:#1a1a2e;color:white;font-family:Arial;display:flex;justify-content:center;align-items:center;height:100vh;"><h1>No data yet</h1></body></html>')

    all_sorted_days = sorted(history.keys())
    if not all_sorted_days:
        return HTMLResponse(content='<html><body style="background:#1a1a2e;color:white;font-family:Arial;display:flex;justify-content:center;align-items:center;height:100vh;"><h1>No data yet</h1></body></html>')

    latest_day = all_sorted_days[-1]
    latest_dt = datetime.strptime(latest_day, "%Y-%m-%d")

    # Filter days by mode
    filtered_days = filter_days_by_mode(all_sorted_days, mode)
    colors = ["#4ecca3","#36a2eb","#e94560","#ff6384","#ffcd56","#9966ff","#ff9f40","#c9cbcf","#4bc0c0","#ff6633"]
    all_players = list(state.keys())
    player_colors = {p: colors[i % len(colors)] for i, p in enumerate(all_players)}

    # Compute filtered stats
    stats = compute_filtered_stats(history, filtered_days)

    rows = []
    for name, s in stats.items():
        rows.append({
            "player": name, "total": s["total"],
            "avg_zip": s["avg_zip"], "avg_patch": s["avg_patch"],
            "zip_wins": s["zip_wins"], "patch_wins": s["patch_wins"],
            "total_wins": s["total_wins"], "missed": s["missed"], "days": s["days"],
            "missed_dates": s.get("missed_dates", []),
            "away": s.get("away", 0), "away_dates": s.get("away_dates", [])
        })
    rows.sort(key=lambda x: x["total"])

    if not rows:
        return HTMLResponse(content='<html><body style="background:#1a1a2e;color:white;font-family:Arial;display:flex;justify-content:center;align-items:center;height:100vh;"><h1>No data for this period</h1></body></html>')

    # Movement
    movement = {}
    if len(filtered_days) >= 2:
        prev_rank = day_entries(history, filtered_days[-2])
        curr_rank = day_entries(history, filtered_days[-1])
        prev_pos = {p: i for i, (p, _) in enumerate(prev_rank)}
        curr_pos = {p: i for i, (p, _) in enumerate(curr_rank)}
        for p in curr_pos:
            if p in prev_pos:
                diff = prev_pos[p] - curr_pos[p]
                if diff > 0:
                    movement[p] = "▲ " + str(diff)
                elif diff < 0:
                    movement[p] = "▼ " + str(abs(diff))
                else:
                    movement[p] = "→"
            else:
                movement[p] = "NEW"

    # Daily highlights
    daily_winner = daily_loser = ""
    if filtered_days:
        items = day_entries(history, filtered_days[-1])
        if items:
            daily_winner = items[0][0] + " (" + str(items[0][1]) + ")"
            daily_loser = items[-1][0] + " (" + str(items[-1][1]) + ")"

    # Weekly winner/leader (always from all data)
    def get_week_range(date_str):
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        monday = dt - timedelta(days=dt.weekday())
        friday = monday + timedelta(days=4)
        return monday.strftime("%Y-%m-%d"), friday.strftime("%Y-%m-%d")

    def compute_weekly(start, end):
        totals = {}
        for d in all_sorted_days:
            if start <= d <= end and d in history:
                for p, total in day_entries(history, d):
                    totals[p] = totals.get(p, 0) + total
        return totals

    weekly_title = ""
    weekly_value = ""
    dow = latest_dt.weekday()
    mon, fri = get_week_range(latest_day)
    if dow >= 4:
        wtotals = compute_weekly(mon, fri)
        if wtotals:
            ww = min(wtotals.items(), key=lambda x: x[1])
            weekly_title = "Weekly Winner"
            weekly_value = ww[0] + " (" + str(ww[1]) + ")"
    else:
        wtotals = compute_weekly(mon, latest_day)
        if wtotals:
            wl = min(wtotals.items(), key=lambda x: x[1])
            weekly_title = "Weekly Leader"
            weekly_value = wl[0] + " (" + str(wl[1]) + ")"

    # Trash talk
    if len(rows) >= 2:
        winner = rows[0]["player"]
        loser = rows[-1]["player"]
        trash_options = [
            winner + " is absolutely cooking right now",
            loser + "... we need to talk",
            winner + " woke up and chose dominance",
            loser + " might want to uninstall LinkedIn",
            winner + " is ice cold under pressure",
            loser + " treating this like a speedrun in reverse",
            winner + " built different",
            "RIP " + loser + "'s leaderboard hopes",
            winner + " is rent free in everyone's head",
            loser + " is the reason we can't have nice things",
            winner + " didn't come to play, they came to slay",
            loser + " playing chess while everyone else plays checkers... badly",
            winner + " is on a different gravitational plane",
            "Somebody check on " + loser + ", they've flatlined",
            winner + " is the final boss of this leaderboard",
            loser + " brought a spoon to a gunfight",
            winner + " making it look criminally easy",
            loser + " is speedrunning the walk of shame",
            "The gap between " + winner + " and " + loser + " is a felony",
            winner + " has entered their villain era",
            loser + " might need a wellness check",
            winner + " is built like a final exam answer key",
            "Is " + loser + " ok? Asking for the whole group chat",
            winner + " could do this in their sleep, and probably did",
            loser + " is allergic to the top of the board",
            winner + " is just showing off at this point",
            loser + " contributing nothing but vibes",
            winner + " is the blueprint",
            "history will not be kind to " + loser,
            winner + " left no crumbs",
        ]
        trash_line = random.choice(trash_options)
    else:
        trash_line = "Need more data..."

    # Table JSON
    table_json_rows = []
    for i, r in enumerate(rows):
        table_json_rows.append({
            "rank": i+1, "player": r["player"], "total": r["total"],
            "avg_zip": r["avg_zip"], "avg_patch": r["avg_patch"],
            "zip_wins": r["zip_wins"], "patch_wins": r["patch_wins"],
            "total_wins": r["total_wins"], "missed": r["missed"],
            "missed_days": r.get("missed_dates", []),
            "away": r.get("away", 0), "away_days": r.get("away_dates", []),
            "days": r["days"], "move": movement.get(r["player"], "")
        })
    table_data_json = json.dumps(table_json_rows)

    # Chart data
    chart_labels = json.dumps([r["player"] for r in rows])
    chart_data_json = json.dumps([r["total"] for r in rows])
    chart_colors_json = json.dumps(["#4ecca3" if i==0 else "#e94560" if i==len(rows)-1 else "#36a2eb" for i in range(len(rows))])

    trend_labels = json.dumps(filtered_days[-14:])
    trend_datasets = []
    active_players = list(stats.keys())
    for idx, player in enumerate(active_players):
        points = []
        for day in filtered_days[-14:]:
            pdata = get_player_day(history, day, player)
            if pdata and pdata.get("excused"):
                pdata = None  # leave a gap rather than plotting a fake zero
            points.append(pdata["total"] if pdata else None)
        trend_datasets.append({"label": player, "data": points, "borderColor": colors[idx % len(colors)], "fill": False, "tension": 0.3})
    trend_json = json.dumps(trend_datasets)

    # Donut data
    def build_donut(field):
        labels, values, dcolors = [], [], []
        for idx, p in enumerate(active_players):
            val = stats[p].get(field, 0)
            if val > 0:
                labels.append(p); values.append(val); dcolors.append(colors[idx % len(colors)])
        return json.dumps(labels), json.dumps(values), json.dumps(dcolors), sum(values)

    zl, zd, zc, zt = build_donut("zip_wins")
    pl, pd, pc, pt_val = build_donut("patch_wins")
    tl, td, tc, tt_val = build_donut("total_wins")

    # Advanced stats HTML
    adv_html = ""
    for player in active_players:
        s = stats[player]
        c = player_colors.get(player, "#36a2eb")
        adv_html += '<div class="stat-card" style="border-top:3px solid ' + c + '">'
        adv_html += '<h4 style="color:' + c + '">' + player + '</h4>'
        adv_html += '<div class="stat-grid">'
        for label, val, clr in [
            ("Total Zip", s["zip_total"], ""),
            ("Total Patches", s["patch_total"], ""),
            ("Best Zip", s["best_zip"], "color:#4ecca3"),
            ("Worst Zip", s["worst_zip"], "color:#e94560"),
            ("Best Patches", s["best_patch"], "color:#4ecca3"),
            ("Worst Patches", s["worst_patch"], "color:#e94560"),
            ("Best Day", s["best_day"], "color:#4ecca3"),
            ("Worst Day", s["worst_day"], "color:#e94560"),
            ("Consistency", s["consistency"], ""),
            ("Participation", str(s["participation"]) + "%", ""),
            ("Avg Rank", s["avg_rank"], ""),
            ("Podium", s["podium"], ""),
            ("Last Place", s["basement"], ""),
            ("Close Calls", s["close_calls"], ""),
            ("Streak", s["streak"], ""),
            ("Approved Away", s.get("away", 0), "color:#9966ff"),
        ]:
            sa = ' style="' + clr + '"' if clr else ""
            adv_html += '<div class="sg"><span class="sl">' + str(label) + '</span><span class="sv"' + sa + '>' + str(val) + '</span></div>'
        adv_html += '</div></div>'

    # Weekly card
    weekly_html = ""
    if weekly_title:
        weekly_html = '<div class="card weekly"><h3>' + weekly_title + '</h3><div class="value">' + weekly_value + '</div></div>'

    # Approved leave card
    away_html = ""
    away_now = acc_active_today()
    if away_now:
        away_bits = [esc(a["player"]) + ' <span class="thru">thru ' + esc(a["end_date"]) + '</span>'
                     for a in away_now]
        away_html = ('<div class="card away"><h3>On Approved Leave</h3><div class="value">'
                     + ' &middot; '.join(away_bits) + '</div></div>')

    # Mode tabs
    modes = [("week", "Week"), ("month", "Month"), ("year", "Year"), ("ytd", "YTD"), ("all", "All Time")]
    mode_html = '<div class="mode-bar">'
    for m_key, m_label in modes:
        active_cls = " active" if m_key == mode else ""
        mode_html += '<a href="/?mode=' + m_key + '" class="mode-btn' + active_cls + '">' + m_label + '</a>'
    mode_html += '</div>'

    num_days = str(len(filtered_days))
    dw = daily_winner or "TBD"
    dl = daily_loser or "TBD"

    css = """<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:linear-gradient(135deg,#1a1a2e 0%,#16213e 50%,#0f3460 100%);color:#eee;font-family:'Segoe UI',Tahoma,Geneva,Verdana,sans-serif;min-height:100vh;padding:20px}
.container{max-width:1200px;margin:0 auto}
.brand{text-align:center;margin-bottom:10px}
.logo{max-width:200px;width:40%;height:auto;filter:drop-shadow(0 4px 12px rgba(0,0,0,.4))}
h1{text-align:center;font-size:2.5em;margin-bottom:5px;background:linear-gradient(90deg,#4ecca3,#36a2eb);-webkit-background-clip:text;-webkit-text-fill-color:transparent}
.subtitle{text-align:center;color:#888;margin-bottom:10px;font-style:italic}
.mode-bar{display:flex;justify-content:center;gap:10px;margin-bottom:30px;flex-wrap:wrap}
.mode-btn{background:rgba(255,255,255,.05);color:#888;padding:8px 20px;border-radius:20px;text-decoration:none;font-size:.9em;border:1px solid rgba(255,255,255,.1);transition:all .2s}
.mode-btn:hover{background:rgba(255,255,255,.1);color:#eee}
.mode-btn.active{background:rgba(78,204,163,.2);color:#4ecca3;border-color:#4ecca3}
.highlights{display:flex;gap:20px;margin-bottom:30px;flex-wrap:wrap;justify-content:center}
.card{background:rgba(255,255,255,.05);border-radius:15px;padding:20px 30px;text-align:center;min-width:200px;border:1px solid rgba(255,255,255,.1);backdrop-filter:blur(10px)}
.card.winner{border-color:#4ecca3}.card.loser{border-color:#e94560}.card.trash{border-color:#ffcd56;min-width:300px}.card.weekly{border-color:#9966ff}
.card.away{border-color:#36a2eb;min-width:260px}.card.away .value{color:#36a2eb;font-size:1.05em}
.card.away .thru{color:#888;font-size:.8em;font-weight:normal}
.nav-bar{display:flex;justify-content:center;gap:10px;margin-bottom:25px;flex-wrap:wrap}
.nav-btn{background:rgba(54,162,235,.12);color:#8fd0ff;padding:9px 18px;border-radius:10px;text-decoration:none;font-size:.85em;border:1px solid rgba(54,162,235,.35);transition:all .2s}
.nav-btn:hover{background:rgba(54,162,235,.25);color:#fff}
.nav-btn.ghost{background:rgba(255,255,255,.04);color:#888;border-color:rgba(255,255,255,.12)}
.nav-btn.ghost:hover{color:#eee}
.card h3{font-size:.9em;color:#888;margin-bottom:8px}.card .value{font-size:1.4em;font-weight:bold}
.card.winner .value{color:#4ecca3}.card.loser .value{color:#e94560}.card.trash .value{color:#ffcd56;font-size:1.1em}.card.weekly .value{color:#9966ff}
table{width:100%;border-collapse:collapse;margin-bottom:40px;background:rgba(255,255,255,.03);border-radius:15px;overflow:hidden}
th{background:rgba(78,204,163,.15);padding:14px 12px;text-align:left;font-size:.85em;color:#4ecca3;text-transform:uppercase;letter-spacing:1px;cursor:pointer;user-select:none;white-space:nowrap}
th:hover{background:rgba(78,204,163,.25)}
th .sort-arrow{margin-left:4px;font-size:.7em}
td{padding:12px;border-bottom:1px solid rgba(255,255,255,.05)}
tr:hover{background:rgba(255,255,255,.05)}
.player{font-weight:bold;font-size:1.1em}.total{font-weight:bold;color:#4ecca3;font-size:1.2em}.move{font-size:1.1em}.missed{color:#e94560;cursor:help;border-bottom:1px dotted #e94560}
.away-cell{color:#36a2eb;cursor:help;border-bottom:1px dotted #36a2eb}
.player-link{color:inherit;text-decoration:none;border-bottom:1px dotted rgba(255,255,255,.3)}.player-link:hover{color:#4ecca3;border-bottom-color:#4ecca3}
.donuts{display:flex;gap:20px;flex-wrap:wrap;justify-content:center;margin-bottom:40px}
.donut-box{background:rgba(255,255,255,.03);border-radius:15px;padding:20px;width:300px;position:relative}
.donut-box h3{text-align:center;margin-bottom:15px;color:#888}
.donut-center{position:absolute;top:55%;left:50%;transform:translate(-50%,-50%);text-align:center;pointer-events:none}
.donut-center .num{font-size:2em;font-weight:bold;color:#eee}.donut-center .label{font-size:.8em;color:#888}
.charts{display:flex;gap:30px;flex-wrap:wrap;justify-content:center;margin-bottom:40px}
.chart-box{background:rgba(255,255,255,.03);border-radius:15px;padding:20px;flex:1;min-width:400px;max-width:600px}
.chart-box h3{text-align:center;margin-bottom:15px;color:#888}
.adv-toggle{text-align:center;margin-bottom:20px}
.adv-toggle button{background:rgba(255,255,255,.08);border:1px solid rgba(255,255,255,.15);color:#ccc;padding:12px 30px;border-radius:10px;cursor:pointer;font-size:1em;transition:all .2s}
.adv-toggle button:hover{background:rgba(255,255,255,.15);color:#fff}
.adv-section{max-height:0;overflow:hidden;transition:max-height .5s ease-out;margin-bottom:40px}
.adv-section.open{max-height:8000px;transition:max-height .8s ease-in}
.adv-grid{display:flex;flex-wrap:wrap;gap:20px;justify-content:center}
.stat-card{background:rgba(255,255,255,.03);border-radius:15px;padding:20px;width:280px;border:1px solid rgba(255,255,255,.08)}
.stat-card h4{text-align:center;font-size:1.1em;margin-bottom:12px}
.stat-grid{display:grid;grid-template-columns:1fr 1fr;gap:8px}
.sg{display:flex;flex-direction:column;padding:6px 8px;background:rgba(255,255,255,.03);border-radius:8px}
.sl{font-size:.7em;color:#888;text-transform:uppercase}.sv{font-size:1.1em;font-weight:bold}
.footer{text-align:center;color:#555;padding:20px;font-size:.8em}
@media(max-width:768px){.highlights{flex-direction:column;align-items:center}.charts{flex-direction:column;align-items:center}.chart-box{min-width:100%}.donuts{flex-direction:column;align-items:center}.donut-box{width:100%;max-width:350px}table{font-size:.85em}.stat-card{width:100%}}
</style>"""

    js_template = """<script>
let tableData=__TABLE_DATA__;
let sortCol="total",sortAsc=true;
function renderTable(){
    const tbody=document.getElementById("tableBody");
    let html="";
    tableData.forEach((r,i)=>{
        const rank=i<3?["#1","#2","#3"][i]:"#"+(i+1);
        let missTitle="No missed days";
        if(r.missed>0&&r.missed_days&&r.missed_days.length){missTitle="Missed: "+r.missed_days.join(", ");}
        const mb=r.missed>0?'<span class="missed" title="'+missTitle+'">'+r.missed+'</span>':'0';
        let awayTitle="No approved leave";
        if(r.away>0&&r.away_days&&r.away_days.length){awayTitle="Approved leave: "+r.away_days.join(", ");}
        const ab=r.away>0?'<span class="away-cell" title="'+awayTitle+'">'+r.away+'</span>':'0';
        const pname="<a class='player-link' href='/player?name="+encodeURIComponent(r.player)+"&mode=__MODE__'>"+r.player+"</a>";
        html+="<tr><td>"+rank+"</td><td class='player'>"+pname+"</td><td class='total'>"+r.total+"</td><td>"+r.avg_zip+"</td><td>"+r.avg_patch+"</td><td>Z:"+r.zip_wins+" P:"+r.patch_wins+" T:"+r.total_wins+"</td><td>"+mb+"</td><td>"+ab+"</td><td>"+r.days+"</td><td class='move'>"+r.move+"</td></tr>";
    });
    tbody.innerHTML=html;
}
document.querySelectorAll("#mainTable th").forEach(th=>{
    th.addEventListener("click",()=>{
        const col=th.dataset.col,type=th.dataset.type;
        if(sortCol===col)sortAsc=!sortAsc;
        else{sortCol=col;sortAsc=true;}
        tableData.sort((a,b)=>{
            let va=a[col],vb=b[col];
            if(type==="num")return sortAsc?va-vb:vb-va;
            va=String(va);vb=String(vb);
            return sortAsc?va.localeCompare(vb):vb.localeCompare(va);
        });
        document.querySelectorAll(".sort-arrow").forEach(s=>s.textContent="");
        th.querySelector(".sort-arrow").textContent=sortAsc?" \u25b2":" \u25bc";
        renderTable();
    });
});
renderTable();
function toggleAdv(){document.getElementById("advSection").classList.toggle("open");}
const donutOpts={cutout:'65%',plugins:{legend:{position:'bottom',labels:{color:'#888',padding:12,font:{size:11}}}}};
new Chart(document.getElementById('zipDonut'),{type:'doughnut',data:{labels:__ZL__,datasets:[{data:__ZD__,backgroundColor:__ZC__,borderWidth:0,spacing:3}]},options:donutOpts});
new Chart(document.getElementById('patchDonut'),{type:'doughnut',data:{labels:__PL__,datasets:[{data:__PD__,backgroundColor:__PC__,borderWidth:0,spacing:3}]},options:donutOpts});
new Chart(document.getElementById('totalDonut'),{type:'doughnut',data:{labels:__TL__,datasets:[{data:__TD__,backgroundColor:__TC__,borderWidth:0,spacing:3}]},options:donutOpts});
new Chart(document.getElementById('barChart'),{type:'bar',data:{labels:__CHART_LABELS__,datasets:[{data:__CHART_DATA__,backgroundColor:__CHART_COLORS__,borderRadius:8}]},options:{plugins:{legend:{display:false}},scales:{y:{ticks:{color:'#888'},grid:{color:'rgba(255,255,255,0.05)'}},x:{ticks:{color:'#888'},grid:{display:false}}}}});
new Chart(document.getElementById('trendChart'),{type:'line',data:{labels:__TREND_LABELS__,datasets:__TREND_DATASETS__},options:{plugins:{legend:{labels:{color:'#888'}}},scales:{y:{ticks:{color:'#888'},grid:{color:'rgba(255,255,255,0.05)'}},x:{ticks:{color:'#888'},grid:{display:false}}},spanGaps:true}});
</script>"""

    js = js_template.replace("__TABLE_DATA__", table_data_json)
    js = js.replace("__MODE__", mode)
    js = js.replace("__ZL__", zl).replace("__ZD__", zd).replace("__ZC__", zc)
    js = js.replace("__PL__", pl).replace("__PD__", pd).replace("__PC__", pc)
    js = js.replace("__TL__", tl).replace("__TD__", td).replace("__TC__", tc)
    js = js.replace("__CHART_LABELS__", chart_labels).replace("__CHART_DATA__", chart_data_json).replace("__CHART_COLORS__", chart_colors_json)
    js = js.replace("__TREND_LABELS__", trend_labels).replace("__TREND_DATASETS__", trend_json)

    html = '<!DOCTYPE html><html><head><title>Zip Patchlings</title><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="refresh" content="300"><link rel="icon" type="image/x-icon" href="/favicon.ico"><script src="https://cdn.jsdelivr.net/npm/chart.js"></script>'
    html += css
    html += '</head><body><div class="container">'
    html += '<div class="brand"><img src="/logo.png" alt="Zip Patchlings" class="logo"></div>'
    html += '<h1>Zip Patchlings</h1>'
    html += '<p class="subtitle">Consistency beats talent. Miss a day? Pay the price.</p>'
    html += mode_html
    html += ('<div class="nav-bar">'
             '<a class="nav-btn" href="/accommodation">Request Time Away</a>'
             '<a class="nav-btn" href="/backfill">Submit Score Proof</a>'
             '<a class="nav-btn ghost" href="/accommodations">Leave Board</a>'
             '</div>')
    html += '<div class="highlights">'
    html += '<div class="card winner"><h3>Latest Winner</h3><div class="value">' + dw + '</div></div>'
    html += '<div class="card loser"><h3>Rough Day</h3><div class="value">' + dl + '</div></div>'
    html += weekly_html
    html += away_html
    html += '<div class="card trash"><h3>Commentary</h3><div class="value">' + trash_line + '</div></div>'
    html += '</div>'
    html += '<table id="mainTable"><thead><tr>'
    for col, dtype, label in [("rank","num","Rank"),("player","str","Player"),("total","num","Total"),("avg_zip","num","Avg Zip"),("avg_patch","num","Avg Patch"),("total_wins","num","Wins"),("missed","num","Missed"),("away","num","Away"),("days","num","Days"),("move","str","Move")]:
        html += '<th data-col="' + col + '" data-type="' + dtype + '">' + label + '<span class="sort-arrow"></span></th>'
    html += '</tr></thead><tbody id="tableBody"></tbody></table>'
    html += '<div class="donuts">'
    html += '<div class="donut-box"><h3>Zip Wins</h3><canvas id="zipDonut"></canvas><div class="donut-center"><div class="num">' + str(zt) + '</div><div class="label">wins</div></div></div>'
    html += '<div class="donut-box"><h3>Patches Wins</h3><canvas id="patchDonut"></canvas><div class="donut-center"><div class="num">' + str(pt_val) + '</div><div class="label">wins</div></div></div>'
    html += '<div class="donut-box"><h3>Total Wins</h3><canvas id="totalDonut"></canvas><div class="donut-center"><div class="num">' + str(tt_val) + '</div><div class="label">wins</div></div></div>'
    html += '</div>'
    html += '<div class="adv-toggle"><button onclick="toggleAdv()">Advanced Stats (click to expand)</button></div>'
    html += '<div class="adv-section" id="advSection"><div class="adv-grid">' + adv_html + '</div></div>'
    html += '<div class="charts">'
    html += '<div class="chart-box"><h3>Total Scores (lower = better)</h3><canvas id="barChart"></canvas></div>'
    html += '<div class="chart-box"><h3>Daily Trend</h3><canvas id="trendChart"></canvas></div>'
    html += '</div>'
    html += '<div class="footer">Auto-refreshes every 5 min | ' + mode.upper() + ' view | Data from ' + num_days + ' day(s)</div>'
    html += '</div>'
    html += js
    html += '</body></html>'

    return HTMLResponse(content=html)


@app.get("/player", response_class=HTMLResponse)
def player_history(name: str, mode: str = "week"):
    state = load_json(STATE_FILE)
    history = load_json(HISTORY_FILE)

    def _shell(body):
        return ('<!DOCTYPE html><html><head><title>' + esc(name) + ' - Zip Patchlings</title>'
                '<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
                '<link rel="icon" type="image/x-icon" href="/favicon.ico">'
                '<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>' + PLAYER_CSS +
                '</head><body><div class="container">'
                '<div class="brand"><a href="/"><img src="/logo.png" alt="Zip Patchlings" class="logo"></a></div>'
                + body + '</div></body></html>')

    if not history:
        return HTMLResponse(content=_shell('<h1>' + esc(name) + '</h1><p class="subtitle">No data yet</p><a class="back" href="/">&larr; Back to leaderboard</a>'))

    all_sorted_days = sorted(history.keys())
    filtered_days = filter_days_by_mode(all_sorted_days, mode)

    # Per-day rows for this player
    day_rows = []
    totals = []
    labels = []
    for d in filtered_days:
        pdata = get_player_day(history, d, name)
        if not pdata:
            continue
        excused = bool(pdata.get("excused"))
        rank = None
        if not excused:
            ranking = day_entries(history, d)
            rank = next((i + 1 for i, (p, _) in enumerate(ranking) if p == name), None)
            field = len(ranking)
            labels.append(d)
            totals.append(pdata.get("total", 0))
        else:
            field = 0
        day_rows.append({
            "date": d, "zip": pdata.get("zip", 0), "patch": pdata.get("patch", 0),
            "total": pdata.get("total", 0), "rank": rank,
            "penalty": pdata.get("penalty", False), "field": field,
            "excused": excused, "excuse_kind": pdata.get("excuse_kind", ""),
            "backfilled": bool(pdata.get("backfilled")),
        })

    if not day_rows:
        return HTMLResponse(content=_shell('<h1>' + esc(name) + '</h1><p class="subtitle">No data for this period</p>' + _player_mode_bar(name, mode) + '<a class="back" href="/">&larr; Back to leaderboard</a>'))

    stats = compute_filtered_stats(history, filtered_days).get(name, {})

    posted = sum(1 for r in day_rows if not r["penalty"] and not r["excused"])
    penalty = sum(1 for r in day_rows if r["penalty"])
    away = sum(1 for r in day_rows if r["excused"])

    # Summary cards
    cards = [
        ("Total", stats.get("total", "-")),
        ("Avg Zip", stats.get("avg_zip", "-")),
        ("Avg Patch", stats.get("avg_patch", "-")),
        ("Days Posted", posted),
        ("Penalty Days", penalty),
        ("Approved Away", away),
        ("Participation", str(stats.get("participation", 0)) + "%"),
        ("Avg Rank", stats.get("avg_rank", "-")),
        ("Best Day", stats.get("best_day", "-")),
        ("Worst Day", stats.get("worst_day", "-")),
        ("Streak", stats.get("streak", "-")),
    ]
    cards_html = '<div class="pcards">'
    for label, val in cards:
        cards_html += '<div class="pcard"><div class="pl">' + label + '</div><div class="pv">' + str(val) + '</div></div>'
    cards_html += '</div>'

    # Per-day table (most recent first)
    table_html = ('<table class="phist"><thead><tr><th>Date</th><th>Zip</th><th>Patches</th>'
                  '<th>Total</th><th>Daily Rank</th><th>Status</th></tr></thead><tbody>')
    for r in reversed(day_rows):
        if r["excused"]:
            cls = ' class="exc-row"'
            rank_txt = '-'
            status = ('<span class="exc-tag" title="' + esc(r["excuse_kind"] or "Approved Time Away")
                      + '">EXCUSED</span>')
            zip_txt = patch_txt = total_txt = '&mdash;'
        else:
            cls = ' class="pen-row"' if r["penalty"] else ''
            rank_txt = ('#' + str(r["rank"]) + ' / ' + str(r["field"])) if r["rank"] else '-'
            if r["penalty"]:
                status = '<span class="pen-tag">PENALTY</span>'
            elif r["backfilled"]:
                status = '<span class="bf-tag">BACKFILLED</span>'
            else:
                status = '<span class="ok-tag">posted</span>'
            zip_txt = str(r["zip"])
            patch_txt = str(r["patch"])
            total_txt = str(r["total"])
        table_html += ('<tr' + cls + '><td>' + r["date"] + '</td><td>' + zip_txt +
                       '</td><td>' + patch_txt + '</td><td class="total">' + total_txt +
                       '</td><td>' + rank_txt + '</td><td>' + status + '</td></tr>')
    table_html += '</tbody></table>'

    # Trend chart (excused days are omitted, so point colors must match that subset)
    plotted = [r for r in day_rows if not r["excused"]]
    chart_labels = json.dumps(labels)
    chart_totals = json.dumps(totals)
    point_colors = json.dumps(["#e94560" if r["penalty"] else "#4ecca3" for r in plotted])
    chart_js = ('<script>new Chart(document.getElementById("ptrend"),{type:"line",data:{labels:' +
                chart_labels + ',datasets:[{label:"Daily Total",data:' + chart_totals +
                ',borderColor:"#36a2eb",pointBackgroundColor:' + point_colors +
                ',pointRadius:5,fill:false,tension:0.3}]},options:{plugins:{legend:{display:false}},'
                'scales:{y:{ticks:{color:"#888"},grid:{color:"rgba(255,255,255,0.05)"}},'
                'x:{ticks:{color:"#888"},grid:{display:false}}}});</script>')

    body = ('<a class="back" href="/?mode=' + mode + '">&larr; Back to leaderboard</a>'
            '<h1>' + esc(name) + '</h1>'
            '<p class="subtitle">Penalty days (highlighted) inherit the day\'s worst score +1. '
            'Approved leave is excused and costs nothing.</p>' +
            _player_mode_bar(name, mode) +
            '<div class="nav-bar">'
            '<a class="nav-btn" href="/accommodation?player=' + quote(name) + '">Request Time Away</a>'
            '<a class="nav-btn" href="/backfill?player=' + quote(name) + '">Submit Score Proof</a>'
            '</div>' + cards_html +
            '<div class="chart-box"><h3>Daily Total Trend</h3><canvas id="ptrend"></canvas></div>' +
            table_html + chart_js)

    return HTMLResponse(content=_shell(body))


def _player_mode_bar(name, mode):
    modes = [("week", "Week"), ("month", "Month"), ("year", "Year"), ("ytd", "YTD"), ("all", "All Time")]
    bar = '<div class="mode-bar">'
    for m_key, m_label in modes:
        active_cls = " active" if m_key == mode else ""
        bar += '<a href="/player?name=' + quote(name) + '&mode=' + m_key + '" class="mode-btn' + active_cls + '">' + m_label + '</a>'
    bar += '</div>'
    return bar


PLAYER_CSS = """<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:linear-gradient(135deg,#1a1a2e 0%,#16213e 50%,#0f3460 100%);color:#eee;font-family:'Segoe UI',Tahoma,Geneva,Verdana,sans-serif;min-height:100vh;padding:20px}
.container{max-width:1000px;margin:0 auto}
.brand{text-align:center;margin-bottom:10px}
.logo{max-width:160px;width:35%;height:auto;filter:drop-shadow(0 4px 12px rgba(0,0,0,.4))}
h1{text-align:center;font-size:2.3em;margin-bottom:5px;background:linear-gradient(90deg,#4ecca3,#36a2eb);-webkit-background-clip:text;-webkit-text-fill-color:transparent}
.subtitle{text-align:center;color:#888;margin-bottom:15px;font-style:italic;font-size:.9em}
.back{display:inline-block;color:#36a2eb;text-decoration:none;margin-bottom:10px;font-size:.9em}
.back:hover{color:#4ecca3}
.mode-bar{display:flex;justify-content:center;gap:10px;margin-bottom:25px;flex-wrap:wrap}
.mode-btn{background:rgba(255,255,255,.05);color:#888;padding:8px 20px;border-radius:20px;text-decoration:none;font-size:.9em;border:1px solid rgba(255,255,255,.1);transition:all .2s}
.mode-btn:hover{background:rgba(255,255,255,.1);color:#eee}
.mode-btn.active{background:rgba(78,204,163,.2);color:#4ecca3;border-color:#4ecca3}
.pcards{display:flex;flex-wrap:wrap;gap:12px;justify-content:center;margin-bottom:30px}
.pcard{background:rgba(255,255,255,.05);border-radius:12px;padding:14px 18px;text-align:center;min-width:120px;border:1px solid rgba(255,255,255,.1)}
.pl{font-size:.7em;color:#888;text-transform:uppercase;letter-spacing:1px;margin-bottom:6px}
.pv{font-size:1.4em;font-weight:bold;color:#4ecca3}
.chart-box{background:rgba(255,255,255,.03);border-radius:15px;padding:20px;margin-bottom:30px}
.chart-box h3{text-align:center;margin-bottom:15px;color:#888}
table.phist{width:100%;border-collapse:collapse;margin-bottom:40px;background:rgba(255,255,255,.03);border-radius:15px;overflow:hidden}
.phist th{background:rgba(78,204,163,.15);padding:14px 12px;text-align:left;font-size:.85em;color:#4ecca3;text-transform:uppercase;letter-spacing:1px}
.phist td{padding:12px;border-bottom:1px solid rgba(255,255,255,.05)}
.phist tr:hover{background:rgba(255,255,255,.05)}
.phist .total{font-weight:bold;color:#4ecca3}
.pen-row{background:rgba(233,69,96,.12)}
.pen-row:hover{background:rgba(233,69,96,.2)}
.pen-tag{background:#e94560;color:#fff;padding:2px 8px;border-radius:6px;font-size:.75em;font-weight:bold}
.exc-row{background:rgba(54,162,235,.1)}
.exc-row:hover{background:rgba(54,162,235,.18)}
.exc-tag{background:#36a2eb;color:#fff;padding:2px 8px;border-radius:6px;font-size:.75em;font-weight:bold;cursor:help}
.bf-tag{background:#9966ff;color:#fff;padding:2px 8px;border-radius:6px;font-size:.75em;font-weight:bold}
.nav-bar{display:flex;justify-content:center;gap:10px;margin-bottom:22px;flex-wrap:wrap}
.nav-btn{background:rgba(54,162,235,.12);color:#8fd0ff;padding:9px 18px;border-radius:10px;text-decoration:none;font-size:.85em;border:1px solid rgba(54,162,235,.35);transition:all .2s}
.nav-btn:hover{background:rgba(54,162,235,.25);color:#fff}
.ok-tag{color:#4ecca3;font-size:.85em}
@media(max-width:768px){.pcards{gap:8px}.pcard{min-width:90px;padding:10px 12px}table.phist{font-size:.85em}}
</style>"""


# =========================================================================== #
# Accommodation + backfill pages
# =========================================================================== #

FORM_CSS = """<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:linear-gradient(135deg,#1a1a2e 0%,#16213e 50%,#0f3460 100%);color:#eee;font-family:'Segoe UI',Tahoma,Geneva,Verdana,sans-serif;min-height:100vh;padding:20px}
.container{max-width:760px;margin:0 auto}
.wide{max-width:1000px}
.brand{text-align:center;margin-bottom:8px}
.logo{max-width:150px;width:32%;height:auto;filter:drop-shadow(0 4px 12px rgba(0,0,0,.4))}
h1{text-align:center;font-size:2.1em;margin-bottom:6px;background:linear-gradient(90deg,#4ecca3,#36a2eb);-webkit-background-clip:text;-webkit-text-fill-color:transparent}
.ribbon{display:block;width:fit-content;margin:0 auto 10px;background:linear-gradient(90deg,#36a2eb,#4ecca3);color:#08111f;font-weight:bold;letter-spacing:2px;text-transform:uppercase;font-size:.78em;padding:6px 22px;border-radius:6px}
.subtitle{text-align:center;color:#9aa4b2;margin-bottom:20px;font-size:.92em}
.back{display:inline-block;color:#36a2eb;text-decoration:none;margin-bottom:12px;font-size:.9em}
.back:hover{color:#4ecca3}
.panel{background:rgba(255,255,255,.05);border:1px solid rgba(255,255,255,.1);border-radius:16px;padding:24px;margin-bottom:24px;backdrop-filter:blur(10px)}
.field{margin-bottom:16px}
.field label{display:block;font-size:.72em;letter-spacing:1.4px;text-transform:uppercase;color:#8fd0ff;margin-bottom:6px;font-weight:bold}
.field .hint{display:block;font-size:.78em;color:#7a8494;margin-top:5px;text-transform:none;letter-spacing:0}
input[type=text],input[type=date],input[type=number],input[type=password],select,textarea{width:100%;background:rgba(10,18,34,.75);border:1px solid rgba(54,162,235,.45);border-radius:10px;color:#eee;padding:11px 13px;font-size:1em;font-family:inherit}
input:focus,select:focus,textarea:focus{outline:none;border-color:#4ecca3;box-shadow:0 0 0 2px rgba(78,204,163,.2)}
input[type=file]{width:100%;color:#bbb;font-size:.9em;padding:10px 0}
textarea{min-height:84px;resize:vertical}
.row{display:flex;gap:16px;flex-wrap:wrap}
.row .field{flex:1;min-width:170px}
.promise{border:2px dashed rgba(78,204,163,.55);border-radius:14px;padding:16px 18px;margin-bottom:18px;background:rgba(78,204,163,.06)}
.promise h3{display:inline-block;background:#4ecca3;color:#0b2018;font-size:.72em;letter-spacing:1.4px;text-transform:uppercase;padding:4px 12px;border-radius:6px;margin-bottom:10px}
.promise label{display:flex;gap:10px;align-items:flex-start;font-size:.92em;color:#dfe6ee;cursor:pointer}
.promise input[type=checkbox]{margin-top:3px;width:18px;height:18px;accent-color:#4ecca3;flex-shrink:0}
.bow{text-align:center;color:#8fd0ff;font-size:.88em;margin:18px 0 14px}
.bow strong{color:#4ecca3}
button.submit{width:100%;background:linear-gradient(90deg,#36a2eb,#4ecca3);color:#08111f;border:none;border-radius:12px;padding:14px;font-size:1.05em;font-weight:bold;cursor:pointer;letter-spacing:.5px;transition:filter .2s}
button.submit:hover{filter:brightness(1.12)}
button.mini{background:rgba(255,255,255,.08);border:1px solid rgba(255,255,255,.2);color:#ddd;border-radius:8px;padding:8px 16px;font-size:.85em;cursor:pointer}
button.mini:hover{background:rgba(255,255,255,.16);color:#fff}
button.approve{background:rgba(78,204,163,.18);border-color:#4ecca3;color:#4ecca3}
button.approve:hover{background:rgba(78,204,163,.32)}
button.deny{background:rgba(233,69,96,.15);border-color:#e94560;color:#ff8ba0}
button.deny:hover{background:rgba(233,69,96,.3)}
.banner{border-radius:12px;padding:13px 16px;margin-bottom:20px;font-size:.92em}
.banner.ok{background:rgba(78,204,163,.14);border:1px solid #4ecca3;color:#9df0d3}
.banner.err{background:rgba(233,69,96,.14);border:1px solid #e94560;color:#ffb3c0}
.banner.warn{background:rgba(255,205,86,.12);border:1px solid #ffcd56;color:#ffe6a8}
.tag{display:inline-block;padding:3px 10px;border-radius:6px;font-size:.72em;font-weight:bold;text-transform:uppercase;letter-spacing:1px}
.tag.pending{background:rgba(255,205,86,.18);color:#ffcd56}
.tag.approved{background:rgba(78,204,163,.18);color:#4ecca3}
.tag.denied{background:rgba(233,69,96,.18);color:#ff8ba0}
.tag.applied{background:rgba(153,102,255,.2);color:#c4a6ff}
.tag.rejected{background:rgba(233,69,96,.18);color:#ff8ba0}
table.board{width:100%;border-collapse:collapse;background:rgba(255,255,255,.03);border-radius:14px;overflow:hidden}
.board th{background:rgba(78,204,163,.15);padding:12px;text-align:left;font-size:.75em;color:#4ecca3;text-transform:uppercase;letter-spacing:1px}
.board td{padding:12px;border-bottom:1px solid rgba(255,255,255,.05);font-size:.92em;vertical-align:top}
.board tr:hover{background:rgba(255,255,255,.04)}
.muted{color:#7a8494;font-size:.85em}
.review{display:flex;gap:18px;flex-wrap:wrap;align-items:flex-start;border-bottom:1px solid rgba(255,255,255,.08);padding-bottom:20px;margin-bottom:20px}
.review:last-child{border-bottom:none;margin-bottom:0;padding-bottom:0}
.review .shot{flex:0 0 220px}
.review .shot img{width:100%;border-radius:10px;border:1px solid rgba(255,255,255,.15)}
.review .meta{flex:1;min-width:250px}
.review h4{font-size:1.05em;margin-bottom:6px;color:#8fd0ff}
.section-title{font-size:.8em;text-transform:uppercase;letter-spacing:2px;color:#8fd0ff;margin-bottom:14px;font-weight:bold}
.nav-bar{display:flex;justify-content:center;gap:10px;margin-bottom:22px;flex-wrap:wrap}
.nav-btn{background:rgba(54,162,235,.12);color:#8fd0ff;padding:9px 18px;border-radius:10px;text-decoration:none;font-size:.85em;border:1px solid rgba(54,162,235,.35);transition:all .2s}
.nav-btn:hover{background:rgba(54,162,235,.25);color:#fff}
.nav-btn.ghost{background:rgba(255,255,255,.04);color:#888;border-color:rgba(255,255,255,.12)}
.nav-btn.ghost:hover{color:#eee}
.empty{color:#7a8494;font-style:italic;font-size:.92em}
.inline{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-top:10px}
.inline input[type=number]{width:90px}
.inline input[type=text]{flex:1;min-width:160px}
.footer{text-align:center;color:#555;padding:20px;font-size:.8em}
@media(max-width:600px){.row{flex-direction:column;gap:0}.review .shot{flex:1 1 100%}}
</style>"""


def form_page(title, body, ribbon="", subtitle="", wide=False):
    head = ('<!DOCTYPE html><html><head><title>' + esc(title) + ' - Zip Patchlings</title>'
            '<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<link rel="icon" type="image/x-icon" href="/favicon.ico">' + FORM_CSS + '</head><body>'
            '<div class="container' + (' wide' if wide else '') + '">'
            '<div class="brand"><a href="/"><img src="/logo.png" alt="Zip Patchlings" class="logo"></a></div>'
            '<h1>Zip Patchlings</h1>')
    if ribbon:
        head += '<span class="ribbon">' + esc(ribbon) + '</span>'
    if subtitle:
        head += '<p class="subtitle">' + subtitle + '</p>'
    return HTMLResponse(content=head + body + '</div></body></html>')


def player_datalist(selected=""):
    options = ''.join('<option value="' + esc(p) + '"></option>' for p in known_players())
    return '<datalist id="players">' + options + '</datalist>'


def _banner(kind, message):
    return '<div class="banner ' + kind + '">' + message + '</div>' if message else ''


@app.get("/accommodation", response_class=HTMLResponse)
def accommodation_form(player: str = "", error: str = "", submitted: int = 0):
    banner = ""
    if submitted:
        banner = _banner("ok",
                         "Request filed. The Games Commissioner has been summoned. "
                         'Track it on the <a class="back" href="/accommodations">leave board</a>.')
    elif error:
        banner = _banner("err", esc(error))

    today = today_local().strftime("%Y-%m-%d")
    kinds = ''.join('<option value="' + esc(k) + '">' + esc(k) + '</option>'
                    for k in ACCOMMODATION_KINDS)

    body = (
        '<a class="back" href="/">&larr; Back to leaderboard</a>' + banner +
        '<div class="panel"><form method="post" action="/accommodation">'
        '<p class="subtitle" style="margin-bottom:20px">For taking an approved accommodation '
        'from the Zip Patchlings Group. Approved days are excused &mdash; no penalty score, '
        'no missed day.</p>'
        '<div class="field"><label>Player name</label>'
        '<input type="text" name="player" list="players" required maxlength="80" value="'
        + esc(player) + '" placeholder="Exactly as it appears on the leaderboard">'
        + player_datalist() +
        '<span class="hint">Match your leaderboard spelling or the excuse will not attach to you.</span>'
        '</div>'
        '<div class="row">'
        '<div class="field"><label>First day away</label>'
        '<input type="date" name="start_date" required value="' + today + '"></div>'
        '<div class="field"><label>Last day away</label>'
        '<input type="date" name="end_date" required value="' + today + '"></div>'
        '</div>'
        '<div class="field"><label>Accommodation type</label>'
        '<select name="kind">' + kinds + '</select></div>'
        '<div class="field"><label>Reason (optional)</label>'
        '<textarea name="reason" maxlength="500" placeholder="Traveling, adventuring, and living my best life"></textarea></div>'
        '<div class="promise"><h3>My promise</h3>'
        '<label><input type="checkbox" name="promise" value="yes" required>'
        'I promise I will continue playing the game and will send screenshots as proof of all '
        'games and times.</label></div>'
        '<div class="field"><label>Player signature</label>'
        '<input type="text" name="signature" required maxlength="80" placeholder="X ________">'
        '</div>'
        '<p class="bow">I bow down to the <strong>Games Commissioner</strong> for approval.</p>'
        '<button class="submit" type="submit">Submit request</button>'
        '</form></div>'
        '<div class="footer">Playing while away? '
        '<a class="back" href="/backfill">Submit your screenshots for backfill</a></div>'
    )
    return form_page("Accommodation request", body, ribbon="Accommodation Request Form")


@app.post("/accommodation")
def accommodation_submit(player: str = Form(""), start_date: str = Form(""),
                         end_date: str = Form(""), kind: str = Form(""),
                         reason: str = Form(""), promise: str = Form(""),
                         signature: str = Form("")):
    player = canonical_player(player)
    start = valid_date(start_date)
    end = valid_date(end_date)

    if not player:
        return _acc_error("Player name is required.", player)
    if not start or not end:
        return _acc_error("Enter valid start and end dates.", player)
    if end < start:
        return _acc_error("The last day away cannot be before the first day away.", player)
    if (end - start).days > 60:
        return _acc_error("Requests are capped at 60 days. File a second request if you truly need more.", player)
    if promise != "yes":
        return _acc_error("You have to make the promise. Those are the rules.", player)
    if not signature.strip():
        return _acc_error("A player signature is required.", player)
    if kind not in ACCOMMODATION_KINDS:
        kind = ACCOMMODATION_KINDS[0]

    _acc_write(
        """
        INSERT INTO accommodations
            (player, player_key, start_date, end_date, kind, reason, promise,
             signature, status, submitted_at)
        VALUES (?, ?, ?, ?, ?, ?, 1, ?, 'pending', ?)
        """,
        (player, name_key(player), start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"),
         kind, reason.strip()[:500], signature.strip()[:80],
         datetime.now(timezone.utc).isoformat()),
    )
    return RedirectResponse(url="/accommodation?submitted=1", status_code=303)


def _acc_error(message, player):
    return RedirectResponse(
        url="/accommodation?error=" + quote(message) + "&player=" + quote(player),
        status_code=303,
    )


@app.get("/accommodations", response_class=HTMLResponse)
def accommodation_board():
    rows = _acc_rows("SELECT * FROM accommodations ORDER BY id DESC LIMIT 200")
    if rows:
        table = ('<table class="board"><thead><tr><th>Player</th><th>Dates</th><th>Type</th>'
                 '<th>Reason</th><th>Status</th></tr></thead><tbody>')
        for r in rows:
            reason = esc(r["reason"]) if r["reason"] else '<span class="muted">&mdash;</span>'
            note = ('<div class="muted">' + esc(r["decision_note"]) + '</div>'
                    if r["decision_note"] else '')
            table += ('<tr><td><strong>' + esc(r["player"]) + '</strong></td>'
                      '<td>' + esc(r["start_date"]) + ' &rarr; ' + esc(r["end_date"]) + '</td>'
                      '<td>' + esc(r["kind"]) + '</td>'
                      '<td>' + reason + '</td>'
                      '<td><span class="tag ' + esc(r["status"]) + '">' + esc(r["status"]) + '</span>'
                      + note + '</td></tr>')
        table += '</tbody></table>'
    else:
        table = '<p class="empty">Nobody has asked for time away yet. Impressive, or suspicious.</p>'

    body = ('<a class="back" href="/">&larr; Back to leaderboard</a>'
            '<div class="panel">' + table + '</div>'
            '<div class="footer"><a class="back" href="/accommodation">File a new request</a></div>')
    return form_page("Leave board", body, ribbon="Leave Board",
                     subtitle="Approved days are excused: no penalty score, no missed day.")


@app.get("/backfill", response_class=HTMLResponse)
def backfill_form(player: str = "", error: str = "", submitted: int = 0):
    banner = ""
    if submitted:
        banner = _banner("ok", "Screenshot received. The Games Commissioner will verify and "
                               "backfill your score.")
    elif error:
        banner = _banner("err", esc(error))

    yesterday = (today_local() - timedelta(days=1)).strftime("%Y-%m-%d")
    body = (
        '<a class="back" href="/">&larr; Back to leaderboard</a>' + banner +
        '<div class="panel"><form method="post" action="/backfill" enctype="multipart/form-data">'
        '<p class="subtitle" style="margin-bottom:20px">Kept your promise while you were away? '
        'Send the proof. Verified screenshots replace an excused or penalty day with your real score.</p>'
        '<div class="field"><label>Player name</label>'
        '<input type="text" name="player" list="players" required maxlength="80" value="'
        + esc(player) + '" placeholder="Exactly as it appears on the leaderboard">'
        + player_datalist() + '</div>'
        '<div class="field"><label>Day played</label>'
        '<input type="date" name="play_date" required max="' + yesterday + '" value="' + yesterday + '">'
        '<span class="hint">Past days only. Today is still being collected from Teams.</span></div>'
        '<div class="row">'
        '<div class="field"><label>Zip time</label>'
        '<input type="number" name="zip_score" required min="0" max="1000000" placeholder="e.g. 42"></div>'
        '<div class="field"><label>Patches</label>'
        '<input type="number" name="patch_score" required min="0" max="1000000" placeholder="e.g. 7"></div>'
        '</div>'
        '<div class="field"><label>Screenshot</label>'
        '<input type="file" name="screenshot" accept="image/*" required>'
        '<span class="hint">PNG, JPEG, GIF or WebP, up to '
        + str(ZS_MAX_PROOF_BYTES // (1024 * 1024)) + ' MB. Show the game and the time.</span></div>'
        '<div class="field"><label>Note (optional)</label>'
        '<textarea name="note" maxlength="300" placeholder="Anything the commissioner should know"></textarea></div>'
        '<button class="submit" type="submit">Submit proof</button>'
        '</form></div>'
    )
    return form_page("Score proof", body, ribbon="Score Backfill Request")


def _backfill_error(message, player):
    return RedirectResponse(
        url="/backfill?error=" + quote(message) + "&player=" + quote(player),
        status_code=303,
    )


@app.post("/backfill")
async def backfill_submit(player: str = Form(""), play_date: str = Form(""),
                          zip_score: int = Form(0), patch_score: int = Form(0),
                          note: str = Form(""), screenshot: UploadFile = File(...)):
    player = canonical_player(player)
    day = valid_date(play_date)

    if not player:
        return _backfill_error("Player name is required.", player)
    if not day:
        return _backfill_error("Enter a valid day played.", player)
    if day >= today_local():
        return _backfill_error("Backfill is for past days only; today is still being collected.", player)
    if zip_score < 0 or patch_score < 0 or zip_score > 1000000 or patch_score > 1000000:
        return _backfill_error("Scores must be between 0 and 1,000,000.", player)

    data = await screenshot.read()
    if not data:
        return _backfill_error("A screenshot is required. That was the promise.", player)
    if len(data) > ZS_MAX_PROOF_BYTES:
        return _backfill_error("Screenshot is too large (max "
                               + str(ZS_MAX_PROOF_BYTES // (1024 * 1024)) + " MB).", player)

    image_name, image_mime = store_proof_image(data)
    if not image_name:
        return _backfill_error("That file is not a PNG, JPEG, GIF or WebP image.", player)

    _acc_write(
        """
        INSERT INTO proofs
            (player, player_key, play_date, zip, patch, note, image_name, image_mime,
             status, submitted_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
        """,
        (player, name_key(player), day.strftime("%Y-%m-%d"), zip_score, patch_score,
         note.strip()[:300], image_name, image_mime, datetime.now(timezone.utc).isoformat()),
    )
    return RedirectResponse(url="/backfill?submitted=1", status_code=303)


@app.get("/api/accommodations")
def api_accommodations(status: str = ""):
    if status:
        return _acc_rows("SELECT * FROM accommodations WHERE status = ? ORDER BY id DESC",
                         (status,))
    return _acc_rows("SELECT * FROM accommodations ORDER BY id DESC")


# --------------------------------------------------------------------------- #
# Games Commissioner
# --------------------------------------------------------------------------- #

def _commish_gate():
    body = ('<a class="back" href="/">&larr; Back to leaderboard</a>'
            '<div class="panel"><form method="post" action="/commissioner/login">'
            '<div class="field"><label>Commissioner passcode</label>'
            '<input type="password" name="passcode" required autofocus></div>'
            '<button class="submit" type="submit">Unlock</button></form></div>')
    return form_page("Commissioner", body, ribbon="Games Commissioner",
                     subtitle="Only the Games Commissioner may approve accommodations.")


@app.post("/commissioner/login")
def commissioner_login(passcode: str = Form("")):
    if ZS_COMMISSIONER_TOKEN and passcode != ZS_COMMISSIONER_TOKEN:
        return form_page(
            "Commissioner",
            _banner("err", "Wrong passcode.") +
            '<div class="panel"><form method="post" action="/commissioner/login">'
            '<div class="field"><label>Commissioner passcode</label>'
            '<input type="password" name="passcode" required autofocus></div>'
            '<button class="submit" type="submit">Unlock</button></form></div>',
            ribbon="Games Commissioner")
    response = RedirectResponse(url="/commissioner", status_code=303)
    response.set_cookie(COMMISH_COOKIE, ZS_COMMISSIONER_TOKEN, max_age=60 * 60 * 24 * 30,
                        httponly=True, samesite="lax")
    return response


@app.post("/commissioner/logout")
def commissioner_logout():
    response = RedirectResponse(url="/", status_code=303)
    response.delete_cookie(COMMISH_COOKIE)
    return response


@app.get("/commissioner", response_class=HTMLResponse)
def commissioner_queue(request: Request, error: str = "", done: str = ""):
    if not is_commissioner(request):
        return _commish_gate()

    banner = ""
    if not ZS_COMMISSIONER_TOKEN:
        banner += _banner("warn", "Set <strong>ZS_COMMISSIONER_TOKEN</strong> to keep this page "
                                  "to yourself. Right now anyone can approve.")
    if done:
        banner += _banner("ok", esc(done))
    if error:
        banner += _banner("err", esc(error))

    pending_acc = _acc_rows("SELECT * FROM accommodations WHERE status = 'pending' ORDER BY id ASC")
    acc_html = '<div class="section-title">Accommodation requests</div>'
    if pending_acc:
        for r in pending_acc:
            reason = ('<p>' + esc(r["reason"]) + '</p>') if r["reason"] else ''
            acc_html += (
                '<div class="review"><div class="meta">'
                '<h4>' + esc(r["player"]) + '</h4>'
                '<p class="muted">' + esc(r["start_date"]) + ' &rarr; ' + esc(r["end_date"]) +
                ' &middot; ' + esc(r["kind"]) + '</p>' + reason +
                '<p class="muted">Signed: ' + esc(r["signature"] or "-") + '</p>'
                '<form method="post" action="/commissioner/accommodation/' + str(r["id"]) + '">'
                '<div class="inline">'
                '<input type="text" name="note" maxlength="200" placeholder="Note (optional)">'
                '<button class="mini approve" type="submit" name="action" value="approve">Approve</button>'
                '<button class="mini deny" type="submit" name="action" value="deny">Deny</button>'
                '</div></form></div></div>')
    else:
        acc_html += '<p class="empty">No pending requests.</p>'

    pending_proofs = _acc_rows("SELECT * FROM proofs WHERE status = 'pending' ORDER BY id ASC")
    proof_html = '<div class="section-title">Screenshot backfill</div>'
    if pending_proofs:
        for r in pending_proofs:
            note = ('<p>' + esc(r["note"]) + '</p>') if r["note"] else ''
            proof_html += (
                '<div class="review">'
                '<div class="shot"><a href="/commissioner/proof/' + str(r["id"]) + '/image" target="_blank">'
                '<img src="/commissioner/proof/' + str(r["id"]) + '/image" alt="Score screenshot"></a></div>'
                '<div class="meta"><h4>' + esc(r["player"]) + '</h4>'
                '<p class="muted">Played ' + esc(r["play_date"]) + '</p>' + note +
                '<form method="post" action="/commissioner/proof/' + str(r["id"]) + '">'
                '<div class="inline">'
                '<input type="number" name="zip_score" min="0" max="1000000" value="' + str(r["zip"]) + '">'
                '<input type="number" name="patch_score" min="0" max="1000000" value="' + str(r["patch"]) + '">'
                '<input type="text" name="note" maxlength="200" placeholder="Note (optional)">'
                '<button class="mini approve" type="submit" name="action" value="apply">Apply score</button>'
                '<button class="mini deny" type="submit" name="action" value="reject">Reject</button>'
                '</div></form></div></div>')
    else:
        proof_html += '<p class="empty">No screenshots waiting.</p>'

    recent = _acc_rows("SELECT * FROM accommodations WHERE status != 'pending' ORDER BY id DESC LIMIT 10")
    recent_html = '<div class="section-title">Recent decisions</div>'
    if recent:
        recent_html += '<table class="board"><thead><tr><th>Player</th><th>Dates</th><th>Status</th></tr></thead><tbody>'
        for r in recent:
            recent_html += ('<tr><td>' + esc(r["player"]) + '</td><td>' + esc(r["start_date"]) +
                            ' &rarr; ' + esc(r["end_date"]) + '</td><td><span class="tag ' +
                            esc(r["status"]) + '">' + esc(r["status"]) + '</span></td></tr>')
        recent_html += '</tbody></table>'
    else:
        recent_html += '<p class="empty">Nothing decided yet.</p>'

    body = ('<a class="back" href="/">&larr; Back to leaderboard</a>' + banner +
            '<div class="nav-bar">'
            '<a class="nav-btn" href="/backfill">Submit Score Proof</a>'
            '<a class="nav-btn ghost" href="/accommodation">Request Time Away</a>'
            '<a class="nav-btn ghost" href="/accommodations">Leave Board</a>'
            '</div>'
            '<div class="panel">' + acc_html + '</div>'
            '<div class="panel">' + proof_html + '</div>'
            '<div class="panel">' + recent_html + '</div>'
            '<div class="footer"><form method="post" action="/commissioner/logout">'
            '<button class="mini" type="submit">Lock this page</button></form></div>')
    return form_page("Commissioner", body, ribbon="Games Commissioner", wide=True)


@app.post("/commissioner/accommodation/{acc_id}")
def commissioner_decide_accommodation(acc_id: int, request: Request,
                                      action: str = Form(""), note: str = Form("")):
    if not is_commissioner(request):
        return RedirectResponse(url="/commissioner", status_code=303)

    rows = _acc_rows("SELECT * FROM accommodations WHERE id = ?", (acc_id,))
    if not rows:
        return RedirectResponse(url="/commissioner?error=" + quote("Request not found."),
                                status_code=303)
    row = rows[0]
    status = "approved" if action == "approve" else "denied"

    _acc_write(
        "UPDATE accommodations SET status = ?, decided_at = ?, decision_note = ? WHERE id = ?",
        (status, datetime.now(timezone.utc).isoformat(), note.strip()[:200] or None, acc_id),
    )

    message = row["player"] + ": " + status
    if status == "approved":
        changed = excuse_recorded_days(row["player"], row["start_date"], row["end_date"], row["kind"])
        if changed:
            message += " (" + str(len(changed)) + " already-scored penalty day(s) reversed)"
    return RedirectResponse(url="/commissioner?done=" + quote(message), status_code=303)


@app.post("/commissioner/proof/{proof_id}")
def commissioner_decide_proof(proof_id: int, request: Request, action: str = Form(""),
                              zip_score: int = Form(0), patch_score: int = Form(0),
                              note: str = Form("")):
    if not is_commissioner(request):
        return RedirectResponse(url="/commissioner", status_code=303)

    rows = _acc_rows("SELECT * FROM proofs WHERE id = ?", (proof_id,))
    if not rows:
        return RedirectResponse(url="/commissioner?error=" + quote("Proof not found."),
                                status_code=303)
    row = rows[0]
    if row["status"] != "pending":
        return RedirectResponse(url="/commissioner?error=" + quote("That proof was already decided."),
                                status_code=303)

    if action != "apply":
        _acc_write(
            "UPDATE proofs SET status = 'rejected', decided_at = ?, decision_note = ? WHERE id = ?",
            (datetime.now(timezone.utc).isoformat(), note.strip()[:200] or None, proof_id),
        )
        return RedirectResponse(url="/commissioner?done=" + quote(row["player"] + ": proof rejected"),
                                status_code=303)

    if zip_score < 0 or patch_score < 0 or zip_score > 1000000 or patch_score > 1000000:
        return RedirectResponse(url="/commissioner?error=" + quote("Scores must be between 0 and 1,000,000."),
                                status_code=303)

    result = apply_backfill(row["player"], row["play_date"], zip_score, patch_score)
    _acc_write(
        """
        UPDATE proofs SET status = 'applied', zip = ?, patch = ?, decided_at = ?,
                          decision_note = ? WHERE id = ?
        """,
        (zip_score, patch_score, datetime.now(timezone.utc).isoformat(),
         note.strip()[:200] or None, proof_id),
    )
    message = (result["player"] + " " + result["date"] + ": " + str(zip_score) + " // "
               + str(patch_score) + " applied (replaced " + result["replaced"] + ")")
    return RedirectResponse(url="/commissioner?done=" + quote(message), status_code=303)


@app.get("/commissioner/proof/{proof_id}/image", include_in_schema=False)
def commissioner_proof_image(proof_id: int, request: Request):
    if not is_commissioner(request):
        return JSONResponse(status_code=401, content={"error": "unauthorized"})
    rows = _acc_rows("SELECT image_name, image_mime FROM proofs WHERE id = ?", (proof_id,))
    if not rows or not rows[0]["image_name"]:
        return JSONResponse(status_code=404, content={"error": "not found"})
    path = os.path.join(ZS_PROOF_DIR, os.path.basename(rows[0]["image_name"]))
    if not os.path.exists(path):
        return JSONResponse(status_code=404, content={"error": "not found"})
    return FileResponse(path, media_type=rows[0]["image_mime"] or "image/png")

# Hidden admin: adjust a single player's score for a specific date.
# ---------------------------------------------------------------------------
# Not linked anywhere in the UI and excluded from the OpenAPI schema (/docs).
# The URL path is configurable via ZS_ADMIN_PATH (default "/backstage"); set
# ZS_ADMIN_TOKEN to additionally require ?token=... on the page and its APIs.
# Editing a day's cell recomputes the ENTIRE leaderboard from history so
# totals / averages / wins / penalty counts all stay consistent.
# =========================================================================== #

ZS_ADMIN_PATH = (os.environ.get("ZS_ADMIN_PATH", "/backstage").rstrip("/") or "/backstage")
ZS_ADMIN_TOKEN = os.environ.get("ZS_ADMIN_TOKEN", "")


def recompute_state_from_history():
    """Rebuild the aggregate leaderboard (STATE_FILE) from HISTORY_FILE.

    Mirrors process()'s accounting so an edited day stays consistent: per day,
    wins go to the best non-penalty scorers, and penalty cells count toward
    penalty_days.
    """
    history = load_json(HISTORY_FILE)
    state = {}
    for day in sorted(history.keys()):
        cells = {}
        for p, v in history[day].items():
            if isinstance(v, dict):
                z = int(v.get("zip", 0))
                pa = int(v.get("patch", 0))
                pen = bool(v.get("penalty", False))
            else:
                z, pa, pen = 0, 0, False
            cells[p] = {"zip": z, "patch": pa, "penalty": pen}
        real = {p: d for p, d in cells.items() if not d["penalty"]}
        pool = real if real else cells
        best_zip = min(pool.items(), key=lambda x: x[1]["zip"])[0] if pool else None
        best_patch = min(pool.items(), key=lambda x: x[1]["patch"])[0] if pool else None
        best_total = min(pool.items(), key=lambda x: x[1]["zip"] + x[1]["patch"])[0] if pool else None
        for p, d in cells.items():
            s = state.setdefault(p, {"zip_total": 0, "patch_total": 0, "days": 0,
                                     "penalty_days": 0, "zip_wins": 0, "patch_wins": 0,
                                     "total_wins": 0})
            s["zip_total"] += d["zip"]
            s["patch_total"] += d["patch"]
            s["days"] += 1
            if d["penalty"]:
                s["penalty_days"] += 1
            if p == best_zip:
                s["zip_wins"] += 1
            if p == best_patch:
                s["patch_wins"] += 1
            if p == best_total:
                s["total_wins"] += 1
    save_json(STATE_FILE, state)
    return state


def _admin_auth_ok(token):
    return (not ZS_ADMIN_TOKEN) or (token == ZS_ADMIN_TOKEN)


def admin_set_score(date: str, player: str, zip: int = 0, patch: int = 0,
                    penalty: bool = False, token: str = ""):
    if not _admin_auth_ok(token):
        return JSONResponse(status_code=404, content={"detail": "Not Found"})
    player = player.strip()
    if not date or not player:
        return JSONResponse(status_code=400, content={"error": "date and player are required"})
    history = load_json(HISTORY_FILE)
    history.setdefault(date, {})
    history[date][player] = {"zip": int(zip), "patch": int(patch),
                             "total": int(zip) + int(patch), "penalty": bool(penalty)}
    save_json(HISTORY_FILE, history)
    recompute_state_from_history()
    return {"status": "ok", "date": date, "player": player, "cell": history[date][player]}


def admin_delete_score(date: str, player: str, token: str = ""):
    if not _admin_auth_ok(token):
        return JSONResponse(status_code=404, content={"detail": "Not Found"})
    history = load_json(HISTORY_FILE)
    if date in history and player in history[date]:
        del history[date][player]
        if not history[date]:
            del history[date]
        save_json(HISTORY_FILE, history)
        recompute_state_from_history()
        return {"status": "deleted", "date": date, "player": player}
    return {"status": "not_found", "date": date, "player": player}


def admin_page(token: str = ""):
    if not _admin_auth_ok(token):
        return JSONResponse(status_code=404, content={"detail": "Not Found"})
    return HTMLResponse(content=ADMIN_HTML.replace("__ADMIN__", ZS_ADMIN_PATH))


app.add_api_route(ZS_ADMIN_PATH, admin_page, methods=["GET"],
                  response_class=HTMLResponse, include_in_schema=False)
app.add_api_route(ZS_ADMIN_PATH + "/set", admin_set_score, methods=["POST"],
                  include_in_schema=False)
app.add_api_route(ZS_ADMIN_PATH + "/delete", admin_delete_score, methods=["POST"],
                  include_in_schema=False)


ADMIN_HTML = """<!DOCTYPE html><html><head><title>Backstage</title>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="icon" type="image/x-icon" href="/favicon.ico">
<style>
*{box-sizing:border-box}
body{margin:0;font-family:-apple-system,Segoe UI,Roboto,sans-serif;background:#1a1a2e;color:#eee}
.container{max-width:820px;margin:0 auto;padding:24px}
.brand{text-align:center;margin-bottom:10px}
.logo{max-width:170px;height:auto}
h1{text-align:center;color:#4ecca3;margin:.2em 0}
.sub{text-align:center;color:#888;margin-bottom:24px;font-size:.9em}
.card{background:rgba(255,255,255,.04);border:1px solid rgba(255,255,255,.08);border-radius:14px;padding:20px;margin-bottom:20px}
label{display:block;font-size:.8em;color:#9aa;text-transform:uppercase;letter-spacing:1px;margin:12px 0 6px}
input[type=text],input[type=number],input[type=date]{width:100%;padding:10px 12px;border-radius:8px;border:1px solid rgba(255,255,255,.15);background:#0f0f1e;color:#eee;font-size:1em}
.row{display:flex;gap:14px;flex-wrap:wrap}
.row>div{flex:1;min-width:140px}
.chk{display:flex;align-items:center;gap:8px;margin-top:14px}
.chk input{width:18px;height:18px}
.btns{display:flex;gap:12px;margin-top:20px;flex-wrap:wrap}
button{border:none;border-radius:8px;padding:11px 22px;font-size:.95em;font-weight:bold;cursor:pointer}
.save{background:#4ecca3;color:#08131b}
.del{background:#e94560;color:#fff}
.ghost{background:rgba(255,255,255,.08);color:#ccc}
button:hover{opacity:.9}
#msg{margin-top:16px;min-height:1.2em;font-size:.95em}
.ok{color:#4ecca3}.err{color:#e94560}
table{width:100%;border-collapse:collapse;margin-top:8px}
th{background:rgba(78,204,163,.15);color:#4ecca3;text-transform:uppercase;font-size:.75em;letter-spacing:1px;padding:10px;text-align:left}
td{padding:9px 10px;border-bottom:1px solid rgba(255,255,255,.06)}
tr.clk{cursor:pointer}tr.clk:hover{background:rgba(255,255,255,.06)}
.pen{color:#e94560;font-weight:bold}
.hint{color:#667;font-size:.8em;margin-top:6px}
h3{color:#888}
</style></head><body><div class="container">
<div class="brand"><img src="/logo.png" alt="" class="logo"></div>
<h1>Backstage</h1>
<div class="sub">Manually set or remove a player's score for a specific date. Every edit recomputes the whole leaderboard.</div>
<div class="card">
<div class="row">
<div><label>Date</label><input type="date" id="date"></div>
<div><label>Player</label><input type="text" id="player" list="players" autocomplete="off" placeholder="Exact name"></div>
</div>
<datalist id="players"></datalist>
<div class="row">
<div><label>Zip</label><input type="number" id="zip" value="0"></div>
<div><label>Patch</label><input type="number" id="patch" value="0"></div>
</div>
<div class="chk"><input type="checkbox" id="penalty"><label style="margin:0">Mark as penalty day</label></div>
<div class="hint">Click a row in the table below to load that player into the form.</div>
<div class="btns">
<button class="ghost" onclick="loadCell()">Load current</button>
<button class="save" onclick="save()">Save</button>
<button class="del" onclick="del()">Delete</button>
</div>
<div id="msg"></div>
</div>
<div class="card">
<h3 style="margin:0 0 6px">Scores on <span id="dlabel">(pick a date)</span></h3>
<table><thead><tr><th>Player</th><th>Zip</th><th>Patch</th><th>Total</th><th>Penalty</th></tr></thead>
<tbody id="dbody"><tr><td colspan="5" style="color:#667">No data</td></tr></tbody></table>
</div>
</div>
<script>
const ADMIN="__ADMIN__";
const TOKEN=new URLSearchParams(location.search).get("token")||"";
let HIST={};
const $=id=>document.getElementById(id);
function qs(o){return Object.keys(o).map(k=>encodeURIComponent(k)+"="+encodeURIComponent(o[k])).join("&");}
function cell(d,p){const v=(HIST[d]||{})[p];if(v==null)return null;if(typeof v==="object")return v;return {zip:0,patch:0,total:v,penalty:false};}
async function loadHistory(){
  HIST=await fetch("/history").then(r=>r.json()).catch(()=>({}));
  const names=new Set();
  Object.values(HIST).forEach(day=>Object.keys(day).forEach(n=>names.add(n)));
  $("players").innerHTML=[...names].sort().map(n=>'<option value="'+n+'">').join("");
  renderDay();
}
function renderDay(){
  const d=$("date").value;$("dlabel").textContent=d||"(pick a date)";
  const day=HIST[d]||{};const keys=Object.keys(day).sort();
  if(!keys.length){$("dbody").innerHTML='<tr><td colspan="5" style="color:#667">No data</td></tr>';return;}
  $("dbody").innerHTML=keys.map(p=>{const c=cell(d,p);return '<tr class="clk" data-p="'+p+'" onclick="pick(this)"><td>'+p+'</td><td>'+c.zip+'</td><td>'+c.patch+'</td><td>'+c.total+'</td><td>'+(c.penalty?'<span class="pen">PENALTY</span>':'')+'</td></tr>';}).join("");
}
function pick(el){$("player").value=el.getAttribute("data-p");loadCell();}
function loadCell(){
  const c=cell($("date").value,$("player").value.trim());
  if(c){$("zip").value=c.zip;$("patch").value=c.patch;$("penalty").checked=!!c.penalty;msg("Loaded current value.","ok");}
  else{msg("No existing score for that date/player - Save will create one.","");}
}
function msg(t,cls){const m=$("msg");m.textContent=t;m.className=cls||"";}
async function post(path,params){
  params.token=TOKEN;
  const r=await fetch(ADMIN+path+"?"+qs(params),{method:"POST"});
  return {ok:r.ok,data:await r.json().catch(()=>({}))};
}
async function save(){
  const date=$("date").value,player=$("player").value.trim();
  if(!date||!player){msg("Date and player are required.","err");return;}
  const res=await post("/set",{date:date,player:player,zip:$("zip").value||0,patch:$("patch").value||0,penalty:$("penalty").checked});
  if(res.ok&&res.data.status==="ok"){msg("Saved "+player+" for "+date+".","ok");await loadHistory();}
  else{msg("Save failed: "+JSON.stringify(res.data),"err");}
}
async function del(){
  const date=$("date").value,player=$("player").value.trim();
  if(!date||!player){msg("Date and player are required.","err");return;}
  if(!confirm("Delete "+player+"'s score for "+date+"?"))return;
  const res=await post("/delete",{date:date,player:player});
  if(res.ok&&res.data.status==="deleted"){msg("Deleted.","ok");await loadHistory();}
  else if(res.data&&res.data.status==="not_found"){msg("No such score to delete.","err");}
  else{msg("Delete failed: "+JSON.stringify(res.data),"err");}
}
$("date").addEventListener("change",renderDay);
document.addEventListener("DOMContentLoaded",loadHistory);
</script>
</body></html>"""
