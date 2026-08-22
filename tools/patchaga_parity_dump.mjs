/**
 * Replay Patchaga runs in the browser's engine and print the results as JSON.
 *
 * Driven by tools/check_patchaga_parity.py, which feeds cases in on stdin and
 * compares every field against its own Python replay. Kept deliberately dumb:
 * it must not normalise, round or tidy anything, because a difference this hid
 * would be a difference the server and the client would go on to have.
 *
 * As well as the final state it emits a running digest of the whole world,
 * recomputed every tick. Comparing final scores tells you that two engines
 * disagree; comparing digests tells you the tick they started to, which is the
 * difference between a day of bisecting and a minute of reading.
 */
import { readFileSync } from 'node:fs';
import { createSim, queueInput, step, durationMs, STATE }
  from '../app/patchaga/sim.mjs';

const input = JSON.parse(readFileSync(0, 'utf8'));

/**
 * A 32-bit rolling hash of everything that can differ.
 *
 * Deliberately arithmetic rather than a real hash: it has to be reproducible in
 * Python without a shared library, and the only property needed is that any
 * changed field changes the result. Imul keeps it in 32 bits the same way the
 * Python side's mask does.
 */
function digest(sim) {
  let h = 2166136261;
  const mix = (v) => {
    h = Math.imul(h ^ (v | 0), 16777619) >>> 0;
  };

  mix(sim.tick);
  mix(sim.score);
  mix(sim.lives);
  mix(sim.wave);
  mix(sim.nextExtraLife);
  mix(sim.stateTick);
  mix(sim.state.length);          // states are strings; length plus order below
  mix(sim.state.charCodeAt(0));
  mix(sim.duck.x);
  mix(sim.duck.dir);
  mix(sim.duck.alive ? 1 : 0);
  mix(sim.duck.merged ? 1 : 0);
  mix(sim.duck.cooldown);
  mix(sim.duck.invuln);
  mix(sim.launchIndex);
  mix(sim.launchTimer);
  mix(sim.diveTimer);
  mix(sim.divesSinceRootkit);
  mix(sim.sweepGroup);
  mix(sim.sweepTimer);
  mix(sim.sweepHits);
  mix(sim.bugsPatched);
  mix(sim.shotsFired);
  mix(sim.forks);
  mix(sim.rescues);
  mix(sim.bugs.length);
  for (const b of sim.bugs) {
    mix(b.state); mix(b.x); mix(b.y); mix(b.t);
    mix(b.vx); mix(b.vy); mix(b.fireTimer);
    mix(b.beamOpen ? 1 : 0); mix(b.holdsDuck ? 1 : 0); mix(b.wantsFork ? 1 : 0);
    mix(b.diveSide); mix(b.divePhase); mix(b.returnX); mix(b.returnY);
    mix(b.isSweep ? 1 : 0); mix(b.sweepLane); mix(b.sweepPhase);
  }
  mix(sim.patches.length);
  for (const p of sim.patches) { mix(p.x); mix(p.y); }
  mix(sim.bugShots.length);
  for (const s of sim.bugShots) { mix(s.x); mix(s.y); mix(s.vx); mix(s.vy); }
  mix(sim.rescue ? 1 : 0);
  if (sim.rescue) { mix(sim.rescue.x); mix(sim.rescue.y); }
  return h >>> 0;
}

function replayCase(seed, inputs, maxTicks, topUp) {
  const sim = createSim(seed);
  const trail = [];
  let next = 0;
  while (sim.state !== STATE.DEAD && sim.tick < maxTicks) {
    while (next < inputs.length && Math.floor(inputs[next] / 4) <= sim.tick) {
      queueInput(sim, Math.floor(inputs[next] / 4), inputs[next] % 4);
      next += 1;
    }
    step(sim);
    // The sim appends presentation events and never trims them; the consumer
    // owns that. Nothing here reads them, and 86,400 ticks of unread events
    // would be a large array kept alive for no reason.
    sim.events.length = 0;
    // Applied after the step and keyed on the tick, so it is a property of the
    // case rather than of the engine. See the note in check_patchaga_parity.py.
    if (topUp > 0 && sim.tick % topUp === 0 && sim.state !== STATE.DEAD) {
      sim.lives = 3;
    }
    trail.push(digest(sim));
  }
  return { sim, trail };
}

const out = input.cases.map((c) => {
  const { sim, trail } = replayCase(c.seed, c.inputs, c.maxTicks, c.topUp || 0);
  return {
    score: sim.score,
    duration_ms: durationMs(sim),
    tick: sim.tick,
    end_tick: sim.endTick,
    play_start_tick: sim.playStartTick,
    state: sim.state,
    wave: sim.wave,
    lives: sim.lives,
    next_extra_life: sim.nextExtraLife,
    bugs_patched: sim.bugsPatched,
    shots_fired: sim.shotsFired,
    waves_cleared: sim.wavesCleared,
    forks: sim.forks,
    rescues: sim.rescues,
    sweep_hits: sim.sweepHits,
    sweep_total: sim.sweepTotal,
    dives_since_rootkit: sim.divesSinceRootkit,
    duck: [sim.duck.x, sim.duck.dir, sim.duck.alive ? 1 : 0,
           sim.duck.merged ? 1 : 0, sim.duck.cooldown, sim.duck.invuln],
    bugs: sim.bugs.map((b) => [b.state, b.x, b.y, b.t, b.vx, b.vy,
                               b.beamOpen ? 1 : 0, b.holdsDuck ? 1 : 0,
                               b.isSweep ? 1 : 0]),
    patches: sim.patches.map((p) => [p.x, p.y]),
    bug_shots: sim.bugShots.map((s) => [s.x, s.y, s.vx, s.vy]),
    rescue: sim.rescue ? [sim.rescue.x, sim.rescue.y] : null,
    inputs: sim.inputs,
    trail,
  };
});

process.stdout.write(JSON.stringify(out));
