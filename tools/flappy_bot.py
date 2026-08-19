"""Traces to test the anti-cheat with: one computed, one played.

Both drive the real simulation from app/flappy.py, so a trace from here is a
trace the server will agree with. That is the point: the forged trace has to be
the genuine article, because a forgery that failed replay would prove nothing.

The only difference between the two is timing and aim. The bot decides on a
fixed offset and steers at the exact centre of the gap. A hand waits a
different moment each time and aims roughly. That difference is the whole
signal the plausibility checks are reading, so keeping the two generators
otherwise identical is deliberate.

Importing this imports app/flappy.py, which opens a database. Set ZS_BUFFER_DB
to a scratch path before importing if the caller has its own database to
protect.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app"))

import flappy  # noqa: E402


def _target_gap(sim):
    """The gap the duck is flying at right now.

    Scanning from one obstacle back matters: the score advances when the duck's
    centre passes the tile's centre, which is well before the tile is behind
    it, so aiming at the next gap that early steers into the tile the duck is
    still inside.
    """
    i = max(0, sim.next_score_index - 1)
    while i < sim.next_score_index + 3:
        if sim.obstacle_screen_x(i) + flappy.TILE_W >= flappy.DUCK_X:
            return sim.gap_center(i)
        i += 1
    return 240.0


def bot_trace(seed, limit=60, cooldown=10, max_ticks=60000):
    """A trace from a look-ahead solver, which is what a forged run looks like.

    Worth having for its own sake as well: these are long, high scoring runs
    that exercise far more of the simulation than random flapping reaches.
    """
    sim = flappy.Sim(seed)
    flaps = []
    last = -999
    while sim.state != flappy.DEAD and sim.score < limit and sim.tick < max_ticks:
        if sim.state == flappy.READY:
            # Nothing falls on the ready screen, so a solver that only reacts
            # to losing height would sit there forever. Something has to go
            # first.
            want = True
        else:
            want = sim.duck_y + flappy.DUCK_H / 2 > _target_gap(sim) + 22
        if want and sim.tick - last >= cooldown:
            flaps.append(sim.tick)
            sim.queue_flap(sim.tick)
            last = sim.tick
        sim.step()
    return flaps


def played_trace(seed, rng, limit=45, aim=9, gap=(8, 22), max_ticks=60000):
    """A trace shaped like a person playing: ragged timing, imperfect aim."""
    sim = flappy.Sim(seed)
    flaps = []
    ready_at = -999
    off = rng.uniform(-aim, aim)
    while sim.state != flappy.DEAD and sim.score < limit and sim.tick < max_ticks:
        if sim.state == flappy.READY:
            want = True
        else:
            want = sim.duck_y + flappy.DUCK_H / 2 > _target_gap(sim) + 22 + off
        if want and sim.tick >= ready_at:
            flaps.append(sim.tick)
            sim.queue_flap(sim.tick)
            # A hand needs a moment before the next tap, and never the same one.
            ready_at = sim.tick + rng.randint(gap[0], gap[1])
            off = rng.uniform(-aim, aim)
        sim.step()
    return flaps


def long_played_trace(seed, rng, want=12, tries=40, **kw):
    """A played run that got somewhere.

    Honest runs die early all the time. The ones that reach a leaderboard, and
    so the ones these checks have to get right, are the ones that did not.
    """
    best, best_score = [], -1
    for _ in range(tries):
        trace = played_trace(seed, rng, **kw)
        score = flappy.replay(seed, trace).score
        if score > best_score:
            best, best_score = trace, score
        if score >= want:
            return trace
    return best


def trace_for_score(seed, target, rng=None, tries=60):
    """A played trace worth roughly `target` patches on this seed.

    Used where a test needs a specific score rather than a specific shape.
    Exact is not always reachable, so this returns the closest it found along
    with what that trace actually scores.
    """
    import random
    rng = rng or random.Random(seed * 7919 + target)
    best, best_score = [], -1
    for _ in range(tries):
        trace = played_trace(seed, rng, limit=target)
        score = flappy.replay(seed, trace).score
        if score == target:
            return trace, score
        if abs(score - target) < abs(best_score - target):
            best, best_score = trace, score
    return best, best_score
