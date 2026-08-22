"""Drive the PatchMan API the way the browser does, end to end.

The point is not that the endpoints return 200. It is that a run played by the
JavaScript engine, submitted exactly as the client submits it, is replayed by
the server, believed, scored to the same number, and lands on the board — and
that the checks around it fire when they should. A route test that never
replays a real run would pass just as happily against a server that trusts
whatever the client sends.

Everything runs against a throwaway database in the temp directory. Nothing
here touches the live one.

    python tools/patchman_smoke.py
"""

import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "app"))

SCRATCH = os.path.join(tempfile.gettempdir(), "patchman_smoke.db")
if os.path.exists(SCRATCH):
    os.remove(SCRATCH)
os.environ["ZS_BUFFER_DB"] = SCRATCH

from fastapi import FastAPI                       # noqa: E402
from fastapi.testclient import TestClient         # noqa: E402

import patchman                                   # noqa: E402

app = FastAPI()
app.include_router(patchman.router)
client = TestClient(app)

failures = []

# A three-minute run takes three minutes to play, and the server checks that.
# Rather than actually sit here for three minutes, the harness owns the clock
# the server reads and advances it in step with the simulation. Every code path
# stays real; only the wall clock is under test control.
CLOCK = {"ms": int(__import__("time").time() * 1000)}
patchman._now_ms = lambda: CLOCK["ms"]


def check(name, ok, detail=""):
    print(("  ok   " if ok else "  FAIL ") + name + (("  " + detail) if detail else ""))
    if not ok:
        failures.append(name)


def bot_trace(seed, max_ticks):
    proc = subprocess.run(
        ["node", os.path.join(HERE, "patchman_bot.mjs"),
         json.dumps({"seeds": [seed], "maxTicks": max_ticks})],
        capture_output=True, text=True, cwd=ROOT,
    )
    if proc.returncode != 0:
        raise SystemExit("bot failed:\n" + proc.stderr)
    return json.loads(proc.stdout)[0]


def play(player, max_ticks=120 * 60, beats=True):
    """One full round trip: take a session, play it, hand it in.

    The trace is replayed here with the *server's* tick budget, not the bot's.
    The bot stops steering after ``max_ticks``, but the game does not stop with
    it: PatchMan keeps running in its last direction until the lives are gone.
    Replaying on the bot's shorter budget would score a different run than the
    one the server sees, and the mismatch would look like a server bug.

    With ``beats`` false the clock never advances, which is what a run that was
    computed rather than played looks like from the server's side.
    """
    res = client.post("/patchman/api/session")
    assert res.status_code == 200, res.text
    issued = res.json()
    turns = bot_trace(issued["seed"], max_ticks)
    sim = patchman.replay(issued["seed"], turns)
    started = CLOCK["ms"]

    if beats:
        # The client beats every five seconds as it plays, which is 600 ticks.
        for tick in range(0, sim.tick + 1, 600):
            CLOCK["ms"] = started + int(tick * patchman.STEP_MS)
            client.post("/patchman/api/beat",
                        json={"session": issued["session"], "tick": tick})
        CLOCK["ms"] = started + sim.duration_ms()
        client.post("/patchman/api/beat",
                    json={"session": issued["session"], "tick": sim.tick})

    res = client.post("/patchman/api/score", json={
        "session": issued["session"],
        "player": player,
        "score": sim.score,
        "duration_ms": sim.duration_ms(),
        "turns": turns,
    })
    return issued, sim, res


