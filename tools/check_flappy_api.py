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
print("\nthe roster endpoint is gone")
status, _, _ = request("/flappy/api/roster")
check("GET /flappy/api/roster is 404", status == 404, "status %s" % status)

# --------------------------------------------------------------------------
print("\nname handling")
# Names are free text. Nothing is checked against the real roster, so a name
# nobody has ever heard of is accepted. Sanitising still applies. No runs
# exist yet at this point, so the typed spelling is what comes back.
cases = [
    ("Andrew Siebert", "Andrew Siebert"),
    ("  Andrew   Siebert ", "Andrew Siebert"),
    ("Zxqv Nonsense", "Zxqv Nonsense"),
    ("  lower case   person ", "lower case person"),
]
for typed, expect in cases:
    status, data = get_json("/flappy/api/player/" + urllib.request.quote(typed))
    check('"%s" is accepted as "%s"' % (typed, expect),
          status == 200 and data.get("ok") is True and data.get("player") == expect,
          str(data))

status, data = get_json("/flappy/api/player/Zxqv%20Nonsense")
check("a name with no runs is accepted, not rejected",
      status == 200 and data.get("ok") is True and data.get("runs") == 0,
      str(data))

# --------------------------------------------------------------------------
print("\nscore submission")

# The harness forges runs, because forgery is the thing being defended against.
# It drives the same simulation the server replays with, so a trace from here is
# one the server will agree with, which is what makes the negative cases mean
# something: they fail on plausibility, not on arithmetic.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "app"))
os.environ["ZS_BUFFER_DB"] = os.path.join(DATA, "zipscores_buffer.db")
os.environ["ZS_STATE_FILE"] = state
import flappy as flappy_module  # noqa: E402
from flappy_bot import bot_trace, played_trace, trace_for_score  # noqa: E402

SCORES = {}


def open_session():
    status, raw, _ = request("/flappy/api/session", "POST")
    if status != 200:
        return None
    return json.loads(raw)


def drop_open_sessions():
    """Unspent seeds count against a cap, and this harness abandons a lot."""
    conn = _db()
    if conn is None:
        return
    try:
        conn.execute("DELETE FROM flappy_sessions WHERE consumed_at IS NULL")
        conn.commit()
    finally:
        conn.close()


def fake_clock(session_id, duration_ms):
    """Move a session's clock back, so a run that takes a minute to play can be
    tested in a millisecond.

    This is the one thing a browser cannot do, which is exactly why the checks
    it stands in for are also exercised directly, without it, further down.
    """
    now = int(time.time() * 1000)
    conn = _db()
    if conn is None:
        return
    try:
        conn.execute(
            "UPDATE flappy_sessions SET issued_at = ?, first_beat = ?, "
            "last_beat = ?, first_tick = 0, last_tick = ?, beats = ? WHERE id = ?",
            (now - duration_ms - 800, now - duration_ms - 600, now - 100,
             int(duration_ms / flappy_module.STEP_MS),
             max(2, int(duration_ms / 5000)), session_id))
        conn.commit()
    finally:
        conn.close()


def post_run(player, trace, session=None, seed=None, score=None,
             duration_ms=None, clock=True, age=180):
    """Post one run. Everything not passed is taken from replaying the trace."""
    if age:
        backdate(age)
    if session is None:
        issued = open_session()
        if issued is None:
            return 0, b"no session", {}
        session, seed = issued["session"], issued["seed"]
    sim = flappy_module.replay(seed, trace)
    if clock:
        fake_clock(session, sim.duration_ms())
    status, raw, headers = request("/flappy/api/score", "POST", {
        "session": session, "player": player,
        "score": sim.score if score is None else score,
        "duration_ms": sim.duration_ms() if duration_ms is None else duration_ms,
        "flaps": trace,
    })
    # Track what actually reached the board, under the name the server chose,
    # so the assertions below compare against the same identity the API uses.
    if status == 200:
        body = json.loads(raw)
        if body.get("counted"):
            SCORES.setdefault(body["player"], []).append(body["score"])
    return status, raw, headers


