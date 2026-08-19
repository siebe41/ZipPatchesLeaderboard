/**
 * Determinism and physics tests for the Flappy Duck simulation.
 *
 *     node tools/test_sim.mjs
 *
 * No test framework, because adding one would mean adding a package manager to
 * a repo whose deploy promise is "upload the files and restart". The modules
 * under test are .mjs, so Node imports them directly with no config file.
 *
 * The claim being tested is narrow and load-bearing: an identical sequence of
 * inputs, expressed in wall-clock milliseconds, produces a bit-identical run at
 * any frame rate. Everything else in the game leans on that.
 */
import { CONFIG, SIM_DT } from '../app/flappy/config.mjs';
import { makeRng } from '../app/flappy/rng.mjs';
import {
  createSim, queueFlap, step, snapshot, replay, durationMs,
  duckHitbox, obstacleAt, visibleRange, STATE, CAUSE,
} from '../app/flappy/sim.mjs';

let passed = 0;
const failures = [];

function check(name, condition, detail) {
  if (condition) {
    passed += 1;
    console.log('  ok   ' + name);
  } else {
    failures.push(name + (detail ? ' :: ' + detail : ''));
    console.log('  FAIL ' + name + (detail ? ' :: ' + detail : ''));
  }
}

function same(name, a, b) {
  const ja = JSON.stringify(a);
  const jb = JSON.stringify(b);
  check(name, ja === jb, ja === jb ? '' : ja.slice(0, 220) + '\n        vs ' + jb.slice(0, 220));
}

// --------------------------------------------------------------------------
// An autopilot, used only to generate a realistic input trace to test with.
// It holds the duck in a sawtooth just under the gap centre, which is roughly
// what a competent human does.
// --------------------------------------------------------------------------

/** Where the duck's sprite origin should sit to fly the obstacle ahead of it. */
function holdLine(sim) {
  const { first, last } = visibleRange(sim);
  for (let i = first; i <= last; i += 1) {
    const ob = obstacleAt(sim, i);
    if (ob.x + CONFIG.tileW > CONFIG.duckX) {
      // A flap lifts about 51 px, so aiming a little below the centre keeps
      // the whole oscillation inside the gap rather than clipping its top.
      return ob.center + 10 - (CONFIG.duckH - CONFIG.hitH) / 2 - CONFIG.hitH / 2;
    }
  }
  return CONFIG.startY;
}

function autopilotInputTimes(seed, maxTicks = 120000) {
  const sim = createSim(seed);
  const times = [];
  queueFlap(sim, 0);
  times.push(0);
  while (sim.state !== STATE.DEAD && sim.tick < maxTicks) {
    if (sim.state === STATE.PLAYING && sim.duckVy >= 0 && sim.duckY >= holdLine(sim)) {
      queueFlap(sim, sim.tick);
      times.push(sim.tick * CONFIG.stepMs);
    }
    step(sim);
  }
  return { times, reference: snapshot(sim) };
}

/** The first seed whose autopilot run clears at least `want` obstacles. */
function traceScoring(want) {
  for (let seed = 20260819; seed < 20260819 + 200; seed += 1) {
    const run = autopilotInputTimes(seed);
    if (run.reference.score >= want) return { seed, ...run };
  }
  throw new Error('no seed produced a scoring autopilot run');
}

// --------------------------------------------------------------------------
// A virtual game loop. This is the same absolute-time accumulator the browser
// loop uses, so testing it here tests the real thing.
// --------------------------------------------------------------------------

function runAtFrameRate(seed, inputTimes, hz, jitterSeed = 0) {
  const sim = createSim(seed);
  const frameDelta = 1000 / hz;
  const jitter = jitterSeed ? makeRng(jitterSeed) : null;
  let now = 0;
  let nextInput = 0;
  let frames = 0;

  while (sim.state !== STATE.DEAD && now < 600000) {
    now += jitter ? frameDelta * (0.4 + jitter() * 1.4) : frameDelta;
    frames += 1;

    // Inputs are stamped on arrival and quantised to a tick, not to a frame.
    while (nextInput < inputTimes.length && inputTimes[nextInput] <= now) {
      queueFlap(sim, Math.floor(inputTimes[nextInput] / CONFIG.stepMs));
      nextInput += 1;
    }

    const target = Math.floor(now / CONFIG.stepMs);
    let guard = 0;
    while (sim.tick < target && guard < CONFIG.maxCatchUpSteps) {
      step(sim);
      guard += 1;
    }
  }
  return { snap: snapshot(sim), frames };
}

