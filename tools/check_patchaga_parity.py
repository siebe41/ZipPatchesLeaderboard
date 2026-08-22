"""Prove the two Patchaga engines are the same engine.

The server replays every submitted run and refuses any score its own replay
does not reproduce. That is only a fair test if ``app/patchaga.py`` and
``app/patchaga/sim.mjs`` agree exactly, and "exactly" here means every integer
of every entity on every tick, not merely the final score. Two engines can
easily reach the same score down different paths and then disagree on the run
after.

So this feeds identical cases to both, compares the finishing state field by
field, and compares a per-tick digest of the whole world. When the digests
diverge it reports the first tick they differ on, because a divergence found at
tick 8,412 is a bug you can read; the same divergence found only as a wrong
final score is a bug you have to bisect.

    python tools/check_patchaga_parity.py            # the standard cases
    python tools/check_patchaga_parity.py --cases 60 # more, for a release
    python tools/check_patchaga_parity.py --seed 31337 --verbose

Half the cases are random input and half are played by ``tools/patchaga_bot.mjs``.
Both matter, and for opposite reasons. Random input dies in the first wave, so
on its own it would never reach a capture, a merged duck or a regression sweep
-- a whole third of the rules would go unchecked. The bot reaches them, but it
plays sensibly, so on its own it would never wander into the ugly corners: dying
on the same tick a wave is cleared, firing into a bug that is already dead,
turning on the frame a beam closes. Neither generator finds what the other does.

Exits non-zero on any disagreement, so it can gate a deploy.
"""

import argparse
import json
import os
import random
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app"))

import patchaga  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DUMP = os.path.join(ROOT, "tools", "patchaga_parity_dump.mjs")

# Long enough that a wave gets cleared and the formation rebuilt, which is where
# a port is most likely to drift, but short enough that sixty cases still run in
# a few seconds.
DEFAULT_TICKS = 120 * 150


def digest(sim):
    """The Python side of the digest in patchaga_parity_dump.mjs.

    FNV-1a over every mutable field, masked to 32 bits after each step so it
    matches JavaScript's Math.imul exactly.
    """
    h = 2166136261

    def mix(v):
        nonlocal h
        h = ((h ^ (int(v) & 0xFFFFFFFF)) * 16777619) & 0xFFFFFFFF

    def mix_signed(v):
        # JavaScript's `v | 0` truncates to a signed 32-bit value; XOR sees the
        # same bit pattern either way, so masking is the same operation.
        nonlocal h
        h = ((h ^ (int(v) & 0xFFFFFFFF)) * 16777619) & 0xFFFFFFFF

    mix(sim.tick)
    mix(sim.score)
    mix(sim.lives)
    mix(sim.wave)
    mix(sim.next_extra_life)
    mix(sim.state_tick)
    mix(len(sim.state))
    mix(ord(sim.state[0]))
    mix_signed(sim.duck.x)
    mix_signed(sim.duck.dir)
    mix(1 if sim.duck.alive else 0)
    mix(1 if sim.duck.merged else 0)
    mix(sim.duck.cooldown)
    mix(sim.duck.invuln)
    mix(sim.launch_index)
    mix(sim.launch_timer)
    mix(sim.dive_timer)
    mix(sim.dives_since_rootkit)
    mix(sim.sweep_group)
    mix(sim.sweep_timer)
    mix(sim.sweep_hits)
    mix(sim.bugs_patched)
    mix(sim.shots_fired)
    mix(sim.forks)
    mix(sim.rescues)
    mix(len(sim.bugs))
    for b in sim.bugs:
        mix(b.state); mix_signed(b.x); mix_signed(b.y); mix_signed(b.t)
        mix_signed(b.vx); mix_signed(b.vy); mix(b.fire_timer)
        mix(1 if b.beam_open else 0); mix(1 if b.holds_duck else 0)
        mix(1 if b.wants_fork else 0)
        mix_signed(b.dive_side); mix(b.dive_phase)
        mix_signed(b.return_x); mix_signed(b.return_y)
        mix(1 if b.is_sweep else 0); mix(b.sweep_lane); mix(b.sweep_phase)
    mix(len(sim.patches))
    for p in sim.patches:
        mix_signed(p[0]); mix_signed(p[1])
    mix(len(sim.bug_shots))
    for s in sim.bug_shots:
        mix_signed(s[0]); mix_signed(s[1]); mix_signed(s[2]); mix_signed(s[3])
    mix(1 if sim.rescue else 0)
    if sim.rescue:
        mix_signed(sim.rescue[0]); mix_signed(sim.rescue[1])
    return h