def post_score(player, target, exact=False, **kw):
    """Post a played-looking run worth about `target` patches.

    Every session carries its own world, so a target is a request rather than
    an instruction: some seeds simply do not allow a run that length. With
    exact=True the harness keeps asking for worlds until it gets one where the
    target is reachable, which is what the tiebreak assertions need.
    """
    for _ in range(12 if exact else 1):
        issued = open_session()
        if issued is None:
            return 0, b"no session", -1
        trace, actual = trace_for_score(issued["seed"], target)
        if exact and actual != target:
            continue
        status, raw, _ = post_run(player, trace, session=issued["session"],
                                  seed=issued["seed"], **kw)
        return status, raw, actual
    return 0, b"no seed allowed that score", -1


status, raw, andrew1 = post_score("  Andrew   Siebert ", 6)
data = json.loads(raw)
check("posts and sanitises the name",
      status == 200 and data["player"] == "Andrew Siebert", str(data))
check("the run counts", data.get("counted") is True, str(data))
check("reports a personal best", data.get("personal_best") is True, str(data))
check("reports a rank", data.get("rank") == 1, str(data))
check("the server reports what the replay says, not what was claimed",
      data.get("score") == andrew1, "%s vs %s" % (data.get("score"), andrew1))

status, raw, dorie1 = post_score("Dorie Wallace", 11)
data = json.loads(raw)
check("second player posts", status == 200 and data["player"] == "Dorie Wallace", str(data))

status, raw, sam1 = post_score("Sam Rivera", 3)
check("third player posts", status == 200, raw[:200])

status, raw, andrew2 = post_score("Andrew Siebert", 2)
data = json.loads(raw)
check("a worse run is stored but is not a personal best",
      status == 200 and data.get("personal_best") is False, str(data))

status, raw, _ = post_score("Nobody At All", 4)
data = json.loads(raw)
check("a name that is not on the real roster is accepted",
      status == 200 and data["player"] == "Nobody At All", raw[:200])

issued = open_session()
status, raw, _ = post_run("Andrew Siebert", [], session=issued["session"],
                          seed=issued["seed"])
check("a zero run is refused", status == 400, raw[:200])

issued = open_session()
trace, _ = trace_for_score(issued["seed"], 4)
status, raw, _ = post_run("   ", trace, session=issued["session"], seed=issued["seed"])
check("a blank name is still refused", status == 400, raw[:200])

# --------------------------------------------------------------------------
print("\nthe seed comes from the server, and is spent once")

issued = open_session()
check("a session hands out a seed",
      issued and isinstance(issued.get("seed"), int) and issued.get("session"),
      str(issued))

trace, _ = trace_for_score(issued["seed"], 5)
status, raw, _ = post_run("Replay Attacker", trace,
                          session=issued["session"], seed=issued["seed"])
check("the first submission of a session is accepted", status in (200, 202), raw[:200])

status, raw, _ = post_run("Replay Attacker", trace,
                          session=issued["session"], seed=issued["seed"], age=0)
check("posting the same session twice is refused", status == 409, raw[:200])

status, raw, _ = post_run("Replay Attacker", trace,
                          session="not-a-real-session", seed=issued["seed"])
check("an invented session is refused", status in (400, 404), raw[:200])

# The old API let the client pick the seed, which is what made an offline
# search for a perfect run possible. It is not a field any more.
status, raw, _ = request("/flappy/api/score", "POST", {
    "player": "Seed Chooser", "score": 5, "seed": 12345,
    "duration_ms": 9000, "flaps": [0, 40, 80],
})
check("a submission with no session is refused", status == 422 or status == 400, raw[:200])

src_game = open(os.path.join(REPO, "app", "flappy", "game.mjs"), encoding="utf-8").read()
check("the client no longer picks a seed", "randomSeed" not in src_game,
      "game.mjs still imports randomSeed")
src_html = open(os.path.join(REPO, "app", "flappy", "index.html"), encoding="utf-8").read()
check("the seed is not shown in the page", 'id="seed"' not in src_html,
      "index.html still has a seed element")

# --------------------------------------------------------------------------
print("\nthe run has to match the world it was issued")

