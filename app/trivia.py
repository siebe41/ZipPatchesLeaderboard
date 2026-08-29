"""Patchling Trivia: a side game that shares the app and nothing else.

Isolation is the whole point of this module, so it is worth being explicit
about what that means: it never opens ``leaderboard.json`` or ``history.json``,
never touches a daily rank, penalty, streak, excused day, weekly total or win
counter, and adds no scheduled job beyond the lazy phase-advance below.

This game is architecturally the odd one out among the games in this app.
Flappy Duck, PatchMan, Patchaga, Ducker, Patch Wall, Patch Trail and Patch
Sweeper are all solo score-attack games: a deterministic simulation the
server replays from a seed and an input trace, because the whole point is a
trustworthy leaderboard for a run nobody else witnessed. Trivia has neither
problem. It is live and social -- a host screen and a room full of phones,
everyone watching the same clock -- so there is nothing to replay and no
reason to forge: the "run" is a shared experience with an audience.

So there is no seed, no session, no heartbeat, no input trace, and no replay.
Instead there is one shared, in-memory game -- deliberately singular, because
this is built for one cabinet at one booth showing one game to one room at a
time, not a matchmaking service. A player's phone and the host's screen both
poll ``/api/state``, which lazily advances the game's phase (lobby -> question
-> reveal -> ... -> final) based on wall-clock time elapsed since the phase
began, computed fresh on every call rather than driven by a background timer
or scheduled job. That keeps this file a plain request/response API with no
persistent connections and no thread to manage -- the cheapest architecture
available for a Raspberry Pi.

Final scores are written to ``trivia_results`` -- the one piece of state this
module keeps beyond the live game -- so the format still gets a leaderboard
like every other game here, even though nothing about getting there was a
replayable run.
"""

from fastapi import APIRouter
from fastapi.responses import JSONResponse, FileResponse
from pydantic import BaseModel, Field
import json
import logging
import os
import random
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


router = APIRouter(prefix="/trivia", tags=["trivia"])

log = logging.getLogger("trivia")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "trivia")

BUFFER_DB = os.environ.get("ZS_BUFFER_DB", "/home/zipscores_buffer.db")
TIMEZONE = os.environ.get("ZS_TIMEZONE", "America/Chicago")


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


# --------------------------------------------------------------------------- #
# The question bank
# --------------------------------------------------------------------------- #
#
# A mix of real IT/security trivia, a little computing history, and a few
# in-jokes about the other games in this app. `correct` is an index into
# `choices`.

