"""Prove the Python simulation in patchman.py matches the JavaScript one.

The server decides scores by replaying a run, so the two engines agreeing is
not a nice property, it is the thing the whole anti-cheat rests on. A drift of
one tick in one branch would either reject honest runs or accept forged ones.

Both sides replay the same seeds and traces, and every field of the result is
compared with no tolerance — including the whole remaining maze, tile by tile,
because a patch eaten on a different tick is exactly the kind of difference
that stays invisible in a score until the run that matters.

The cases are chosen to hit the branches a port usually gets wrong: the tunnel
wrap, where JavaScript's remainder operator goes negative and Python's does
not; the frightened window, which is the only consumer of the generator; the
journey home, which is the only path that follows a distance field; a level
clear; and every degenerate trace shape that has no business working but has to
behave the same in both.

    python tools/check_patchman_parity.py [cases]
"""

import json
import os
import random
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "app"))
sys.path.insert(0, HERE)

# The module opens a database on import, so send it somewhere disposable.
os.environ.setdefault("ZS_BUFFER_DB",
                      os.path.join(HERE, "_patchman_parity_scratch.db"))

import patchman  # noqa: E402

FIELDS = (
    "score", "duration_ms", "tick", "end_tick", "play_start_tick", "state",
    "level", "lives", "patches_left", "total_patches",
    "pac_x", "pac_y", "pac_dir", "pac_want",
    "phase_index", "phase_kind", "fright_ticks", "fright_chain",
    "vulns_patched", "elroy_stage", "freeze_ticks", "bonus_state",
    "bonuses_shown", "house_idle", "vulns", "turns", "tiles",
)

# Long enough that a case can clear a level and lose three lives, short enough
# that a few hundred cases still run in seconds.
CASE_TICKS = 120 * 75


def encode(ticks_and_dirs):
    return [t * 4 + d for t, d in ticks_and_dirs]


def human_trace(rng, idle_ticks, count):
    """Ragged turns after a pause on the ready screen.

    The pause is the point. Trace ticks are absolute, so a player who reads the
    instructions before their first press produces a trace whose numbers sit
    well beyond where a naive port would expect them.
    """
    out = []
    t = idle_ticks
    last = -1
    for _ in range(count):
        t += rng.randint(4, 140)
        d = rng.randrange(4)
        if d == last:                # the client drops repeats, so must this
            d = (d + 1) % 4
        last = d
        out.append((t, d))
    return encode(out)


def solver_trace(rng, count):
    """Turns on an exact cadence, which is what a machine produces."""
    out = []
    t = rng.randint(0, 30)
    last = -1
    for _ in range(count):
        t += 16                      # one tile at the base speed
        d = rng.randrange(4)
        if d == last:
            d = (d + 1) % 4
        last = d
        out.append((t, d))
    return encode(out)


def tunnel_trace():
    """Straight into the wrap, then back, then round again.

    The tunnel is the one place a position crosses zero, and it is the branch
    where JavaScript's remainder operator and Python's disagree unless the port
    normalises first. It is worth a case of its own.
    """
    return encode([(0, 1), (30, 0), (300, 1), (900, 3), (1500, 1), (2400, 2)])


def bot_traces(seeds, max_ticks):
    """Traces from the greedy bot in tools/patchman_bot.mjs.

    Generated in JavaScript because the bot needs a simulation to play against
    and the client's is the authority. What comes back is only input, so it
    carries no opinion about the outcome into the comparison.
    """
    proc = subprocess.run(
        ["node", os.path.join(HERE, "patchman_bot.mjs"),
         json.dumps({"seeds": seeds, "maxTicks": max_ticks})],
        capture_output=True, text=True, cwd=ROOT,
    )
    if proc.returncode != 0:
        raise SystemExit("bot failed:\n" + proc.stderr)
    return json.loads(proc.stdout)