a = open_session()
b = open_session()
trace, _ = trace_for_score(a["seed"], 6)
status, raw, _ = post_run("World Swapper", trace, session=b["session"], seed=b["seed"],
                          score=flappy_module.replay(a["seed"], trace).score)
detail = json.loads(raw) if raw.startswith(b"{") else {}
check("a trace played on another seed does not survive the swap",
      status == 400 or detail.get("counted") is False,
      "%s %s" % (status, raw[:160]))
drop_open_sessions()

# --------------------------------------------------------------------------
print("\nthe score is derived, not accepted")

issued = open_session()
trace, actual = trace_for_score(issued["seed"], 5)
status, raw, _ = post_run("Score Inflator", trace, session=issued["session"],
                          seed=issued["seed"], score=actual + 90)
check("a claimed score the trace does not produce is refused", status == 400, raw[:200])
check("the refusal says what the run really scored",
      str(actual).encode() in raw, raw[:200])

issued = open_session()
trace, actual = trace_for_score(issued["seed"], 5)
status, raw, _ = post_run("Duration Faker", trace, session=issued["session"],
                          seed=issued["seed"], duration_ms=200)
data = json.loads(raw)
check("a claimed duration is ignored in favour of the replay", status in (200, 202), raw[:200])
conn = _db()
stored = conn.execute(
    "SELECT duration_ms FROM flappy_runs WHERE player_key = 'duration faker'").fetchone()
conn.close()
check("the stored duration comes from the replay",
      stored is not None and stored[0] > 1000, str(stored))

# --------------------------------------------------------------------------
print("\nthe run has to have taken the time it claims")

issued = open_session()
trace, actual = trace_for_score(issued["seed"], 12)
sim = flappy_module.replay(issued["seed"], trace)
status, raw, _ = post_run("Instant Runner", trace, session=issued["session"],
                          seed=issued["seed"], clock=False)
data = json.loads(raw)
check("a run computed instantly is accepted but does not count",
      status == 202 and data.get("counted") is False, raw[:220])
check("the player is told why", "sooner" in (data.get("reason") or "")
      or "contact" in (data.get("reason") or ""), str(data.get("reason")))

conn = _db()
row = conn.execute("SELECT verified, flags FROM flappy_runs "
                   "WHERE player_key = 'instant runner'").fetchone()
conn.close()
check("the run is stored, just not counted", row is not None and row[0] == 0, str(row))
check("the reason is recorded with it", row and "faster_than_real_time" in row[1], str(row))

status, data = get_json("/flappy/api/board?view=alltime")
check("an uncounted run stays off the board",
      "Instant Runner" not in [r["player"] for r in data["rows"]], str(data["rows"])[:200])

# --------------------------------------------------------------------------
print("\nthe run has to look hand timed")

issued = open_session()
forged = bot_trace(issued["seed"], limit=30)
status, raw, _ = post_run("Offline Solver", forged, session=issued["session"],
                          seed=issued["seed"])
data = json.loads(raw)
solved = flappy_module.replay(issued["seed"], forged).score
check("a solver trace scores well", solved >= 20, "scored %d" % solved)
check("a solver trace is accepted but does not count",
      status == 202 and data.get("counted") is False, raw[:220])
check("the player is told it looked machine timed",
      "hand timed" in (data.get("reason") or "") or "evenly" in (data.get("reason") or ""),
      str(data.get("reason")))

import random  # noqa: E402
rng = random.Random(4242)
issued = open_session()
played = played_trace(issued["seed"], rng, limit=30)
status, raw, _ = post_run("Honest Player", played, session=issued["session"],
                          seed=issued["seed"])
data = json.loads(raw)
check("the same run played by hand does count",
      status == 200 and data.get("counted") is True, raw[:220])

# The rejection that used to catch honest players: flap ticks are absolute and
# include the ready screen, so anyone who read the instructions first was
# refused for a trace that "did not line up with its length".
issued = open_session()
idle = 1800  # fifteen seconds looking at the start prompt
patient = [t + idle for t in played_trace(issued["seed"], random.Random(11), limit=8)]
status, raw, _ = post_run("Patient Reader", patient, session=issued["session"],
                          seed=issued["seed"])