QUESTIONS = [
    {"q": "\"Patch Tuesday\" -- Microsoft's regular monthly update release -- falls on which day?",
     "choices": ["Monday", "Tuesday", "Wednesday", "Friday"], "correct": 1},
    {"q": "What does \"CVE\" stand for?",
     "choices": ["Common Vulnerabilities and Exposures", "Critical Virus Event",
                 "Cyber Vulnerability Engine", "Certified Vendor Endpoint"], "correct": 0},
    {"q": "Which of these is NOT a real CVSS severity rating?",
     "choices": ["Low", "Medium", "Severe", "Critical"], "correct": 2},
    {"q": "What year was \"Creeper,\" generally considered the first computer virus, created?",
     "choices": ["1971", "1983", "1995", "1969"], "correct": 0},
    {"q": "In \"rubber duck debugging,\" what do you actually do?",
     "choices": ["Explain your code line by line to a rubber duck",
                 "Throw a rubber duck at a failing test",
                 "Compile from a duck-shaped USB drive",
                 "Use duck-typing in Python"], "correct": 0},
    {"q": "Which of our arcade games is a tribute to Frogger?",
     "choices": ["PatchMan", "Patchaga", "Ducker", "Patch Wall"], "correct": 2},
    {"q": "What does \"zero-day\" mean in security?",
     "choices": ["A vulnerability with a patch already released",
                 "A vulnerability unknown to the vendor, with no patch yet",
                 "A virus that deletes itself after 24 hours",
                 "A CVE older than a year"], "correct": 1},
    {"q": "A 2024 update from this vendor famously caused Blue Screens on millions of PCs worldwide.",
     "choices": ["Microsoft", "CrowdStrike", "McAfee", "Norton"], "correct": 1},
    {"q": "What does \"WSUS\" stand for?",
     "choices": ["Windows Server Update Services", "Web Security Update Suite",
                 "Windows System Utility Scanner", "Wide-Scale Update Sync"], "correct": 0},
    {"q": "The Morris Worm, one of the first internet worms, was released in what year?",
     "choices": ["1988", "1995", "1979", "2001"], "correct": 0},
    {"q": "Testing your own systems for vulnerabilities, with permission, is called:",
     "choices": ["Black hat hacking", "Penetration testing", "Phishing", "Doxxing"], "correct": 1},
    {"q": "Which of these is a real, historical email virus?",
     "choices": ["ILOVEYOU", "IHATEYOU", "PLEASEPATCHME", "SENDHELP"], "correct": 0},
    {"q": "In Patch Sweeper, what's the largest size of \"debt\" chunk called?",
     "choices": ["Legacy Monolith", "Big Bug", "Mega Dependency", "Ancient Module"], "correct": 0},
    {"q": "What does \"MFA\" stand for?",
     "choices": ["Multi-Factor Authentication", "Mandatory File Access",
                 "Managed Firewall Application", "Multiple Firewall Auth"], "correct": 0},
    {"q": "Which statement about incognito/private browsing is actually true?",
     "choices": ["It hides your IP address from websites",
                 "It mainly just avoids saving local browsing history",
                 "It encrypts all your traffic",
                 "It blocks all ads and trackers"], "correct": 1},
    {"q": "What was the first widely-used graphical web browser, released in 1993?",
     "choices": ["Internet Explorer", "Netscape Navigator", "Mosaic", "Chrome"], "correct": 2},
    {"q": "In PatchDefender, what do you click or tap to do?",
     "choices": ["Move a paddle", "Launch an interceptor at a target",
                 "Rotate a ship", "Steer a duck"], "correct": 1},
    {"q": "Phishing typically tries to trick you into:",
     "choices": ["Downloading a game", "Giving up sensitive info like passwords",
                 "Updating your OS", "Installing an ad blocker"], "correct": 1},
    {"q": "Which of these passwords is the weakest?",
     "choices": ["correcthorsebatterystaple", "P@ssw0rd123!", "password", "xK9#mQ2$vL7"],
     "correct": 2},
    {"q": "Which of our games features a duck towing a growing trail of collected patches?",
     "choices": ["Ducker", "Patch Trail", "Patch Wall", "Patchaga"], "correct": 1},
    {"q": "In computing, a \"patch\" originally referred to:",
     "choices": ["A literal piece of tape used to fix punch-card programs",
                 "A Windows-only term", "A type of virus", "A firewall rule"], "correct": 0},
    {"q": "What is a \"honeypot\" in cybersecurity?",
     "choices": ["A reward for finding bugs", "A decoy system meant to attract and detect attackers",
                 "A backup server", "A type of malware"], "correct": 1},
    {"q": "What does \"DDoS\" stand for?",
     "choices": ["Distributed Denial of Service", "Direct Data over SSL",
                 "Dynamic DNS Server", "Double Data over Sockets"], "correct": 0},
    {"q": "Tricking someone over the *phone* instead of email is called:",
     "choices": ["Phishing", "Vishing", "Smishing", "Whaling"], "correct": 1},
    {"q": "Most modern guidance recommends a minimum password length of:",
     "choices": ["4 characters", "6 characters", "8 characters", "12+ characters"], "correct": 3},
    {"q": "In Patch Wall, what happens to the ball's speed as a rally goes on?",
     "choices": ["It slows down", "It speeds up", "It stays the same", "It changes color"],
     "correct": 1},
    {"q": "\"Open source\" means:",
     "choices": ["The software is free forever", "The source code is publicly viewable and modifiable",
                 "It only runs on Linux", "It has no license"], "correct": 1},
    {"q": "Which of these is NOT an endpoint type defended in PatchDefender?",
     "choices": ["WORKSTATION", "SERVER", "LAPTOP", "MAINFRAME"], "correct": 3},
]

ROUNDS_PER_GAME = 10
QUESTION_SECONDS = 15.0
REVEAL_SECONDS = 8.0
LOBBY_MIN_PLAYERS = 1

PHASE_LOBBY = "lobby"
PHASE_QUESTION = "question"
PHASE_REVEAL = "reveal"
PHASE_FINAL = "final"


# --------------------------------------------------------------------------- #
# The one shared game
# --------------------------------------------------------------------------- #
#
# Singular and in-memory on purpose -- see the module docstring. A process
# restart clears it, which is exactly what "upload files, restart" already
# implies for every other piece of state in this app.

def _fresh_room():
    return {
        "phase": PHASE_LOBBY,
        "phase_started_at": time.time(),
        "round_index": -1,
        "questions": [],
        "players": {},   # token -> {name, score, answers: {round: {...}}}
        "persisted": False,
        "started_at": None,
    }