def build_cases(count):
    rng = random.Random(20260226)
    cases = []

    # Degenerate shapes first, because they are where a port usually differs.
    cases.append({"seed": 1, "turns": []})                    # never starts
    cases.append({"seed": 0, "turns": [0]})                   # one press, tick 0
    cases.append({"seed": 0xFFFFFFFF, "turns": encode([(0, 1), (0, 0), (0, 3)])})
    cases.append({"seed": 12345, "turns": encode([(5, 2), (5, 2), (5, 2)])})
    cases.append({"seed": 999, "turns": encode([(9000, 1)])})  # a very long think
    cases.append({"seed": 7, "turns": encode([(0, 0)])})       # straight into a wall
    cases.append({"seed": 8, "turns": tunnel_trace()})
    cases.append({"seed": 4242, "turns": encode([(t, t % 4) for t in range(0, 800, 7)])})

    # Runs that actually play. Without these the frightened window, the journey
    # home and the level clear are never reached, and those are the branches
    # most worth proving.
    played = max(6, count // 3)
    seeds = [rng.getrandbits(32) for _ in range(played)]
    for seed, turns in zip(seeds, bot_traces(seeds, CASE_TICKS)):
        cases.append({"seed": seed, "turns": turns})

    for _ in range(count):
        seed = rng.getrandbits(32)
        pick = rng.random()
        if pick < 0.4:
            turns = human_trace(rng, rng.choice([0, 3, 240, 1500]), rng.randint(0, 90))
        elif pick < 0.7:
            turns = solver_trace(rng, rng.randint(4, 120))
        else:
            n = rng.randint(0, 60)
            ticks = sorted(rng.randint(0, CASE_TICKS) for _ in range(n))
            turns = encode([(t, rng.randrange(4)) for t in ticks])
        cases.append({"seed": seed, "turns": turns})

    for case in cases:
        case["maxTicks"] = CASE_TICKS
    return cases


def python_side(case):
    sim = patchman.replay(case["seed"], case["turns"], max_ticks=case["maxTicks"])
    return {
        "score": sim.score,
        "duration_ms": sim.duration_ms(),
        "tick": sim.tick,
        "end_tick": sim.end_tick,
        "play_start_tick": sim.play_start_tick,
        "state": sim.state,
        "level": sim.level,
        "lives": sim.lives,
        "patches_left": sim.patches_left,
        "total_patches": sim.total_patches,
        "pac_x": sim.pac_x,
        "pac_y": sim.pac_y,
        "pac_dir": sim.pac_dir,
        "pac_want": sim.pac_want,
        "phase_index": sim.phase_index,
        "phase_kind": sim.phase_kind,
        "fright_ticks": sim.fright_ticks,
        "fright_chain": sim.fright_chain,
        "vulns_patched": sim.vulns_patched,
        "elroy_stage": sim.elroy_stage,
        "freeze_ticks": sim.freeze_ticks,
        "bonus_state": sim.bonus_state,
        "bonuses_shown": sim.bonuses_shown,
        "house_idle": sim.house_idle,
        "vulns": [[g.x, g.y, g.dir, g.state, 1 if g.fright else 0]
                  for g in sim.vulns],
        "turns": sim.turns,
        "tiles": "".join(sim.tiles),
    }


def main():
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 120
    cases = build_cases(count)

    dumper = os.path.join(HERE, "patchman_parity_dump.mjs")
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
    results = []
    for i, case in enumerate(cases):
        mine = python_side(case)
        results.append(mine)
        theirs = js[i]
        for field in FIELDS:
            if mine[field] != theirs[field]:
                a, b = repr(mine[field]), repr(theirs[field])
                print("case %d seed=%d turns=%d: %s\n  python=%s\n  js    =%s"
                      % (i, case["seed"], len(case["turns"]), field,
                         a[:200], b[:200]))
                failures += 1
                break

    scored = sum(1 for r in results if r["score"] > 0)
    best = max(r["score"] for r in results)
    deepest = max(r["level"] for r in results)
    ate = sum(1 for r in results if r["vulns_patched"] > 0)
    caught = sum(r["vulns_patched"] for r in results)
    print("%d cases, %d scored, best %d points, deepest level %d, "
          "%d runs patched a vulnerability (%d in total)"
          % (len(cases), scored, best, deepest, ate, caught))
    if failures:
        print("%d cases differ. The engines are not the same game." % failures)
        return 1
    print("Python and JavaScript agree on every field of every case.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
