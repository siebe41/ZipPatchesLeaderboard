"""Drive the Patchaga API the way the browser does, end to end.

The point is not that the endpoints return 200. It is that a run played by the
JavaScript engine, submitted exactly as the client submits it, is replayed by
the server, believed, scored to the same number, and lands on the board -- and
that the checks around it fire when they should. A route test that never
replays a real run would pass just as happily against a server that trusts
whatever the client sends.

Everything runs against a throwaway database in the temp directory. Nothing
here touches the live one.

    python tools/patchaga_smoke.py
"""

import json
import os
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "app"))

SCRATCH = os.path.join(tempfile.gettempdir(), "patchaga_smoke.db")
if os.path.exists(SCRATCH):
    os.remove(SCRATCH)
os.environ["ZS_BUFFER_DB"] = SCRATCH

from fastapi import FastAPI                       # noqa: E402
from fastapi.testclient import TestClient         # noqa: E402

import patchaga                                   # noqa: E402

app = FastAPI()
app.include_router(patchaga.router)
client = TestClient(app)

failures = []

# A two-minute run takes two minutes to play, and the server checks that. Rather
# than actually sit here for two minutes, the harness owns the clock the server
# reads and advances it in step with the simulation. Every code path stays real;
# only the wall clock is under test control.
CLOCK = {"ms": int(time.time() * 1000)}
patchaga._now_ms = lambda: CLOCK["ms"]


def check(name, ok, detail=""):
    print(("  ok   " if ok else "  FAIL ") + name + (("  " + detail) if detail else ""))
    if not ok:
        failures.append(name)


def bot_trace(seed, minutes, human=True):
    """One bot run against an exact seed, returned as the client would send it.

    ``human`` is on by default, and is the difference between a trace the board
    should accept and one it should refuse. The bot's default mode re-decides
    its direction every tick, which produces steering far too fast and far too
    evenly spaced to have come from a hand -- so the honest-run checks here need
    the hand, and the check that the anti-cheat still fires needs the machine.
    """
    proc = subprocess.run(
        ["node", os.path.join(HERE, "patchaga_bot.mjs"),
         "1", "--seed", str(seed), "--minutes", str(minutes), "--traces"]
        + (["--human"] if human else []),
        capture_output=True, text=True, cwd=ROOT,
    )
    if proc.returncode != 0:
        raise SystemExit("bot failed:\n" + proc.stderr)
    return json.loads(proc.stdout)[0]["inputs"]


def play(player, minutes=2, beats=True, human=True):
    """One full round trip: take a session, play it, hand it in.

    The trace is replayed here with the *server's* tick budget, not the bot's.
    The bot stops steering after its ceiling but the game does not stop with it:
    the duck keeps flying in its last direction until the lives are gone.
    Replaying on the bot's shorter budget would score a different run than the
    one the server sees, and the mismatch would look like a server bug.

    With ``beats`` false the clock never advances, which is what a run that was
    computed rather than played looks like from the server's side.
    """
    res = client.post("/patchaga/api/session")
    assert res.status_code == 200, res.text
    issued = res.json()
    inputs = bot_trace(issued["seed"], minutes, human)
    sim = patchaga.replay(issued["seed"], inputs)
    started = CLOCK["ms"]

    if beats:
        # The client beats every five seconds as it plays, which is 600 ticks.
        # Note what the clock is keyed on: the *absolute* tick, not the run's
        # duration. Those differ, because duration is measured from the tick the
        # first wave actually started rather than from tick zero, and pacing the
        # last beat by duration would report more simulated time than wall time
        # and flag an honest run as having outrun the clock.
        for tick in range(0, sim.tick + 1, 600):
            CLOCK["ms"] = started + int(tick * patchaga.STEP_MS)
            client.post("/patchaga/api/beat",
                        json={"session": issued["session"], "tick": tick})
        CLOCK["ms"] = started + int(sim.tick * patchaga.STEP_MS)
        client.post("/patchaga/api/beat",
                    json={"session": issued["session"], "tick": sim.tick})

    res = client.post("/patchaga/api/score", json={
        "session": issued["session"],
        "player": player,
        "score": sim.score,
        "duration_ms": sim.duration_ms(),
        "inputs": inputs,
    })
    return issued, sim, res


