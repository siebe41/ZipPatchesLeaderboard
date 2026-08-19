"""Prove the Python simulation in flappy.py matches the JavaScript one.

The server decides scores by replaying a run, so the two engines agreeing is
not a nice property, it is the thing the whole anti-cheat rests on. A drift of
one tick in one branch would either reject honest runs or accept forged ones.

Both sides replay the same seeds and traces and every field of the result is
compared, floats included, with no tolerance. IEEE 754 says they should match
exactly, so anything less is a real difference worth failing over.

    python tools/check_sim_parity.py [cases]
"""

import json
import os
import random
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "app"))

# The module opens a database on import, so send it somewhere disposable.
os.environ.setdefault("ZS_BUFFER_DB", os.path.join(HERE, "_parity_scratch.db"))

import flappy  # noqa: E402

FIELDS = ("score", "duration_ms", "tick", "death_tick", "play_start_tick",
          "state", "cause", "duck_y", "duck_vy", "scroll_x", "flaps", "gaps")


def bot_trace(seed, limit=60):
    """A trace from a look-ahead bot, which is what a forged run looks like.

    Worth including for its own sake: these are long, high scoring runs that
    exercise far more of the simulation than random flapping ever reaches.
    """
    sim = flappy.Sim(seed)
    flaps = []
    last_flap = -999
    while sim.state != flappy.DEAD and sim.score < limit and sim.tick < 60000:
        want = False
        if sim.state == flappy.READY:
            # Nothing falls on the ready screen, so a bot that only reacts to
            # losing height would sit there forever. Something has to go first.
            want = True
        else:
            target = None
            # Scan from one obstacle back. Scoring happens when the duck's
            # centre passes the tile's centre, which is well before the tile is
            # behind it, so aiming at the next gap that early steers into the
            # tile the duck is still inside.
            i = max(0, sim.next_score_index - 1)
            while target is None and i < sim.next_score_index + 3:
                if sim.obstacle_screen_x(i) + flappy.TILE_W >= flappy.DUCK_X:
                    target = sim.gap_center(i)
                i += 1
            if target is None:
                target = 240.0
            want = sim.duck_y + flappy.DUCK_H / 2 > target + 22
        if want and sim.tick - last_flap >= 10:
            flaps.append(sim.tick)
            sim.queue_flap(sim.tick)
            last_flap = sim.tick
        sim.step()
    return flaps


def human_trace(rng, idle_ticks):
    """Ragged taps after a pause on the ready screen.

    The pause is the point. Flap ticks are absolute, so a player who reads the
    instructions before their first tap produces a trace whose numbers sit far
    beyond the length of the run itself.
    """
    flaps = []
    t = idle_ticks
    for _ in range(rng.randint(0, 90)):
        t += rng.randint(6, 95)
        flaps.append(t)
    return flaps


def build_cases(count):
    rng = random.Random(20260819)
    cases = []

    # Degenerate shapes first, because they are where a port usually differs.
    cases.append({"seed": 1, "flaps": []})
    cases.append({"seed": 0, "flaps": [0]})
    cases.append({"seed": 0xFFFFFFFF, "flaps": [0, 0, 0, 1, 1]})
    cases.append({"seed": 12345, "flaps": [5, 5, 5, 5, 5]})
    cases.append({"seed": 999, "flaps": [900, 5]})          # out of order
    cases.append({"seed": 424242, "flaps": [0, 1, 2, 3, 4, 5, 6, 7]})
    cases.append({"seed": 7, "flaps": [40000]})             # never plays
    cases.append({"seed": 8, "flaps": list(range(0, 4000, 11))})

    for _ in range(count):
        seed = rng.getrandbits(32)
        pick = rng.random()
        if pick < 0.35:
            cases.append({"seed": seed, "flaps": bot_trace(seed, rng.randint(3, 45))})
        elif pick < 0.75:
            cases.append({"seed": seed,
                          "flaps": human_trace(rng, rng.choice([0, 3, 240, 1500, 9000]))})
        else:
            n = rng.randint(0, 40)
            ticks = sorted(rng.randint(0, 3000) for _ in range(n))
            cases.append({"seed": seed, "flaps": ticks})
    return cases


def python_side(case):
    sim = flappy.replay(case["seed"], case["flaps"])
    return {
        "score": sim.score,
        "duration_ms": sim.duration_ms(),
        "tick": sim.tick,
        "death_tick": sim.death_tick,
        "play_start_tick": sim.play_start_tick,
        "state": sim.state,
        "cause": sim.cause,
        "duck_y": sim.duck_y,
        "duck_vy": sim.duck_vy,
        "scroll_x": sim.scroll_x,
        "flaps": sim.flaps,
        "gaps": sim.gaps,
    }


def main():
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 150
    cases = build_cases(count)

    dumper = os.path.join(HERE, "sim_parity_dump.mjs")
    proc = subprocess.run(
        ["node", dumper],
        input=json.dumps({"cases": cases}),
        capture_output=True, text=True, cwd=ROOT,
    )
    if proc.returncode != 0:
        print("node failed:\n" + proc.stderr)
        return 1
    js = json.loads(proc.stdout)

    failures = 0
    for i, case in enumerate(cases):
        mine = python_side(case)
        theirs = js[i]
        for field in FIELDS:
            if mine[field] != theirs[field]:
                failures += 1
                print("case %d seed=%d flaps=%d: %s python=%r js=%r"
                      % (i, case["seed"], len(case["flaps"]), field,
                         mine[field], theirs[field]))
                break

    scored = sum(1 for c in cases if python_side(c)["score"] > 0)
    best = max(python_side(c)["score"] for c in cases)
    print("%d cases, %d reached the board, best run %d patches"
          % (len(cases), scored, best))
    if failures:
        print("%d cases differ. The engines are not the same game." % failures)
        return 1
    print("Python and JavaScript agree on every field of every case.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