// --------------------------------------------------------------------------

console.log('\nseeded placement');
{
  const a = createSim(12345);
  const b = createSim(12345);
  for (let i = 0; i < 40; i += 1) { obstacleAt(a, i); obstacleAt(b, i); }
  same('same seed gives the same gap sequence', a.gaps, b.gaps);

  const c = createSim(999);
  for (let i = 0; i < 40; i += 1) obstacleAt(c, i);
  check('a different seed gives a different sequence',
    JSON.stringify(a.gaps) !== JSON.stringify(c.gaps));

  const inRange = a.gaps.every(
    (g) => g >= CONFIG.gapCenterMin && g <= CONFIG.gapCenterMax && Number.isInteger(g));
  check('every gap centre is an integer inside the configured range', inRange);

  const gapBottom = CONFIG.gapCenterMax + CONFIG.gapHeight / 2;
  check('the lowest gap still clears the ground', gapBottom < CONFIG.groundY,
    'gap bottom ' + gapBottom + ' vs ground ' + CONFIG.groundY);
  check('the highest gap still clears the ceiling',
    CONFIG.gapCenterMin - CONFIG.gapHeight / 2 > CONFIG.ceilingY);
}

console.log('\nphysics');
{
  const sim = createSim(7);
  queueFlap(sim, 0);
  step(sim);
  check('the first input starts the run', sim.state === STATE.PLAYING);
  check('a flap replaces vertical velocity rather than adding to it',
    Math.abs(sim.duckVy - (CONFIG.flapImpulse + CONFIG.gravity * SIM_DT)) < 1e-9,
    'vy=' + sim.duckVy);

  // Two flaps in quick succession must not stack into a rocket.
  const s2 = createSim(7);
  queueFlap(s2, 0);
  step(s2);
  queueFlap(s2, s2.tick);
  step(s2);
  check('a second flap does not compound the first',
    Math.abs(s2.duckVy - (CONFIG.flapImpulse + CONFIG.gravity * SIM_DT)) < 1e-9,
    'vy=' + s2.duckVy);

  const fall = createSim(7);
  queueFlap(fall, 0);
  for (let i = 0; i < 2000; i += 1) step(fall);
  check('the fall is capped at terminal speed',
    fall.duckVy <= CONFIG.terminalFall + 1e-9);
}

console.log('\nceiling clamps instead of killing');
{
  const sim = createSim(4242);
  let alive = true;
  for (let i = 0; i < 1200; i += 1) {
    queueFlap(sim, sim.tick); // hold the button down forever
    step(sim);
    if (sim.state === STATE.DYING || sim.state === STATE.DEAD) {
      if (sim.cause === CAUSE.GROUND) alive = false;
      break;
    }
  }
  check('flying into the ceiling does not end the run by itself',
    sim.duckY >= CONFIG.ceilingY - 1e-9 && alive);
  check('the duck is held at the ceiling', sim.duckY <= CONFIG.ceilingY + 1e-9);
}

console.log('\nhitbox');
{
  const box = duckHitbox(100);
  check('the collision box is inset from the sprite',
    box.w === CONFIG.hitW && box.h === CONFIG.hitH
    && box.x > CONFIG.duckX && box.y > 100,
    JSON.stringify(box));
  check('the collision width is narrower than the cap art',
    CONFIG.tileW < CONFIG.capW);
}

console.log('\nscoring');
{
  const sim = createSim(31337);
  queueFlap(sim, 0);
  let centerWhenScored = null;
  const duckCenter = CONFIG.duckX + CONFIG.duckW / 2;
  for (let i = 0; i < 6000 && sim.score === 0 && sim.state !== STATE.DEAD; i += 1) {
    if (sim.state === STATE.PLAYING && sim.duckVy >= 0 && sim.duckY >= holdLine(sim)) {
      queueFlap(sim, sim.tick);
    }
    const before = obstacleAt(sim, 0).x + CONFIG.tileW / 2;
    step(sim);
    if (sim.score === 1 && centerWhenScored === null) centerWhenScored = before;
  }
  check('a cleared obstacle scores', sim.score >= 1, 'state ' + sim.state);
  check('the score lands as the duck centre passes the obstacle centre',
    centerWhenScored !== null && Math.abs(centerWhenScored - duckCenter) <= 2,
    'obstacle centre was ' + centerWhenScored + ', duck centre ' + duckCenter);
}