_lock = threading.Lock()
_room = _fresh_room()


def _clean_name(name):
    cleaned = str(name or "")
    cleaned = re.sub(r"[\x00-\x1F\x7F]", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip()[:40]


def _score_for(elapsed, correct):
    if not correct:
        return 0
    frac = max(0.0, min(1.0, elapsed / QUESTION_SECONDS))
    return round(100 + 900 * (1 - frac))


def _score_round(room):
    idx = room["round_index"]
    question = room["questions"][idx]
    started = room["phase_started_at"]
    for token, player in room["players"].items():
        answer = player["answers"].get(idx)
        if answer is None:
            player["answers"][idx] = {"choice": None, "correct": False, "points": 0}
            continue
        correct = answer["choice"] == question["correct"]
        points = _score_for(answer["answered_at"] - started, correct)
        answer["correct"] = correct
        answer["points"] = points
        player["score"] += points


def _all_answered(room):
    idx = room["round_index"]
    players = room["players"]
    if not players:
        return False
    return all(idx in p["answers"] for p in players.values())


def _advance_phase(room):
    now = time.time()
    elapsed = now - room["phase_started_at"]

    if room["phase"] == PHASE_QUESTION:
        if elapsed >= QUESTION_SECONDS or _all_answered(room):
            _score_round(room)
            room["phase"] = PHASE_REVEAL
            room["phase_started_at"] = now
    elif room["phase"] == PHASE_REVEAL:
        if elapsed >= REVEAL_SECONDS:
            if room["round_index"] + 1 >= len(room["questions"]):
                room["phase"] = PHASE_FINAL
                room["phase_started_at"] = now
                _persist_results(room)
            else:
                room["round_index"] += 1
                room["phase"] = PHASE_QUESTION
                room["phase_started_at"] = now


# --------------------------------------------------------------------------- #
# Storage (final results only)
# --------------------------------------------------------------------------- #

_db_lock = threading.Lock()
_db_dir = os.path.dirname(os.path.abspath(BUFFER_DB))
if _db_dir:
    os.makedirs(_db_dir, exist_ok=True)

_conn = sqlite3.connect(BUFFER_DB, check_same_thread=False, timeout=10.0)
_conn.row_factory = sqlite3.Row
_conn.execute(
    """
    CREATE TABLE IF NOT EXISTS trivia_results (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        player       TEXT    NOT NULL,
        score        INTEGER NOT NULL,
        correct      INTEGER NOT NULL,
        rounds       INTEGER NOT NULL,
        rank         INTEGER NOT NULL,
        players      INTEGER NOT NULL,
        created_at   TEXT    NOT NULL
    )
    """
)
_conn.execute("CREATE INDEX IF NOT EXISTS idx_trivia_score ON trivia_results(score DESC, id ASC)")
_conn.execute("CREATE INDEX IF NOT EXISTS idx_trivia_created ON trivia_results(created_at)")
_conn.commit()


def _write(sql, params=()):
    with _db_lock:
        cur = _conn.execute(sql, params)
        _conn.commit()
        return cur.lastrowid


def _rows(sql, params=()):
    with _db_lock:
        return [dict(r) for r in _conn.execute(sql, params).fetchall()]


def _persist_results(room):
    if room["persisted"]:
        return
    room["persisted"] = True
    ranked = sorted(room["players"].values(), key=lambda p: -p["score"])
    now = _utc_stamp()
    total_players = len(ranked)
    for rank, player in enumerate(ranked, start=1):
        correct = sum(1 for a in player["answers"].values() if a["correct"])
        _write(
            "INSERT INTO trivia_results "
            "(player, score, correct, rounds, rank, players, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (player["name"], player["score"], correct, len(room["questions"]),
             rank, total_players, now),
        )
    log.info("trivia: game finished, %d players", total_players)


# --------------------------------------------------------------------------- #
# Snapshot for polling clients
# --------------------------------------------------------------------------- #

def _leaderboard(room):
    ranked = sorted(room["players"].values(), key=lambda p: -p["score"])
    return [{"name": p["name"], "score": p["score"]} for p in ranked]