def main():
    print("pages and assets")
    for path, kind in [("/patchaga", "text/html"),
                       ("/patchaga/board", "text/html"),
                       ("/patchaga/static/game.mjs", "text/javascript"),
                       ("/patchaga/static/sim.mjs", "text/javascript"),
                       ("/patchaga/static/board.mjs", "text/javascript"),
                       ("/patchaga/static/style.css", "text/css"),
                       ("/patchaga/static/board.css", "text/css"),
                       ("/patchaga/static/logo.png", "image/png"),
                       ("/patchaga/static/favicon.png", "image/png"),
                       ("/patchaga/static/share.png", "image/png")]:
        res = client.get(path)
        check(path, res.status_code == 200 and kind in res.headers["content-type"],
              str(res.status_code))

    res = client.get("/patchaga/static/../../main.py")
    check("path traversal refused", res.status_code == 404, str(res.status_code))

    print("an honest run")
    issued, sim, res = play("Smoke Tester")
    body = res.json()
    check("accepted", res.status_code == 200, res.text[:120])
    check("counted", body.get("counted") is True, str(body))
    check("server scored it the same", body.get("score") == sim.score,
          "server=%s client=%s" % (body.get("score"), sim.score))
    check("wave recorded", body.get("wave") == sim.wave,
          "server=%s client=%s" % (body.get("wave"), sim.wave))
    check("ranked", body.get("rank") == 1, str(body.get("rank")))
    print("     score %d, wave %d, %d bugs, %d inputs, %.1fs"
          % (sim.score, sim.wave, sim.bugs_patched, len(sim.inputs),
             sim.duration_ms() / 1000))

    print("the same run handed in twice")
    res = client.post("/patchaga/api/score", json={
        "session": issued["session"], "player": "Smoke Tester",
        "score": sim.score, "duration_ms": sim.duration_ms(), "inputs": sim.inputs,
    })
    check("session cannot be reused", res.status_code >= 400, str(res.status_code))

    print("a claimed score the trace does not support")
    res = client.post("/patchaga/api/session")
    issued = res.json()
    inputs = bot_trace(issued["seed"], 1)
    honest = patchaga.replay(issued["seed"], inputs)
    started = CLOCK["ms"]
    for tick in range(0, honest.tick + 1, 600):
        CLOCK["ms"] = started + int(tick * patchaga.STEP_MS)
        client.post("/patchaga/api/beat",
                    json={"session": issued["session"], "tick": tick})
    CLOCK["ms"] = started + int(honest.tick * patchaga.STEP_MS)
    res = client.post("/patchaga/api/score", json={
        "session": issued["session"], "player": "Fibber",
        "score": honest.score + 50000,
        "duration_ms": honest.duration_ms(), "inputs": inputs,
    })
    body = res.json()
    check("inflated score refused",
          res.status_code >= 400 or body.get("counted") is False, str(body)[:140])

    print("a run computed rather than played")
    # Everything about this submission is true. The only thing wrong with it is
    # that no time passed: the trace claims minutes of play, and the session was
    # issued moments ago with no heartbeats behind it.
    _, fast, res = play("Speedrunner", beats=False)
    body = res.json()
    check("run with no elapsed time refused",
          res.status_code >= 400 or body.get("counted") is False, str(body)[:160])

    print("a run steered by a solver")
    # Same game, same server, same round trip -- the only difference is that the
    # bot re-decides its direction every tick instead of every few hundred
    # milliseconds. Nothing about the run is forged and it replays perfectly;
    # what gives it away is that no hand produces steering that fast or that
    # evenly spaced. The honest run above and this one bracket the threshold,
    # which a one-sided test could not do.
    _, solver, res = play("Solver", human=False)
    body = res.json()
    check("machine steering refused", body.get("counted") is False, str(body)[:140])
    check("solver run still replays",
          body.get("score") == solver.score,
          "server=%s client=%s" % (body.get("score"), solver.score))

    print("a hand rolling from one key to the other")
    # The bug this guards against was found by playing the game in a browser and
    # watching an honest run get refused. Changing direction quickly produces a
    # key-up and a key-down microseconds apart, which reach the trace as a
    # neutral and a direction on the same tick. Counted as two presses that is
    # inhumanly fast, and it happens to everyone. Counted as one motion, which
    # is what it is, it is nothing.
    roll = []
    t = 0
    for i in range(40):
        roll.append(t * 4 + patchaga.A_NEUTRAL)          # let go of one key
        roll.append(t * 4 + (i % 2))                     # press the other
        t += 34 + (i * 11) % 47                          # a hand, so not a metronome
    stats = patchaga.interval_stats(roll)
    check("a roll counts as one input", stats["short"] == 0,
          "%d short gaps over %d intervals" % (stats["short"], stats["intervals"]))
    check("a roll does not skew the spread",
          stats["modal_share"] < patchaga.MODAL_SHARE_LIMIT,
          "modal share %.2f" % stats["modal_share"])

    # What that is worth is only visible against the alternative. Counting the
    # release as an input of its own puts half the gaps on zero, which is both a
    # spike and a pile of impossibly fast presses -- two flags on a trace whose
    # only crime is changing direction.
    raw = [c // 4 for c in roll]
    raw_gaps = [raw[i + 1] - raw[i] for i in range(len(raw) - 1)]
    naive_short = sum(1 for g in raw_gaps if g < patchaga.MIN_HUMAN_GAP_TICKS)
    check("and the naive count would have refused it",
          naive_short > patchaga.MAX_SHORT_GAPS,
          "%d short gaps if a release counted as a press" % naive_short)

    # The other half of the same claim: the collapse must not give a solver a
    # way to launder its rate. Presses on adjacent ticks stay adjacent whether
    # or not a neutral is inserted before each one.
    fast = []
    for i in range(60):
        fast.append(i * 4 + patchaga.A_NEUTRAL)
        fast.append(i * 4 + (i % 2))
    stats = patchaga.interval_stats(fast)
    check("presses a tick apart are still caught",
          stats["short"] > patchaga.MAX_SHORT_GAPS,
          "%d short gaps, allowed %d" % (stats["short"], patchaga.MAX_SHORT_GAPS))

    print("the accuracy check")
    # A solver picks the tick that hits, and a hand does not. Synthesising a
    # real solver would mean searching the trace shot by shot, which costs a
    # full replay per shot; the check itself is what matters, so it is fed a
    # real replayed run with its two counters set. Both directions are tested,
    # because a threshold that flags everything passes a one-sided test.
    probe = patchaga.replay(issued["seed"], inputs)
    probe.shots_fired = 200
    probe.bugs_patched = 198
    check("perfect aim flagged",
          "inhuman_accuracy" in patchaga.hand_flags(probe), "99% hit rate")
    probe.bugs_patched = 120
    check("ordinary aim not flagged",
          "inhuman_accuracy" not in patchaga.hand_flags(probe), "60% hit rate")
    probe.shots_fired = patchaga.ACCURACY_MIN_SHOTS - 1
    probe.bugs_patched = probe.shots_fired
    check("too few shots to judge",
          "inhuman_accuracy" not in patchaga.hand_flags(probe),
          "%d shots" % probe.shots_fired)

    print("a made up session")
    res = client.post("/patchaga/api/score", json={
        "session": "0" * 32, "player": "Ghost", "score": 100,
        "duration_ms": 30000, "inputs": [4, 12],
    })
    check("unknown session refused", res.status_code >= 400, str(res.status_code))

    print("the board")
    for view in ("alltime", "season", "today", "volume"):
        res = client.get("/patchaga/api/board?view=" + view + "&player=Smoke%20Tester")
        data = res.json()
        check("view " + view, res.status_code == 200 and "rows" in data,
              str(res.status_code))
    res = client.get("/patchaga/api/board?view=nonsense")
    check("unknown view refused", res.status_code >= 400, str(res.status_code))

    res = client.get("/patchaga/api/player/Smoke%20Tester")
    data = res.json()
    check("player summary", res.status_code == 200 and data.get("runs") == 1,
          str(data.get("runs")))
    check("summary counts bugs", data.get("bugs", 0) > 0, str(data.get("bugs")))

    # A name nobody has played is a valid name, not an error: the page shows a
    # player with nothing on the board yet. Only a name that sanitises away is
    # refused.
    res = client.get("/patchaga/api/player/Nobody%20At%20All")
    data = res.json()
    check("unseen player reads as empty",
          res.status_code == 200 and data.get("runs") == 0, str(res.status_code))
    res = client.get("/patchaga/api/player/%20%20%20")
    check("empty name refused", res.status_code == 404, str(res.status_code))

    print("isolation")
    names = {r["name"] for r in patchaga._rows(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    strays = {n for n in names
              if not n.startswith("patchaga_") and n != "sqlite_sequence"}
    check("only patchaga_ tables were created", not strays,
          ", ".join(sorted(strays)) or "none")

    print()
    if failures:
        print("%d checks failed: %s" % (len(failures), ", ".join(failures)))
        return 1
    print("every check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