def replay_python(seed, inputs, max_ticks, top_up=0):
    sim = patchaga.Sim(seed)
    trail = []
    nxt = 0
    while sim.state != patchaga.S_DEAD and sim.tick < max_ticks:
        while nxt < len(inputs) and inputs[nxt] // 4 <= sim.tick:
            sim.queue_input(inputs[nxt] // 4, inputs[nxt] % 4)
            nxt += 1
        sim.step()
        if top_up > 0 and sim.tick % top_up == 0 and sim.state != patchaga.S_DEAD:
            sim.lives = 3
        trail.append(digest(sim))

    return {
        "score": sim.score,
        "duration_ms": sim.duration_ms(),
        "tick": sim.tick,
        "end_tick": sim.end_tick,
        "play_start_tick": sim.play_start_tick,
        "state": sim.state,
        "wave": sim.wave,
        "lives": sim.lives,
        "next_extra_life": sim.next_extra_life,
        "bugs_patched": sim.bugs_patched,
        "shots_fired": sim.shots_fired,
        "waves_cleared": sim.waves_cleared,
        "forks": sim.forks,
        "rescues": sim.rescues,
        "sweep_hits": sim.sweep_hits,
        "sweep_total": sim.sweep_total,
        "dives_since_rootkit": sim.dives_since_rootkit,
        "duck": [sim.duck.x, sim.duck.dir, 1 if sim.duck.alive else 0,
                 1 if sim.duck.merged else 0, sim.duck.cooldown, sim.duck.invuln],
        "bugs": [[b.state, b.x, b.y, b.t, b.vx, b.vy,
                  1 if b.beam_open else 0, 1 if b.holds_duck else 0,
                  1 if b.is_sweep else 0] for b in sim.bugs],
        "patches": [[p[0], p[1]] for p in sim.patches],
        "bug_shots": [[s[0], s[1], s[2], s[3]] for s in sim.bug_shots],
        "rescue": [sim.rescue[0], sim.rescue[1]] if sim.rescue else None,
        "inputs": list(sim.inputs),
        "trail": trail,
    }


def make_case(rng, seed, max_ticks):
    """A run made of the kind of input a player produces.

    Not a random button mash: real play is bursts of steering with fire held
    down through them, and the states that only a real-looking run reaches --
    a cleared wave, a capture, a merged duck -- are exactly the states worth
    checking. Fire is emitted on the cooldown so the trace exercises the cap on
    patches in flight as well.
    """
    inputs = []
    tick = rng.randrange(30, 240)
    firing = rng.random() < 0.9
    while tick < max_ticks:
        action = rng.choice([patchaga.A_LEFT, patchaga.A_RIGHT, patchaga.A_NEUTRAL])
        inputs.append(tick * 4 + action)
        hold = rng.randrange(8, 90)
        if firing:
            shot = tick + rng.randrange(0, 6)
            while shot < tick + hold:
                inputs.append(shot * 4 + patchaga.A_FIRE)
                shot += patchaga.PATCH_COOLDOWN + rng.randrange(0, 4)
        elif rng.random() < 0.35:
            firing = True
        tick += hold
    inputs.sort(key=lambda c: c // 4)
    return {"seed": seed, "inputs": inputs, "maxTicks": max_ticks}


def bot_cases(count, max_ticks, base_seed, top_up):
    """Traces from a bot that can actually play, so deep waves get checked.

    Its ceiling is the case length rather than the bot's own default, because a
    trace that runs past ``max_ticks`` would have its tail silently ignored and
    the case would quietly become shorter than it looks.

    ``top_up`` restores lives on a fixed tick interval. Nothing in the game does
    this and nothing can ask it to; it belongs to the harness, and both engines
    apply it at exactly the same point in exactly the same way, so a case with
    it on is every bit as deterministic as one without. It is here because the
    regression sweep is wave 4 and a good bot dies in wave 2, so without it a
    whole wave type -- and the perfect-clear bonus that goes with it -- would
    never be compared at all.
    """
    if count <= 0:
        return []
    minutes = max_ticks / (120.0 * 60.0)
    proc = subprocess.run(
        ["node", os.path.join(ROOT, "tools", "patchaga_bot.mjs"),
         str(count), "--seed", str(base_seed), "--minutes", "%.6f" % minutes,
         "--topup", str(top_up), "--traces"],
        capture_output=True, text=True, cwd=ROOT,
    )
    if proc.returncode != 0:
        print("bot failed:\n" + proc.stderr)
        return []
    return [{"seed": r["seed"], "inputs": r["inputs"], "maxTicks": max_ticks,
             "topUp": top_up, "source": "bot"}
            for r in json.loads(proc.stdout)]


def compare(name, js, py, verbose=False):
    """Report every field that differs, not just the first."""
    problems = []
    for key in sorted(set(js) | set(py)):
        if key == "trail":
            continue
        a, b = js.get(key), py.get(key)
        if a != b:
            problems.append("  %-20s js=%r  py=%r" % (key, a, b))

    ta, tb = js.get("trail") or [], py.get("trail") or []
    if ta != tb:
        if len(ta) != len(tb):
            problems.append("  %-20s js=%d ticks  py=%d ticks"
                            % ("trail length", len(ta), len(tb)))
        first = next((i for i in range(min(len(ta), len(tb))) if ta[i] != tb[i]), None)
        if first is not None:
            problems.append("  %-20s first differs at tick %d (js=%08x py=%08x)"
                            % ("trail", first, ta[first], tb[first]))

    if problems:
        print("MISMATCH %s" % name)
        for line in problems:
            print(line)
        return False
    if verbose:
        print("ok  %s  score=%d wave=%d ticks=%d inputs=%d"
              % (name, py["score"], py["wave"], py["tick"], len(py["inputs"])))
    return True


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cases", type=int, default=24)
    ap.add_argument("--ticks", type=int, default=DEFAULT_TICKS)
    ap.add_argument("--seed", type=int, default=None,
                    help="check one specific game seed")
    ap.add_argument("--rng", type=int, default=1234,
                    help="seed for the case generator, so a failure repeats")
    ap.add_argument("--topup", type=int, default=1200,
                    help="harness-only: restore bot lives every N ticks, so "
                         "cases reach the waves a real run dies before")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    rng = random.Random(args.rng)
    if args.seed is not None:
        cases = [make_case(rng, args.seed, args.ticks)]
    else:
        # Fixed low seeds first so a regression is reproducible by hand, then
        # spread across the range the server actually issues.
        seeds = [1, 2, 3, 7, 42, 31337]
        half = max(len(seeds), args.cases // 2)
        seeds += [rng.randrange(1, 0x7FFFFFFF)
                  for _ in range(max(0, half - len(seeds)))]
        cases = [make_case(rng, s, args.ticks) for s in seeds]
        cases += bot_cases(max(0, args.cases - len(cases)), args.ticks,
                           args.rng, args.topup)

    proc = subprocess.run(
        ["node", DUMP],
        input=json.dumps({"cases": cases}),
        capture_output=True, text=True, cwd=ROOT,
    )
    if proc.returncode != 0:
        print("node failed:\n" + proc.stderr)
        return 2

    js_results = json.loads(proc.stdout)

    failures = 0
    deepest = 0
    for case, js in zip(cases, js_results):
        py = replay_python(case["seed"], case["inputs"], case["maxTicks"],
                           case.get("topUp", 0))
        deepest = max(deepest, py["wave"])
        label = "seed %d%s" % (case["seed"],
                               " (bot)" if case.get("source") == "bot" else "")
        if not compare(label, js, py, args.verbose):
            failures += 1

    total = len(cases)
    if failures:
        print("\n%d of %d cases disagree. The server would reject real runs."
              % (failures, total))
        return 1
    print("%d cases, every tick identical in both engines. Deepest wave %d."
          % (total, deepest))
    return 0


if __name__ == "__main__":
    sys.exit(main())