def _snapshot(room, token):
    now = time.time()
    elapsed = now - room["phase_started_at"]
    remaining = 0.0
    if room["phase"] == PHASE_QUESTION:
        remaining = max(0.0, QUESTION_SECONDS - elapsed)
    elif room["phase"] == PHASE_REVEAL:
        remaining = max(0.0, REVEAL_SECONDS - elapsed)

    out = {
        "phase": room["phase"],
        "remaining": round(remaining, 1),
        "questionSeconds": QUESTION_SECONDS,
        "revealSeconds": REVEAL_SECONDS,
        "round": room["round_index"] + 1,
        "rounds": len(room["questions"]),
        "playerCount": len(room["players"]),
        "leaderboard": _leaderboard(room)[:10],
    }

    if room["phase"] in (PHASE_QUESTION, PHASE_REVEAL) and room["questions"]:
        q = room["questions"][room["round_index"]]
        out["question"] = q["q"]
        out["choices"] = q["choices"]
        answered_count = sum(
            1 for p in room["players"].values() if room["round_index"] in p["answers"])
        out["answeredCount"] = answered_count

    if room["phase"] == PHASE_REVEAL and room["questions"]:
        q = room["questions"][room["round_index"]]
        out["correct"] = q["correct"]

    player = room["players"].get(token) if token else None
    if player is not None:
        out["you"] = {"name": player["name"], "score": player["score"]}
        if room["phase"] == PHASE_QUESTION:
            out["youAnswered"] = room["round_index"] in player["answers"]
        elif room["phase"] == PHASE_REVEAL:
            answer = player["answers"].get(room["round_index"])
            out["yourAnswer"] = answer

    return out


# --------------------------------------------------------------------------- #
# API
# --------------------------------------------------------------------------- #

class JoinIn(BaseModel):
    name: str = Field(default="", max_length=40)


class AnswerIn(BaseModel):
    token: str = Field(default="", max_length=64)
    choice: int = 0


def _reject(message, status=400):
    return JSONResponse(status_code=status, content={"ok": False, "error": message})


@router.post("/api/join")
def api_join(payload: JoinIn):
    name = _clean_name(payload.name)
    if not name:
        return _reject("Enter a name to join.")
    with _lock:
        _advance_phase(_room)
        if _room["phase"] != PHASE_LOBBY:
            return _reject("A round is already in progress. Wait for the next game.", 409)
        token = secrets.token_hex(12)
        _room["players"][token] = {"name": name, "score": 0, "answers": {}}
    return {"ok": True, "token": token}


@router.post("/api/start")
def api_start():
    with _lock:
        if len(_room["players"]) < LOBBY_MIN_PLAYERS:
            return _reject("Need at least one player to start.")
        picks = QUESTIONS[:]
        random.shuffle(picks)
        _room["questions"] = picks[:min(ROUNDS_PER_GAME, len(picks))]
        _room["round_index"] = 0
        _room["phase"] = PHASE_QUESTION
        _room["phase_started_at"] = time.time()
        _room["started_at"] = _utc_stamp()
        for player in _room["players"].values():
            player["score"] = 0
            player["answers"] = {}
    return {"ok": True}


@router.post("/api/reset")
def api_reset():
    global _room
    with _lock:
        _room = _fresh_room()
    return {"ok": True}


@router.post("/api/answer")
def api_answer(payload: AnswerIn):
    with _lock:
        _advance_phase(_room)
        if _room["phase"] != PHASE_QUESTION:
            return _reject("That round is no longer taking answers.", 409)
        player = _room["players"].get(payload.token)
        if player is None:
            return _reject("Unknown player. Rejoin the game.", 404)
        idx = _room["round_index"]
        if idx in player["answers"]:
            return _reject("Already answered this round.", 409)
        choice = max(0, min(3, int(payload.choice)))
        player["answers"][idx] = {"choice": choice, "answered_at": time.time()}
    return {"ok": True}


@router.get("/api/state")
def api_state(token: str = ""):
    with _lock:
        _advance_phase(_room)
        return _snapshot(_room, token)


# --------------------------------------------------------------------------- #
# Leaderboard
# --------------------------------------------------------------------------- #

@router.get("/api/board")
def api_board(limit: int = 10):
    limit = max(1, min(100, int(limit)))
    top = _rows(
        "SELECT player, score, correct, rounds, players, created_at "
        "FROM trivia_results ORDER BY score DESC, id ASC LIMIT ?",
        (limit,),
    )
    recent = _rows(
        "SELECT player, score, correct, rounds, rank, players, created_at "
        "FROM trivia_results ORDER BY id DESC LIMIT ?",
        (limit,),
    )
    return {"top": top, "recent": recent}


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
def host_page():
    return _serve("host.html")


@router.get("/join", include_in_schema=False)
def join_page():
    return _serve("join.html")


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