console.log('\nreachability');
{
  // A gap the duck physically cannot climb to is not difficulty, it is a
  // coin flip, so the placement rule has to guarantee every step is flyable.
  const climbPerFlapCycle = (CONFIG.flapImpulse * CONFIG.flapImpulse)
    / (2 * CONFIG.gravity);
  const cycleSeconds = -CONFIG.flapImpulse / CONFIG.gravity;
  const climbRate = climbPerFlapCycle / cycleSeconds;
  const secondsBetween = CONFIG.spacing / CONFIG.scrollSpeed;
  check('the largest allowed step between gaps is climbable',
    CONFIG.gapCenterMaxDelta < climbRate * secondsBetween,
    'delta ' + CONFIG.gapCenterMaxDelta + ' vs reachable '
      + Math.round(climbRate * secondsBetween));

  const sim = createSim(881);
  for (let i = 0; i < 300; i += 1) obstacleAt(sim, i);
  const worst = sim.gaps.slice(1).reduce(
    (m, g, i) => Math.max(m, Math.abs(g - sim.gaps[i])), 0);
  check('no generated pair exceeds the limit', worst <= CONFIG.gapCenterMaxDelta,
    'worst step ' + worst);
  check('the generator still uses the whole configured range',
    Math.min(...sim.gaps) < CONFIG.gapCenterMin + 30
    && Math.max(...sim.gaps) > CONFIG.gapCenterMax - 30,
    'saw ' + Math.min(...sim.gaps) + ' to ' + Math.max(...sim.gaps));
}

console.log('\nframe rate independence');
{
  const { seed, times, reference } = traceScoring(8);
  check('the test trace is a real run',
    reference.score >= 8 && times.length > 10,
    'seed ' + seed + ' scored ' + reference.score + ' from ' + times.length + ' inputs');

  const at30 = runAtFrameRate(seed, times, 30);
  const at60 = runAtFrameRate(seed, times, 60);
  const at144 = runAtFrameRate(seed, times, 144);
  const at240 = runAtFrameRate(seed, times, 240);
  const jittered = runAtFrameRate(seed, times, 60, 0xbeef);

  check('the frame rates really did differ',
    at30.frames !== at60.frames && at60.frames !== at144.frames,
    at30.frames + ' / ' + at60.frames + ' / ' + at144.frames + ' frames');

  same('30 Hz matches 60 Hz', at30.snap, at60.snap);
  same('144 Hz matches 60 Hz', at144.snap, at60.snap);
  same('240 Hz matches 60 Hz', at240.snap, at60.snap);
  same('a jittered variable frame time matches 60 Hz', jittered.snap, at60.snap);
  check('the outcome is a real score at every rate', at60.snap.score >= 8,
    'score ' + at60.snap.score);
}

console.log('\nreplay from the recorded trace');
{
  const { seed, times } = traceScoring(8);
  const live = runAtFrameRate(seed, times, 60);
  const again = replay(seed, live.snap.flaps);
  same('replaying the seed and flap trace reproduces the run',
    snapshot(again), live.snap);

  const other = replay(seed + 7, live.snap.flaps);
  check('the same trace on a different seed is a different run',
    other.score !== again.score || other.deathTick !== again.deathTick,
    'both scored ' + again.score + ' and died at ' + again.deathTick);

  const twice = replay(seed, live.snap.flaps);
  same('replay is stable across calls', snapshot(twice), snapshot(again));

  const d = durationMs(again);
  check('duration is measured from the first flap',
    d > 0 && Math.abs(d - (again.deathTick - again.playStartTick) * CONFIG.stepMs) < 1,
    'duration ' + d + 'ms for score ' + again.score);
}

console.log('\ndeath');
{
  const sim = createSim(555);
  queueFlap(sim, 0);
  for (let i = 0; i < 20000 && sim.state !== STATE.DEAD; i += 1) step(sim);
  check('doing nothing ends on the ground', sim.state === STATE.DEAD);
  check('the duck comes to rest on the ground',
    Math.abs(duckHitbox(sim.duckY).y + CONFIG.hitH - CONFIG.groundY) < 1e-6);

  const settled = snapshot(sim);
  for (let i = 0; i < 500; i += 1) step(sim);
  same('a finished run never changes again', snapshot(sim), settled);
}

console.log('');
if (failures.length) {
  console.log(failures.length + ' failed, ' + passed + ' passed');
  for (const f of failures) console.log('  - ' + f);
  process.exit(1);
}
console.log(passed + ' passed, 0 failed');
