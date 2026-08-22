/**
 * A bot that plays Patchaga, for measuring the game rather than beating it.
 *
 *   node tools/patchaga_bot.mjs [runs] [--seed N] [--minutes M] [--human] [--verbose]
 *
 * Two things this is for. The first is difficulty: a game nobody can finish a
 * wave of is not hard, it is broken, and the only way to know which one you
 * have built is to have something play it a few hundred times. The second is
 * the anti-cheat work, because by default this produces exactly the kind of
 * trace a solver would -- perfectly timed, perfectly spaced -- and the server
 * is supposed to notice. A default run that the board accepts without comment
 * is a finding.
 *
 * It deliberately plays like a competent person rather than optimally: it only
 * presses fire when a press would do something, it changes direction only when
 * the direction it wants changes, and it does not read the random state. What
 * it does not simulate is a hand, which is the point.
 *
 * ``--human`` adds the hand. It gives the bot a reaction delay that varies
 * from correction to correction and a dead zone around its target, which is
 * what stops it hunting left and right every other tick. That is the difference
 * the server is measuring, so it is the mode a harness needs when it wants a
 * run the board should accept. It is a worse player, which is the point: the
 * two modes together bracket the threshold rather than testing one side of it.
 */
import {
  createSim, step, queueInput, durationMs, canFire,
  STATE, ACTION, BUG, KIND,
} from '../app/patchaga/sim.mjs';
import { CONFIG } from '../app/patchaga/config.mjs';
import { makeRng, rngInt } from '../app/patchaga/rng.mjs';

const U = CONFIG.unit;
const PX = (v) => v * U;

// How long the hand takes to act on something it has just seen, and how much
// that varies. Both are in ticks, so 10 to 34 is roughly 80 to 280ms -- slow
// for a reaction to a known stimulus, about right for deciding what to do.
const REACT_MIN = 10;
const REACT_SPREAD = 24;

// How far off target the duck has to be before a person bothers correcting.
// Without this the bot oscillates around its target every other tick, which is
// the single loudest machine signature in the whole trace.
const HUMAN_DEADZONE = PX(9);

/** Anything that can end the run, and how close it is to doing so. */
function nearestThreat(sim) {
  const duck = sim.duck;
  let worst = null;
  let worstY = -1;

  for (const s of sim.bugShots) {
    if (s.y < PX(300)) continue;                       // still far above
    if (Math.abs(s.x - duck.x) > PX(26)) continue;     // not in this lane
    if (s.y > worstY) { worst = s; worstY = s.y; }
  }
  for (const b of sim.bugs) {
    if (b.state !== BUG.DIVING && b.state !== BUG.SWEEPING) continue;
    if (b.y < PX(330)) continue;
    if (Math.abs(b.x - duck.x) > PX(30)) continue;
    if (b.y > worstY) { worst = b; worstY = b.y; }
  }
  return worst;
}

/** The bug worth shooting: whatever is lowest, because it is closest to being a problem. */
function bestTarget(sim) {
  let best = null;
  for (const b of sim.bugs) {
    if (b.state === BUG.DEAD || b.state === BUG.WAITING) continue;
    if (b.state === BUG.SWEEPING && b.t < 0) continue;
    if (!best || b.y > best.y) best = b;
  }
  return best;
}

/**
 * A run's worth of input, played by the bot.
 *
 * ``topUp`` restores lives every N ticks. It is not a game mode and nothing in
 * the app can turn it on; it exists so a harness can drive a run past the wave
 * a good player dies on. The sweep wave is wave 4, and a bot that dies in wave
 * 2 never reaches the rules that only run there. Because the top-up depends on
 * the tick and nothing else, a run with it on is still perfectly deterministic
 * and still replays identically in both engines.
 *
 * ``human`` slows the steering down to something a hand could have produced.
 * Note where its randomness comes from: a generator of its own, seeded from the
 * run's seed but never the simulation's. Drawing from ``sim.rng`` would consume
 * the stream the server replays and the run would no longer be reproducible
 * from its seed and trace, which is the one property everything else rests on.
 */