data = json.loads(raw)
check("a long pause on the start screen is no longer a rejection",
      status in (200, 202), raw[:220])
check("and it is not treated as a forgery either", data.get("counted") is True, raw[:220])

# --------------------------------------------------------------------------
print("\nboards")
drop_open_sessions()
for view in ["alltime", "season", "today", "volume"]:
    status, data = get_json("/flappy/api/board?view=" + view)
    ok = status == 200 and len(data["rows"]) >= 4
    check("view %s lists the players" % view, ok, str(data)[:300])
    if ok:
        scores = [r["score"] for r in data["rows"]]
        check("view %s is ranked" % view, scores == sorted(scores, reverse=True), str(scores))
        players = [r["player"] for r in data["rows"]]
        check("view %s has one row per player" % view,
              len(players) == len(set(players)), str(players))

status, data = get_json("/flappy/api/board?view=alltime")
best = max(SCORES.items(), key=lambda kv: max(kv[1]))
check("alltime leader is the best single run",
      data["rows"][0]["player"] == best[0] and data["rows"][0]["score"] == max(best[1]),
      "%s vs %s" % (data["rows"][0], best))

status, data = get_json("/flappy/api/board?view=volume")
totals = {r["player"]: r["score"] for r in data["rows"]}
check("volume totals every run",
      totals.get("Andrew Siebert") == sum(SCORES["Andrew Siebert"]),
      "%s vs %s" % (totals.get("Andrew Siebert"), SCORES["Andrew Siebert"]))

status, data = get_json("/flappy/api/board?view=bogus")
check("unknown view is refused", status == 400, str(data))

status, data = get_json("/flappy/api/board?view=alltime&limit=1&player=sam%20rivera")
check("own row is pinned when outside the shown rows",
      len(data["rows"]) == 1 and data["pinned"] and data["pinned"]["player"] == "Sam Rivera",
      str(data))

status, data = get_json("/flappy/api/board?view=alltime&limit=20&player=sam%20rivera")
check("own row is not pinned when already shown", data["pinned"] is None, str(data))

# --------------------------------------------------------------------------
print("\ntiebreaking")
tied = []
for who in ["Tie One", "Tie Two", "Tie Three"]:
    status, raw, actual = post_score(who, 4, exact=True)
    tied.append((who, status, actual))
check("three runs of the same score were posted",
      all(s == 200 and a == 4 for _, s, a in tied), str(tied))

status, data = get_json("/flappy/api/board?view=alltime")
rows = [r for r in data["rows"] if r["player"].startswith("Tie ")]
check("equal scores rank by earliest run",
      [r["player"] for r in rows] == ["Tie One", "Tie Two", "Tie Three"],
      str([r["player"] for r in rows]))
stamps = [r["created_at"] for r in rows]
check("tied rows are in ascending timestamp order", stamps == sorted(stamps), str(stamps))

# --------------------------------------------------------------------------
print("\nplayer summary")
status, data = get_json("/flappy/api/player/Andrew%20Siebert")
check("summary counts every run", data["runs"] == len(SCORES["Andrew Siebert"]),
      "%s vs %s" % (data["runs"], SCORES["Andrew Siebert"]))
check("summary reports the best", data["best"] == max(SCORES["Andrew Siebert"]),
      str(data)[:300])
check("summary reports a rank per view",
      set(data["ranks"]) == {"alltime", "season", "today", "volume"}, str(data["ranks"]))

conn = _db()
n = conn.execute("SELECT COUNT(*) FROM flappy_runs").fetchone()[0]
counted = conn.execute("SELECT COUNT(*) FROM flappy_runs WHERE verified = 1").fetchone()[0]
conn.close()
check("every run is stored, not just personal bests", n > counted, "%d rows, %d counted" % (n, counted))
check("refused runs are kept for inspection", n - counted >= 2, "%d not counted" % (n - counted))

# Free-text names still resolve to one identity per spelling-insensitive key,
# and the summary reports the spelling the board shows rather than the one in
# the URL.
status, data = get_json("/flappy/api/player/ANDREW%20SIEBERT")
check("a different spelling is the same player",
      status == 200 and data["runs"] == len(SCORES["Andrew Siebert"]), str(data)[:300])
