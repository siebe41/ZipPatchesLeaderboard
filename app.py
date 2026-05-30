from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
import json
import os
import re
import random
import math
from datetime import datetime, timedelta

app = FastAPI()

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

    # Score limits to prevent cheating
    MIN_TOTAL_SCORE = 9  # Minimum reasonable combined score (greater than 8)
    MAX_SCORE = 1000000

    # Check for the "zip//patch" format
    m = re.search(r'^(\d+)\s*//\s*(\d+)$', msg)
    if m:
        try:
            zip_score = int(m.group(1))
            patch_score = int(m.group(2))
        except (ValueError, OverflowError):
            return None, "Invalid score format. Nice attempt though!"

        # Validate scores are within reasonable range
        total = zip_score + patch_score
        if total < MIN_TOTAL_SCORE:
            return None, f"Nice try! {zip_score}//{patch_score} = {total}? Are we sure they're not cheating?"
        if zip_score > MAX_SCORE or patch_score > MAX_SCORE:
            return None, "Those numbers are suspiciously large... 🤔"

        return (zip_score, patch_score), None

    # Check for single number format
    m = re.search(r'^(\d+)$', msg)
    if m:
        try:
            zip_score = int(m.group(1))
        except (ValueError, OverflowError):
            return None, "Invalid score format. Nice attempt though!"

        # Validate score is within reasonable range
        if zip_score < MIN_TOTAL_SCORE:
            return None, f"Score of {zip_score}? That's a bit too good to be true... 🧐"
        if zip_score > MAX_SCORE:
            return None, "Those numbers are suspiciously large... 🤔"

        return (zip_score, 0), None

    return None, "Invalid score format. Nice attempt though!"


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
    rejections = []  # Track rejected scores for trash talk

    for line in payload.messages:
        if ":" not in line:
            continue
        name, msg = line.split(":", 1)
        name = name.strip()

        parsed, error_msg = parse_score(msg)
        if not parsed:
            if error_msg:
                rejections.append(f"{name}: {error_msg}")
            continue
        z, p = parsed
        day_scores[name] = {"zip": z, "patch": p}
        participants.add(name)

    if not day_scores:
        return rejections

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

    return rejections


@app.post("/ingest")
def ingest(payload: Payload):
    rejections = process(payload)

    if rejections:
        return {
            "status": "ok",
            "trash_talk": rejections,
            "message": "Some scores were rejected. Up your game! 💪"
        }

    return {"status": "ok"}


@app.get("/leaderboard")
def leaderboard():
    state = load_json(STATE_FILE)
    rows = []
    for name, d in state.items():
        total = d["zip_total"] + d["patch_total"]
        avg_total = total / d["days"]
        avg_zip = d["zip_total"] / d["days"]
        avg_patch = d["patch_total"] / d["days"]

        # Generate trash talk based on performance
        trash_talk = []

        # Suspiciously good scores (average < 15)
        if avg_total < 15:
            trash_talk.append(f"🤨 Average of {avg_total:.1f}? Are we sure {name} isn't cheating?")

        # Missed days
        if d["penalty_days"] > 0:
            trash_talk.append(f"💤 {d['penalty_days']} missed day{'s' if d['penalty_days'] > 1 else ''}. Someone's slacking!")

        # Really good at one thing
        if avg_zip < 10 and avg_patch > 20:
            trash_talk.append("🎯 Zip wizard but patch struggles are real")
        elif avg_patch < 10 and avg_zip > 20:
            trash_talk.append("🎯 Patch master but zip game needs work")

        rows.append({
            "player": name,
            "total": total,
            "avg_zip": avg_zip,
            "avg_patch": avg_patch,
            "missed": d["penalty_days"],
            "trash_talk": trash_talk if trash_talk else None
        })

    sorted_rows = sorted(rows, key=lambda x: x["total"])

    # Add extra trash talk for last place
    if len(sorted_rows) > 1:
        last_place = sorted_rows[-1]
        if last_place["trash_talk"] is None:
            last_place["trash_talk"] = []
        last_place["trash_talk"].append("🏆 Last place! At least you're consistently... last.")

    # Add praise for first place
    if sorted_rows:
        first_place = sorted_rows[0]
        if first_place["trash_talk"] is None:
            first_place["trash_talk"] = []
        first_place["trash_talk"].insert(0, "👑 First place! Show-off.")

    return sorted_rows
