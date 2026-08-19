/**
 * Dump the result of replaying a set of traces, for the parity check.
 *
 * Reads {"cases":[{"seed":n,"flaps":[...]}]} on stdin and writes one result per
 * case on stdout. The tick budget matches replay() in flappy.py so the two
 * engines are asked exactly the same question.
 */
import { createSim, queueFlap, step, durationMs, STATE } from '../app/flappy/sim.mjs';

const FALL_TICKS = 400;
const ABSOLUTE_MAX_TICKS = 120 * 60 * 10;

function run(seed, flaps) {
  const last = flaps.length ? flaps[flaps.length - 1] : 0;
  const maxTicks = Math.min(last, ABSOLUTE_MAX_TICKS) + FALL_TICKS;

  const sim = createSim(seed);
  let next = 0;
  while (sim.state !== STATE.DEAD && sim.tick < maxTicks) {
    while (next < flaps.length && flaps[next] <= sim.tick) {
      queueFlap(sim, flaps[next]);
      next += 1;
    }
    step(sim);
  }
  return {
    score: sim.score,
    duration_ms: durationMs(sim),
    tick: sim.tick,
    death_tick: sim.deathTick,
    play_start_tick: sim.playStartTick,
    state: sim.state,
    cause: sim.cause,
    duck_y: sim.duckY,
    duck_vy: sim.duckVy,
    scroll_x: sim.scrollX,
    flaps: sim.flaps,
    gaps: sim.gaps,
  };
}

let raw = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', (chunk) => { raw += chunk; });
process.stdin.on('end', () => {
  const input = JSON.parse(raw);
  const out = input.cases.map((c) => run(c.seed, c.flaps));
  process.stdout.write(JSON.stringify(out));
});
