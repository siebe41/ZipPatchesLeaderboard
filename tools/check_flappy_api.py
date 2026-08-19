"""End to end check for the Flappy Duck router.

Exercises every existing endpoint, every new one, and proves that posting a
score leaves leaderboard.json and history.json byte for byte identical.

Run the app first, then:  python tools/check_flappy_api.py http://127.0.0.1:8931
"""
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8931"
DATA = sys.argv[2] if len(sys.argv) > 2 else os.path.join(
    os.environ.get("TEMP", "/tmp"), "flappy-test")
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

passed = 0
failed = []


def reset_runs():
    """Start from an empty board so the counts below mean something.

    Deleting from a second connection is safe: the server holds its own
    connection to the same file and SQLite serialises the two.
    """
    conn = _db()
    if conn is None:
        return
    try:
        conn.execute("DELETE FROM flappy_runs")
        conn.execute("DELETE FROM sqlite_sequence WHERE name = 'flappy_runs'")
        conn.commit()
    except Exception:
        pass
    finally:
        conn.close()


def _db():
    import sqlite3
    db = os.path.join(DATA, "zipscores_buffer.db")
    if not os.path.exists(db):
        return None
    return sqlite3.connect(db, timeout=10.0)


def backdate(seconds):
    """Age every stored run, so the next submission is not rate limited.

    Shifting them all by the same amount leaves their relative order intact,
    which is what the tiebreak assertions depend on.
    """
    conn = _db()
    if conn is None:
        return
    try:
        conn.execute(
            "UPDATE flappy_runs SET created_at = "
            "strftime('%Y-%m-%dT%H:%M:%SZ', created_at, ?)", ("-%d seconds" % seconds,))
        conn.commit()
    finally:
        conn.close()


def check(name, ok, detail=""):
    global passed
    if ok:
        passed += 1
        print("  ok   %s" % name)
    else:
        failed.append(name)
        print("  FAIL %s  %s" % (name, detail))


def request(path, method="GET", body=None):
    url = BASE + path
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as res:
            return res.status, res.read(), dict(res.headers)
    except urllib.error.HTTPError as err:
        return err.code, err.read(), dict(err.headers)


def get_json(path):
    status, raw, _ = request(path)
    try:
        return status, json.loads(raw)
    except ValueError:
        return status, None