check("the summary reports the stored spelling",
      data.get("player") == "Andrew Siebert", str(data.get("player")))

# --------------------------------------------------------------------------
print("\ninput traces")
issued = open_session()
status, raw, _ = post_run("Sam Rivera", [10, 5, 20], session=issued["session"],
                          seed=issued["seed"])
check("an out of order input trace is refused", status == 400, raw[:200])

issued = open_session()
status, raw, _ = post_run("Sam Rivera", [10, 99999999], session=issued["session"],
                          seed=issued["seed"])
check("an input trace outside the run is refused", status == 400, raw[:200])

issued = open_session()
status, raw, _ = post_run("Sam Rivera", list(range(0, 6000)),
                          session=issued["session"], seed=issued["seed"])
check("an oversized input trace is refused", status == 400, raw[:200])

# --------------------------------------------------------------------------
print("\nrate limiting")
drop_open_sessions()
status, raw, _ = post_score("Rate Test", 3)
check("a first run posts", status == 200, raw[:200])

status, raw, _ = post_score("Rate Test", 3, age=0)
check("a second run posted immediately is rate limited", status == 429, raw[:200])

status, raw, _ = post_score("Rate Test", 12, age=5)
check("a long run cannot be posted seconds after the last", status == 429, raw[:200])

status, raw, _ = post_score("Other Player", 3, age=0)
check("the limit is per player, not global", status in (200, 202), raw[:200])

# --------------------------------------------------------------------------
print("\npruning")
drop_open_sessions()
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
print("\nre-judging runs that predate any of this")

# A run that has already been judged and held back must stay held back. The
# audit's work queue is "flags IS NULL" for exactly this reason: widening it to
# something like "verified = 0" would quietly put every caught cheat back on the
# board the next time the container restarted, and nothing else would notice.
conn = _db()
conn.execute(
    "INSERT INTO flappy_runs (player, player_key, score, seed, duration_ms, "
    "flaps, created_at, verified, flags) VALUES "
    "('Already Caught', 'already caught', 99, 4242, 60000, NULL, "
    "strftime('%Y-%m-%dT%H:%M:%SZ', 'now', '-90 days'), 0, ?)",
    (json.dumps(["machine_timing"]),))
conn.commit()
conn.close()
flappy_module.audit_legacy_runs()
conn = _db()
still = conn.execute(
    "SELECT verified, flags FROM flappy_runs WHERE player_key = 'already caught'"
).fetchone()
conn.close()
check("a run already held back is not quietly restored by a later audit",
      still is not None and still[0] == 0, str(still))
check("its original reason survives", still and "machine_timing" in still[1], str(still))

status, data = get_json("/flappy/api/board?view=alltime")
check("and it never reaches the board",
      "Already Caught" not in [r["player"] for r in data["rows"]], str(data["rows"])[:200])

conn = _db()
conn.execute("UPDATE flappy_runs SET verified = 0, flags = NULL")
conn.commit()
conn.close()
result = flappy_module.audit_legacy_runs()
check("every unjudged run is looked at", result["checked"] > 0, str(result))
conn = _db()
unjudged = conn.execute(
    "SELECT COUNT(*) FROM flappy_runs WHERE flags IS NULL").fetchone()[0]
kept_back = conn.execute(
    "SELECT COUNT(*) FROM flappy_runs WHERE verified = 1").fetchone()[0]
conn.close()
check("nothing is left unjudged", unjudged == 0, "%d left" % unjudged)
check("runs with no trace left are kept rather than voided", kept_back > 0,
      "%d kept" % kept_back)
check("running it again does nothing", flappy_module.audit_legacy_runs()["checked"] == 0)

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

# Stronger than "does not write it": since names are free text, the module has
# no reason to open the real leaderboard state file at all. The docstring still
# mentions the file by name, so this looks for the code references.
flappy_src = open(os.path.join(REPO, "app", "flappy.py"), encoding="utf-8").read()
check("flappy.py never reads the leaderboard state file",
      "ZS_STATE_FILE" not in flappy_src and "STATE_FILE" not in flappy_src,
      "found a reference to the real state file")

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
