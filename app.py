from fastapi import FastAPI
from pydantic import BaseModel
import json
import os
import re

app = FastAPI()

STATE_FILE = "leaderboard.json"
HISTORY_FILE = "history.json"


# --- Models ---
class Payload(BaseModel):
    date: str
    messages: list[str]


# --- Helpers ---
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

    # Maximum reasonable score to prevent cheating with huge numbers
    MAX_SCORE = 1000000

    # Check for the "zip//patch" format
    m = re.search(r'^(\d+)\s*//\s*(\d+)$', msg)
    if m:
        zip_score = int(m.group(1))
        patch_score = int(m.group(2))

        # Validate scores are within reasonable range
        if zip_score < 0 or patch_score < 0:
            return None
        if zip_score > MAX_SCORE or patch_score > MAX_SCORE:
            return None

        return zip_score, patch_score

    # Check for single number format
    m = re.search(r'^(\d+)$', msg)
    if m:
        zip_score = int(m.group(1))

        # Validate score is within reasonable range
        if zip_score < 0 or zip_score > MAX_SCORE:
            return None

        return zip_score, 0

    return None


# --- Core logic ---
def process(payload):
    state = load_json(STATE_FILE)
    history = load_json(HISTORY_FILE)

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
        return

    worst_zip = max(v["zip"] for v in day_scores.values())
    worst_patch = max(v["patch"] for v in day_scores.values())

    penalty_zip = worst_zip + 1
    penalty_patch = worst_patch + 1

    history[payload.date] = {}

    for p in participants:
        if p not in state:
            state[p] = {
                "zip_total": 0,
                "patch_total": 0,
                "days": 0,
                "penalty_days": 0
            }

        if p in day_scores:
            z = day_scores[p]["zip"]
            pa = day_scores[p]["patch"]
        else:
            z = penalty_zip
            pa = penalty_patch
            state[p]["penalty_days"] += 1

        state[p]["zip_total"] += z
        state[p]["patch_total"] += pa
        state[p]["days"] += 1

        history[payload.date][p] = z + pa

    save_json(STATE_FILE, state)
    save_json(HISTORY_FILE, history)


# --- Routes ---
@app.post("/ingest")
def ingest(payload: Payload):
    process(payload)
    return {"status": "ok"}


@app.get("/leaderboard")
def leaderboard():
    state = load_json(STATE_FILE)

    rows = []
    for name, d in state.items():
        total = d["zip_total"] + d["patch_total"]
        rows.append({
            "player": name,
            "total": total,
            "avg_zip": d["zip_total"] / d["days"],
            "avg_patch": d["patch_total"] / d["days"],
            "missed": d["penalty_days"]
        })

    return sorted(rows, key=lambda x: x["total"])