export function playRun(seed, maxTicks, topUp = 0, human = false) {
  const sim = createSim(seed);
  const hand = makeRng((seed ^ 0x9e3779b9) >>> 0);
  let held = 0;
  let actAt = 0;      // the tick the hand is next willing to move on
  let pendingWant = 0;

  for (let t = 0; t < maxTicks && sim.state !== STATE.DEAD; t++) {
    let want = held;

    if (sim.duck.alive) {
      const threat = nearestThreat(sim);
      if (threat) {
        // Break the lane. Running for the middle rather than the nearest wall
        // stops it cornering itself, which is the mistake a new player makes.
        const room = threat.x > sim.duck.x
          ? sim.duck.x - PX(CONFIG.duckMargin)
          : PX(CONFIG.width - CONFIG.duckMargin) - sim.duck.x;
        want = room > PX(40) ? (threat.x > sim.duck.x ? -1 : 1)
          : (threat.x > sim.duck.x ? 1 : -1);
      } else {
        const target = bestTarget(sim);
        if (!target) {
          want = 0;
        } else {
          const dx = target.x - sim.duck.x;
          const dead = human ? HUMAN_DEADZONE : PX(3);
          want = Math.abs(dx) <= dead ? 0 : (dx > 0 ? 1 : -1);
        }
      }
    } else {
      want = 0;
    }

    if (human) {
      // The decision is made every tick, as before. What changes is that the
      // hand only gets to act on it now and then, so a direction the bot wanted
      // for two ticks and then changed its mind about never reaches the trace.
      if (want !== pendingWant) {
        pendingWant = want;
        actAt = t + REACT_MIN + rngInt(hand, REACT_SPREAD);
      }
      if (t >= actAt && pendingWant !== held) {
        queueInput(sim, t, pendingWant === 0 ? ACTION.NEUTRAL
          : (pendingWant > 0 ? ACTION.RIGHT : ACTION.LEFT));
        held = pendingWant;
      }
    } else if (want !== held) {
      queueInput(sim, t, want === 0 ? ACTION.NEUTRAL
        : (want > 0 ? ACTION.RIGHT : ACTION.LEFT));
      held = want;
    }

    // Only press when a press would fire. Recording presses that provably did
    // nothing would fill the trace with noise and tell the replay nothing.
    if (canFire(sim)) queueInput(sim, t, ACTION.FIRE);

    step(sim);
    sim.events.length = 0;
    if (topUp > 0 && sim.tick % topUp === 0 && sim.state !== STATE.DEAD) {
      sim.lives = 3;
    }
  }
  return sim;
}

function main() {
  const args = process.argv.slice(2);
  const flag = (name, fallback) => {
    const i = args.indexOf(name);
    return i >= 0 && args[i + 1] !== undefined ? Number(args[i + 1]) : fallback;
  };
  const runs = Number(args[0]) > 0 ? Number(args[0]) : 20;
  const baseSeed = flag('--seed', 1);
  const minutes = flag('--minutes', 12);
  const verbose = args.includes('--verbose');
  // Plays with a hand rather than a solver's reflexes. Slower and worse, and
  // the only mode that produces a trace the board should accept.
  const human = args.includes('--human');
  // Emits the traces themselves rather than a report, for tools that need runs
  // that got somewhere. Random input dies in the first wave, so it never
  // reaches a capture, a merged duck or a regression sweep -- which are exactly
  // the states a parity check most needs to cover.
  const traces = args.includes('--traces');
  const topUp = flag('--topup', 0);
  const maxTicks = Math.min(Math.round(minutes * 60 * 120), CONFIG.absoluteMaxTicks);

  if (traces) {
    const out = [];
    for (let i = 0; i < runs; i++) {
      const sim = playRun(baseSeed + i, maxTicks, topUp, human);
      out.push({ seed: baseSeed + i, inputs: sim.inputs, wave: sim.wave,
                 score: sim.score });
    }
    process.stdout.write(JSON.stringify(out));
    return;
  }

  const results = [];
  for (let i = 0; i < runs; i++) {
    const sim = playRun(baseSeed + i, maxTicks, topUp, human);
    results.push({
      seed: baseSeed + i,
      score: sim.score,
      wave: sim.wave,
      cleared: sim.wavesCleared,
      patched: sim.bugsPatched,
      shots: sim.shotsFired,
      forks: sim.forks,
      rescues: sim.rescues,
      secs: durationMs(sim) / 1000,
      trace: sim.inputs.length,
      finished: sim.state === STATE.DEAD,
    });
    if (verbose) {
      const r = results[results.length - 1];
      console.log(`seed ${r.seed}: ${r.score} pts, wave ${r.wave}, `
        + `${r.cleared} cleared, ${r.forks} forks, ${r.rescues} rescues, `
        + `${(r.patched / Math.max(1, r.shots) * 100).toFixed(0)}% hit, `
        + `${r.secs.toFixed(1)}s, ${r.trace} inputs`);
    }
  }

  const sum = (f) => results.reduce((a, r) => a + f(r), 0);
  const sorted = results.map((r) => r.score).sort((a, b) => a - b);
  const median = sorted[Math.floor(sorted.length / 2)];

  console.log(`\n${runs} runs, ${minutes} minute ceiling`);
  console.log(`score      min ${sorted[0]}  median ${median}  max ${sorted[sorted.length - 1]}`);
  console.log(`waves      cleared ${sum((r) => r.cleared)} total, `
    + `best wave ${Math.max(...results.map((r) => r.wave))}`);
  console.log(`the fork   ${sum((r) => r.forks)} forks, ${sum((r) => r.rescues)} rescues`);
  console.log(`length     median ${(results.map((r) => r.secs).sort((a, b) => a - b)[Math.floor(runs / 2)]).toFixed(1)}s, `
    + `longest ${Math.max(...results.map((r) => r.secs)).toFixed(1)}s`);
  console.log(`trace      longest ${Math.max(...results.map((r) => r.trace))} of ${CONFIG.maxInputTrace} allowed`);
  const stalled = results.filter((r) => !r.finished).length;
  if (stalled) console.log(`WARNING    ${stalled} run(s) hit the time ceiling without dying`);
  const noClear = results.filter((r) => r.cleared === 0).length;
  if (noClear) console.log(`NOTE       ${noClear} run(s) never cleared a wave`);
}

// Only run the harness when invoked directly, so playRun can be imported.
if (process.argv[1] && process.argv[1].endsWith('patchaga_bot.mjs')) main();