def digest(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


# --------------------------------------------------------------------------
reset_runs()

print("\nexisting endpoints still respond")
for path in ["/", "/leaderboard", "/history", "/accommodation", "/accommodations",
             "/backfill", "/commissioner", "/api/accommodations", "/favicon.ico",
             "/logo.png"]:
    status, _, _ = request(path)
    check(path, status in (200, 404), "status %s" % status)

# --------------------------------------------------------------------------
print("\nisolation")
state = os.path.join(DATA, "leaderboard.json")
history = os.path.join(DATA, "history.json")
before = (digest(state), digest(history))
before_state = json.load(open(state, encoding="utf-8"))

# --------------------------------------------------------------------------
print("\ngame page and static files")
status, raw, headers = request("/flappy")
check("GET /flappy is html", status == 200 and b"<canvas" in raw, "status %s" % status)

status, raw, headers = request("/flappy/board")
check("GET /flappy/board is html",
      status == 200 and b"<table" in raw and b"Hall of fame" in raw, "status %s" % status)

for name, expect in [("game.mjs", "text/javascript"), ("style.css", "text/css"),
                     ("atlas.png", "image/png"), ("atlas.json", "application/json"),
                     ("sim.mjs", "text/javascript"), ("config.mjs", "text/javascript"),
                     ("board.mjs", "text/javascript"), ("board.css", "text/css")]:
    status, raw, headers = request("/flappy/static/" + name)
    ctype = headers.get("content-type", "")
    check("GET /flappy/static/%s" % name,
          status == 200 and len(raw) > 0 and expect in ctype,
          "status %s type %s" % (status, ctype))

for attack in ["../main.py", "..%2fmain.py", "../../etc/passwd", "%2e%2e/main.py"]:
    status, raw, _ = request("/flappy/static/" + attack)
    check("traversal blocked: %s" % attack,
          status == 404 or b"FastAPI" not in raw, "status %s" % status)

# --------------------------------------------------------------------------
print("\nroster")
status, data = get_json("/flappy/api/roster")
check("GET /flappy/api/roster", status == 200 and "Andrew Siebert" in data["players"],
      str(data))

# --------------------------------------------------------------------------
print("\nname binding")
cases = [
    ("Andrew Siebert", "Andrew Siebert"),
    ("andrew siebert", "Andrew Siebert"),
    ("  Andrew   Siebert ", "Andrew Siebert"),
]
for typed, expect in cases:
    status, data = get_json("/flappy/api/player/" + urllib.request.quote(typed))
    check('"%s" resolves to "%s"' % (typed, expect),
          status == 200 and data.get("player") == expect, str(data))

status, data = get_json("/flappy/api/player/Zxqv%20Nonsense")
check("unknown name is rejected", status == 404 and "No leaderboard player" in
      str(data.get("error")), str(data))

# --------------------------------------------------------------------------
print("\nscore submission")


def post_score(player, score, duration_ms=None, seed=12345, flaps=None, age=180):
    """Post a run. Ages the existing rows first so the rate limiter, which is
    tested separately below, does not get in the way of the board assertions."""
    if age:
        backdate(age)
    if duration_ms is None:
        duration_ms = int((340 + (score - 1) * 160 + 26 - 79) / 110 * 1000) + 400
    if flaps is None:
        flaps = list(range(0, max(1, score) * 40, 40))
    return request("/flappy/api/score", "POST", {
        "player": player, "score": score, "seed": seed,
        "duration_ms": duration_ms, "flaps": flaps,
    })


status, raw, _ = post_score("andrew siebert", 12)
data = json.loads(raw)
check("posts and canonicalises the name",
      status == 200 and data["player"] == "Andrew Siebert", str(data))
check("reports a personal best", data.get("personal_best") is True, str(data))
check("reports a rank", data.get("rank") == 1, str(data))

status, raw, _ = post_score("Dorie Wallace", 25)
data = json.loads(raw)
check("second player posts", status == 200 and data["player"] == "Dorie Wallace", str(data))

status, raw, _ = post_score("Sam Rivera", 7)
check("third player posts", status == 200, raw[:200])

status, raw, _ = post_score("Andrew Siebert", 4)
data = json.loads(raw)
check("a worse run is stored but is not a personal best",
      status == 200 and data.get("personal_best") is False, str(data))

status, raw, _ = post_score("Nobody At All", 5)
check("unknown player is refused", status == 400, raw[:200])

status, raw, _ = post_score("Andrew Siebert", 0)
check("a zero run is refused", status == 400, raw[:200])

# --------------------------------------------------------------------------
print("\nboards")
for view in ["alltime", "season", "today", "volume"]:
    status, data = get_json("/flappy/api/board?view=" + view)
    ok = status == 200 and len(data["rows"]) == 3
    check("view %s has one row per player" % view, ok, str(data)[:300])
    if ok:
        scores = [r["score"] for r in data["rows"]]
        check("view %s is ranked" % view, scores == sorted(scores, reverse=True), str(scores))

status, data = get_json("/flappy/api/board?view=alltime")
check("alltime leader is the best single run",
      data["rows"][0]["player"] == "Dorie Wallace" and data["rows"][0]["score"] == 25,
      str(data["rows"][0]))

status, data = get_json("/flappy/api/board?view=volume")
totals = {r["player"]: r["score"] for r in data["rows"]}
check("volume totals every run", totals.get("Andrew Siebert") == 16, str(totals))

status, data = get_json("/flappy/api/board?view=bogus")
check("unknown view is refused", status == 400, str(data))

status, data = get_json("/flappy/api/board?view=alltime&limit=1&player=sam%20rivera")
check("own row is pinned when outside the shown rows",
      len(data["rows"]) == 1 and data["pinned"] and data["pinned"]["player"] == "Sam Rivera",
      str(data))

status, data = get_json("/flappy/api/board?view=alltime&limit=10&player=sam%20rivera")
check("own row is not pinned when already shown", data["pinned"] is None, str(data))

# --------------------------------------------------------------------------
print("\ntiebreaking")
for who in ["Andrew Siebert", "Dorie Wallace", "Sam Rivera"]:
    post_score(who, 33)
status, data = get_json("/flappy/api/board?view=alltime")
top3 = [r["player"] for r in data["rows"][:3]]
check("equal scores rank by earliest run",
      top3 == ["Andrew Siebert", "Dorie Wallace", "Sam Rivera"], str(top3))
stamps = [r["created_at"] for r in data["rows"][:3]]
check("tied rows are in ascending timestamp order", stamps == sorted(stamps), str(stamps))

# --------------------------------------------------------------------------
print("\nplayer summary")
status, data = get_json("/flappy/api/player/Andrew%20Siebert")
check("summary counts every run", data["runs"] == 3, str(data)[:300])
check("summary reports the best", data["best"] == 33, str(data)[:300])
check("summary reports a rank per view",
      set(data["ranks"]) == {"alltime", "season", "today", "volume"}, str(data["ranks"]))

conn = _db()
n = conn.execute("SELECT COUNT(*) FROM flappy_runs").fetchone()[0]
conn.close()
check("every run is stored, not just personal bests", n == 7, "rows %d" % n)

# --------------------------------------------------------------------------
print("\nplausibility and rate limiting")
floor_ms = int((340 + 49 * 160 + 26 - 79) / 110 * 1000)

status, raw, _ = post_score("Andrew Siebert", 50, duration_ms=1000)
check("a score faster than the obstacles arrive is refused", status == 400, raw[:200])
check("the refusal says how long it should take",
      b"at least" in raw and str(round(floor_ms / 1000.0, 1)).encode() in raw, raw[:200])

status, raw, _ = post_score("Andrew Siebert", 50, duration_ms=floor_ms + 500)
check("the same score at a possible pace is accepted", status == 200, raw[:200])

status, raw, _ = post_score("Andrew Siebert", 3, age=0)
check("a second run posted immediately is rate limited", status == 429, raw[:200])

status, raw, _ = post_score("Andrew Siebert", 40, age=5)
check("a long run cannot be posted seconds after the last", status == 429, raw[:200])

status, raw, _ = post_score("Dorie Wallace", 3, age=0)
check("the limit is per player, not global", status == 200, raw[:200])

status, raw, _ = post_score("Sam Rivera", 8, flaps=[10, 5, 20])
check("an out of order input trace is refused", status == 400, raw[:200])

status, raw, _ = post_score("Sam Rivera", 8, flaps=[10, 99999999])
check("an input trace outside the run is refused", status == 400, raw[:200])

status, raw, _ = post_score("Sam Rivera", 8, flaps=list(range(0, 6000)))
check("an oversized input trace is refused", status == 400, raw[:200])

conn = _db()
kept = conn.execute(
    "SELECT COUNT(*) FROM flappy_runs WHERE flaps IS NOT NULL").fetchone()[0]
conn.execute("UPDATE flappy_runs SET created_at = "
             "strftime('%Y-%m-%dT%H:%M:%SZ', created_at, '-400 days')")
conn.commit()
before_prune = conn.execute("SELECT COUNT(*) FROM flappy_runs").fetchone()[0]
conn.close()

# The server prunes once a day and already did so on its first submission, so
# call the function directly rather than restarting the app to see it happen.
sys.path.insert(0, os.path.join(REPO, "app"))
os.environ["ZS_BUFFER_DB"] = os.path.join(DATA, "zipscores_buffer.db")
os.environ["ZS_STATE_FILE"] = state
import flappy as flappy_module
pruned = flappy_module.prune_traces()

conn = _db()
traces = conn.execute(
    "SELECT COUNT(*) FROM flappy_runs WHERE flaps IS NOT NULL").fetchone()[0]
rows_left = conn.execute("SELECT COUNT(*) FROM flappy_runs").fetchone()[0]
conn.close()
check("old input traces are pruned", traces == 0 and pruned == kept and kept > 1,
      "had %d, pruned %d, left %d" % (kept, pruned, traces))
check("pruning keeps every score row", rows_left == before_prune,
      "rows %d, was %d" % (rows_left, before_prune))
check("pruning is skipped once it has run today", flappy_module.prune_traces() == 0)
check("the module file wins over the static folder",
      flappy_module.__file__.endswith("flappy.py"), flappy_module.__file__)

# --------------------------------------------------------------------------
print("\nseasons are derived, not stamped")
conn = _db()
cols = {r[1] for r in conn.execute("PRAGMA table_info(flappy_runs)")}
conn.close()
check("no season column is written", "season" not in cols, str(sorted(cols)))

status, data = get_json("/flappy/api/board?view=alltime")
fame = data["hall_of_fame"]
check("finished seasons appear in the hall of fame", len(fame) >= 1, str(fame)[:300])
if fame:
    check("a hall of fame entry names a winner and a season",
          fame[0]["player"] and re.match(r"^\d{4}-\d{2}$", fame[0]["season"]),
          str(fame[0]))
    check("the hall of fame excludes the current season",
          fame[0]["season"] < time.strftime("%Y-%m"), str(fame[0]["season"]))

status, data = get_json("/flappy/api/board?view=today")
check("a view with no qualifying run is empty, not an error",
      status == 200 and data["rows"] == [], str(data)[:200])

# --------------------------------------------------------------------------
print("\nisolation, after all that writing")
after = (digest(state), digest(history))
check("leaderboard.json is untouched", before[0] == after[0])
check("history.json is untouched", before[1] == after[1])
check("roster content is unchanged",
      json.load(open(state, encoding="utf-8")) == before_state)

db = os.path.join(DATA, "zipscores_buffer.db")
import sqlite3
conn = sqlite3.connect(db)
tables = {r[0] for r in conn.execute(
    "SELECT name FROM sqlite_master WHERE type='table'")}
check("flappy_runs exists", "flappy_runs" in tables, str(tables))
check("every new table is prefixed",
      all(t.startswith("flappy_") for t in tables
          if t not in {"buffer", "accommodations", "proofs", "sqlite_sequence"}),
      str(tables))
check("existing tables are still there", "buffer" in tables, str(tables))
conn.close()

# --------------------------------------------------------------------------
print("\nmain.py")
diff = os.popen('git -C "%s" diff --numstat -- app/main.py' % REPO).read().strip()
added = int(diff.split()[0]) if diff else 0
removed = int(diff.split()[1]) if diff else 0
check("main.py has at most two added lines and none removed",
      added <= 2 and removed == 0, diff or "no diff")

# --------------------------------------------------------------------------
print("\nconfig.mjs and flappy.py agree")
cfg = open(os.path.join(REPO, "app", "flappy", "config.mjs"), encoding="utf-8").read()
srv = open(os.path.join(REPO, "app", "flappy.py"), encoding="utf-8").read()


def js(name):
    return float(re.search(r"\b%s:\s*([-\d.]+)" % name, cfg).group(1))


def py(name):
    return float(re.search(r"^%s = ([-\d.]+)" % name, srv, re.M).group(1))


for jsname, pyname in [("scrollSpeed", "SCROLL_SPEED"), ("spacing", "SPACING"),
                       ("firstObstacleX", "FIRST_OBSTACLE_X"), ("tileW", "TILE_W"),
                       ("duckX", "DUCK_X"), ("duckW", "DUCK_W")]:
    check("%s matches %s" % (jsname, pyname), js(jsname) == py(pyname),
          "%s vs %s" % (js(jsname), py(pyname)))

# --------------------------------------------------------------------------
print("\n%d passed, %d failed" % (passed, len(failed)))
for name in failed:
    print("  failed: " + name)
sys.exit(1 if failed else 0)
