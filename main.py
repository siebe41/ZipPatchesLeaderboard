from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import json
import os
import re
import random
import math
import sqlite3
import threading
from datetime import datetime, timedelta, timezone

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

STATE_FILE = "/home/leaderboard.json"
HISTORY_FILE = "/home/history.json"


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
    return process(payload)


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
ZS_FINALIZE_HOUR = int(os.environ.get("ZS_FINALIZE_HOUR", "1"))
ZS_FINALIZE_MINUTE = int(os.environ.get("ZS_FINALIZE_MINUTE", "10"))


def _zs_tz():
    if ZoneInfo is not None:
        try:
            return ZoneInfo(ZS_TIMEZONE)
        except Exception:
            pass
    return timezone.utc


_zs_db_lock = threading.Lock()
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
    return {"received": len(messages), "new": new_count}


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

        for d in filtered_days:
            if d not in history:
                continue
            pdata = get_player_day(history, d, player)
            if pdata is None:
                continue

            all_scores.append(pdata)
            if not pdata.get("penalty", False):
                real_scores.append(pdata)
            else:
                penalty_count += 1

            # Daily rank
            day_entries = [(p2, day_total_val(v2)) for p2, v2 in history[d].items()]
            day_entries.sort(key=lambda x: x[1])
            rank = next((i+1 for i, (pp, _) in enumerate(day_entries) if pp == player), len(day_entries))
            daily_ranks.append(rank)

            # Daily winners (only among non-penalty posters)
            actual_posters = {}
            for p2, v2 in history[d].items():
                pd2 = get_player_day(history, d, p2)
                if pd2 and not pd2.get("penalty", False):
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
            if pdata is None:
                continue
            entries = [(p2, day_total_val(v2)) for p2, v2 in history[d].items()]
            entries.sort(key=lambda x: x[1])
            if entries and entries[-1][0] == player:
                last_place += 1
            if not pdata.get("penalty", False) and entries:
                best = entries[0][1]
                if 0 < pdata["total"] - best <= 5:
                    close_calls += 1

        streak = 0
        for d in reversed(filtered_days):
            pdata = get_player_day(history, d, player)
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
            "best_zip": min(real_zips), "worst_zip": max(real_zips),
            "best_patch": min(real_patches), "worst_patch": max(real_patches),
            "best_day": min(real_totals), "worst_day": max(real_totals),
            "consistency": std_dev, "participation": participation,
            "avg_rank": avg_rank, "podium": podium,
            "basement": last_place, "close_calls": close_calls, "streak": streak
        }

    return results


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
    if mode == "week":
        week_start = latest_dt - timedelta(days=latest_dt.weekday())
        week_end = week_start + timedelta(days=6)
        filtered_days = [d for d in all_sorted_days if week_start.strftime("%Y-%m-%d") <= d <= week_end.strftime("%Y-%m-%d")]
    elif mode == "month":
        filtered_days = [d for d in all_sorted_days if d[:7] == latest_day[:7]]
    elif mode in ("year", "ytd"):
        filtered_days = [d for d in all_sorted_days if d[:4] == latest_day[:4]]
    else:
        filtered_days = list(all_sorted_days)

    if not filtered_days:
        filtered_days = list(all_sorted_days)

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
            "total_wins": s["total_wins"], "missed": s["missed"], "days": s["days"]
        })
    rows.sort(key=lambda x: x["total"])

    if not rows:
        return HTMLResponse(content='<html><body style="background:#1a1a2e;color:white;font-family:Arial;display:flex;justify-content:center;align-items:center;height:100vh;"><h1>No data for this period</h1></body></html>')

    # Movement
    movement = {}
    if len(filtered_days) >= 2:
        prev_data = history.get(filtered_days[-2], {})
        curr_data = history.get(filtered_days[-1], {})
        prev_rank = sorted(prev_data.items(), key=lambda x: day_total_val(x[1]))
        curr_rank = sorted(curr_data.items(), key=lambda x: day_total_val(x[1]))
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
        ld = filtered_days[-1]
        if ld in history:
            items = [(p, day_total_val(v)) for p, v in history[ld].items()]
            winner = min(items, key=lambda x: x[1])
            loser = max(items, key=lambda x: x[1])
            daily_winner = winner[0] + " (" + str(winner[1]) + ")"
            daily_loser = loser[0] + " (" + str(loser[1]) + ")"

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
                for p, v in history[d].items():
                    totals[p] = totals.get(p, 0) + day_total_val(v)
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
        trash_options = [
            rows[0]["player"] + " is absolutely cooking right now",
            rows[-1]["player"] + "... we need to talk",
            rows[0]["player"] + " woke up and chose dominance",
            rows[-1]["player"] + " might want to uninstall LinkedIn",
            rows[0]["player"] + " is ice cold under pressure",
            rows[-1]["player"] + " treating this like a speedrun in reverse",
            rows[0]["player"] + " built different",
            "RIP " + rows[-1]["player"] + "'s leaderboard hopes",
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
        ]:
            sa = ' style="' + clr + '"' if clr else ""
            adv_html += '<div class="sg"><span class="sl">' + str(label) + '</span><span class="sv"' + sa + '>' + str(val) + '</span></div>'
        adv_html += '</div></div>'

    # Weekly card
    weekly_html = ""
    if weekly_title:
        weekly_html = '<div class="card weekly"><h3>' + weekly_title + '</h3><div class="value">' + weekly_value + '</div></div>'

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
h1{text-align:center;font-size:2.5em;margin-bottom:5px;background:linear-gradient(90deg,#4ecca3,#36a2eb);-webkit-background-clip:text;-webkit-text-fill-color:transparent}
.subtitle{text-align:center;color:#888;margin-bottom:10px;font-style:italic}
.mode-bar{display:flex;justify-content:center;gap:10px;margin-bottom:30px;flex-wrap:wrap}
.mode-btn{background:rgba(255,255,255,.05);color:#888;padding:8px 20px;border-radius:20px;text-decoration:none;font-size:.9em;border:1px solid rgba(255,255,255,.1);transition:all .2s}
.mode-btn:hover{background:rgba(255,255,255,.1);color:#eee}
.mode-btn.active{background:rgba(78,204,163,.2);color:#4ecca3;border-color:#4ecca3}
.highlights{display:flex;gap:20px;margin-bottom:30px;flex-wrap:wrap;justify-content:center}
.card{background:rgba(255,255,255,.05);border-radius:15px;padding:20px 30px;text-align:center;min-width:200px;border:1px solid rgba(255,255,255,.1);backdrop-filter:blur(10px)}
.card.winner{border-color:#4ecca3}.card.loser{border-color:#e94560}.card.trash{border-color:#ffcd56;min-width:300px}.card.weekly{border-color:#9966ff}
.card h3{font-size:.9em;color:#888;margin-bottom:8px}.card .value{font-size:1.4em;font-weight:bold}
.card.winner .value{color:#4ecca3}.card.loser .value{color:#e94560}.card.trash .value{color:#ffcd56;font-size:1.1em}.card.weekly .value{color:#9966ff}
table{width:100%;border-collapse:collapse;margin-bottom:40px;background:rgba(255,255,255,.03);border-radius:15px;overflow:hidden}
th{background:rgba(78,204,163,.15);padding:14px 12px;text-align:left;font-size:.85em;color:#4ecca3;text-transform:uppercase;letter-spacing:1px;cursor:pointer;user-select:none;white-space:nowrap}
th:hover{background:rgba(78,204,163,.25)}
th .sort-arrow{margin-left:4px;font-size:.7em}
td{padding:12px;border-bottom:1px solid rgba(255,255,255,.05)}
tr:hover{background:rgba(255,255,255,.05)}
.player{font-weight:bold;font-size:1.1em}.total{font-weight:bold;color:#4ecca3;font-size:1.2em}.move{font-size:1.1em}.missed{color:#e94560}
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
        const mb=r.missed>0?'<span class=missed>'+r.missed+'</span>':'0';
        html+="<tr><td>"+rank+"</td><td class='player'>"+r.player+"</td><td class='total'>"+r.total+"</td><td>"+r.avg_zip+"</td><td>"+r.avg_patch+"</td><td>Z:"+r.zip_wins+" P:"+r.patch_wins+" T:"+r.total_wins+"</td><td>"+mb+"</td><td>"+r.days+"</td><td class='move'>"+r.move+"</td></tr>";
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
    js = js.replace("__ZL__", zl).replace("__ZD__", zd).replace("__ZC__", zc)
    js = js.replace("__PL__", pl).replace("__PD__", pd).replace("__PC__", pc)
    js = js.replace("__TL__", tl).replace("__TD__", td).replace("__TC__", tc)
    js = js.replace("__CHART_LABELS__", chart_labels).replace("__CHART_DATA__", chart_data_json).replace("__CHART_COLORS__", chart_colors_json)
    js = js.replace("__TREND_LABELS__", trend_labels).replace("__TREND_DATASETS__", trend_json)

    html = '<!DOCTYPE html><html><head><title>Zip Patchlings</title><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="refresh" content="300"><script src="https://cdn.jsdelivr.net/npm/chart.js"></script>'
    html += css
    html += '</head><body><div class="container">'
    html += '<h1>Zip Patchlings</h1>'
    html += '<p class="subtitle">Consistency beats talent. Miss a day? Pay the price.</p>'
    html += mode_html
    html += '<div class="highlights">'
    html += '<div class="card winner"><h3>Latest Winner</h3><div class="value">' + dw + '</div></div>'
    html += '<div class="card loser"><h3>Rough Day</h3><div class="value">' + dl + '</div></div>'
    html += weekly_html
    html += '<div class="card trash"><h3>Commentary</h3><div class="value">' + trash_line + '</div></div>'
    html += '</div>'
    html += '<table id="mainTable"><thead><tr>'
    for col, dtype, label in [("rank","num","Rank"),("player","str","Player"),("total","num","Total"),("avg_zip","num","Avg Zip"),("avg_patch","num","Avg Patch"),("total_wins","num","Wins"),("missed","num","Missed"),("days","num","Days"),("move","str","Move")]:
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