def main():
    print("pages and assets")
    for path, kind in [("/patchman", "text/html"),
                       ("/patchman/board", "text/html"),
                       ("/patchman/static/game.mjs", "text/javascript"),
                       ("/patchman/static/sim.mjs", "text/javascript"),
                       ("/patchman/static/style.css", "text/css"),
                       ("/patchman/static/logo.png", "image/png"),
                       ("/patchman/static/favicon.png", "image/png"),
                       ("/patchman/static/share.png", "image/png")]:
        res = client.get(path)
        check(path, res.status_code == 200 and kind in res.headers["content-type"],
              str(res.status_code))

    res = client.get("/patchman/static/../../main.py")
    check("path traversal refused", res.status_code == 404, str(res.status_code))

    print("an honest run")
    issued, sim, res = play("Smoke Tester")
    body = res.json()
    check("accepted", res.status_code == 200, res.text[:120])
    check("counted", body.get("counted") is True, str(body))
    check("server scored it the same", body.get("score") == sim.score,
          "server=%s client=%s" % (body.get("score"), sim.score))
    check("level recorded", body.get("level") == sim.level,
          "server=%s client=%s" % (body.get("level"), sim.level))
    check("ranked", body.get("rank") == 1, str(body.get("rank")))
    print("     score %d, level %d, %d turns, %.1fs"
          % (sim.score, sim.level, len(sim.turns), sim.duration_ms() / 1000))

    print("the same run handed in twice")
    res = client.post("/patchman/api/score", json={
        "session": issued["session"], "player": "Smoke Tester",
        "score": sim.score, "duration_ms": sim.duration_ms(), "turns": sim.turns,
    })
    check("session cannot be reused", res.status_code >= 400, str(res.status_code))

    print("a claimed score the trace does not support")
    res = client.post("/patchman/api/session")
    issued = res.json()
    turns = bot_trace(issued["seed"], 120 * 30)
    honest = patchman.replay(issued["seed"], turns)
    started = CLOCK["ms"]
    for tick in range(0, honest.tick + 1, 600):
        CLOCK["ms"] = started + int(tick * patchman.STEP_MS)
        client.post("/patchman/api/beat",
                    json={"session": issued["session"], "tick": tick})
    CLOCK["ms"] = started + honest.duration_ms()
    res = client.post("/patchman/api/score", json={
        "session": issued["session"], "player": "Fibber",
        "score": honest.score + 50000,
        "duration_ms": honest.duration_ms(), "turns": turns,
    })
    body = res.json()
    check("inflated score refused",
          res.status_code >= 400 or body.get("counted") is False, str(body)[:140])

    print("a run computed rather than played")
    # Everything about this submission is true. The only thing wrong with it is
    # that no time passed: the trace claims a minute of play, and the session
    # was issued moments ago with no heartbeats behind it.
    _, fast, res = play("Speedrunner", beats=False)
    body = res.json()
    check("run with no elapsed time refused",
          res.status_code >= 400 or body.get("counted") is False, str(body)[:160])

    print("a made up session")
    res = client.post("/patchman/api/score", json={
        "session": "0" * 32, "player": "Ghost", "score": 100,
        "duration_ms": 30000, "turns": [4, 12],
    })
    check("unknown session refused", res.status_code >= 400, str(res.status_code))

    print("the board")
    for view in ("alltime", "season", "today", "volume"):
        res = client.get("/patchman/api/board?view=" + view + "&player=Smoke%20Tester")
        data = res.json()
        check("view " + view, res.status_code == 200 and "rows" in data,
              str(res.status_code))
    res = client.get("/patchman/api/board?view=nonsense")
    check("unknown view refused", res.status_code >= 400, str(res.status_code))

    res = client.get("/patchman/api/player/Smoke%20Tester")
    data = res.json()
    check("player summary", res.status_code == 200 and data.get("runs") == 1,
          str(data.get("runs")))
    check("summary counts patches", data.get("patches", 0) > 0,
          str(data.get("patches")))

    # A name nobody has played is a valid name, not an error: the page shows a
    # player with nothing on the board yet. Only a name that sanitises away is
    # refused.
    res = client.get("/patchman/api/player/Nobody%20At%20All")
    data = res.json()
    check("unseen player reads as empty",
          res.status_code == 200 and data.get("runs") == 0, str(res.status_code))
    res = client.get("/patchman/api/player/%20%20%20")
    check("empty name refused", res.status_code == 404, str(res.status_code))

    print("isolation")
    names = {r["name"] for r in patchman._rows(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    strays = {n for n in names
              if not n.startswith("patchman_") and n != "sqlite_sequence"}
    check("only patchman_ tables were created", not strays,
          ", ".join(sorted(strays)) or "none")

    print()
    if failures:
        print("%d checks failed: %s" % (len(failures), ", ".join(failures)))
        return 1
    print("every check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
