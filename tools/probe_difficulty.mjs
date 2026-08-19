/**
 * Difficulty probe for Flappy Duck.
 *
 * Difficulty is deliberately constant: the gap never shrinks and the scroll
 * never speeds up. That only works if the gaps a seed produces are actually
 * clearable, so this flies a simple bot through a batch of seeds and reports
 * how far it gets. It is a tuning aid, not a test, and the app never imports it.
 *
 *   node tools/probe_difficulty.mjs            batch report over 200 seeds
 *   node tools/probe_difficulty.mjs 1234 20    one seed, up to 20 patches
 */
import { CONFIG } from '../app/flappy/config.mjs';
import { createSim, queueFlap, step, obstacleScreenX, obstacleAt, STATE } from '../app/flappy/sim.mjs';

/** Fly one seed and return the flap trace and the score it reached. */
export function planRun(seed, targetScore = 15) {
  const sim = createSim(seed);
  const flaps = [];
  let lastFlap = -99;
  const flap = () => { queueFlap(sim, sim.tick); flaps.push(sim.tick); lastFlap = sim.tick; };
  flap();
  while (sim.state !== STATE.DEAD && sim.score < targetScore && sim.tick < 40000) {
    // obstacleScreenX is the tile's left edge, so an obstacle is only behind the
    // duck once its whole width has gone past. Aiming at the next gap any
    // earlier steers out of the gap the duck is still flying through.
    let index = 0;
    while (obstacleScreenX(sim, index) + CONFIG.tileW < CONFIG.duckX) index += 1;
    const center = obstacleAt(sim, index).center;
    // A flap carries the duck about 50px up, so aim from below the gap centre
    // and the bob that follows straddles it.
    if (sim.duckY + CONFIG.duckH / 2 > center + 22 && sim.tick - lastFlap >= 10) flap();
    step(sim);
  }
  return { seed, flaps, score: sim.score, ticks: sim.tick, cause: sim.cause };
}

if (process.argv[2]) {
  const r = planRun(Number(process.argv[2]), Number(process.argv[3] || 15));
  console.log(JSON.stringify(r));
} else {
  const seeds = 200;
  const target = 15;
  let sum = 0, worst = Infinity, cleared = 0;
  for (let s = 1; s <= seeds; s += 1) {
    const r = planRun(s, target);
    sum += r.score;
    worst = Math.min(worst, r.score);
    if (r.score >= 10) cleared += 1;
  }
  console.log('seeds        ', seeds);
  console.log('mean patches ', (sum / seeds).toFixed(1), '(bot stops at', target + ')');
  console.log('reached 10   ', cleared + '/' + seeds);
  console.log('worst seed   ', worst);
  if (cleared / seeds < 0.9) {
    console.log('\nUnder 90 percent. Look at gapCenterMaxDelta in config.mjs.');
    process.exitCode = 1;
  }
}
