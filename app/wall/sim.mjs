/**
 * The Patch Wall simulation.
 *
 * This module is pure: no DOM, no canvas, no timers, no Math.random, no wall
 * clock. Given a seed and a list of input events, it produces exactly the
 * same run every time, on any machine, at any frame rate. That property is
 * the whole reason the loop is built this way, and it is what lets the
 * server replay a submitted run in Python instead of believing the score it
 * was handed.
 *
 * The opponent paddle is not a second player's input, so it needs no
 * encoding: it is a deterministic function of the ball's state, the level,
 * and the tick, same as the ball's own physics.
 *
 * Positions and speeds are integers in sub-units. Every division floors.
 * Nothing here calls a transcendental function -- the ball's serve angle and
 * its bounce off a paddle are both linear, so there is no trigonometry to
 * disagree about between a browser and a Python port.
 *
 * None of the artwork or the names come from any existing arcade game. It is
 * a paddle-and-ball game, which is a genre, built out of Patch My PC's own
 * material: IT batting exploits back across the network boundary.
 */
import { CONFIG, fdiv, tierSpeedPct, aiReactTicks } from './config.mjs';
import { makeRng, rngInt } from './rng.mjs';

const U = CONFIG.unit;
const PX = (px) => px * U;
const WIDTH_SU = PX(CONFIG.width);
const HEIGHT_SU = PX(CONFIG.height);
const PLAYER_X_SU = PX(CONFIG.paddleMargin);
const AI_X_SU = WIDTH_SU - PX(CONFIG.paddleMargin);
const PADDLE_HALF_W_SU = PX(CONFIG.paddleHalfW);
const PADDLE_HALF_H_SU = PX(CONFIG.paddleHalfH);
const BALL_HALF_SU = PX(CONFIG.ballHalf);
const PADDLE_Y_MIN = PADDLE_HALF_H_SU;
const PADDLE_Y_MAX = HEIGHT_SU - PADDLE_HALF_H_SU;

export const STATE = {
  READY: 'ready',     // "GET READY", nothing moving yet
  PLAYING: 'playing',
  DYING: 'dying',      // reacting to a lost point, before the next serve
  CLEAR: 'clear',      // a round won, celebrating before the level sharpens
  DEAD: 'dead',        // out of lives, the run is over
};

/**
 * The three things a player can do. These are *edges*, not held state: the
 * trace records the moment a direction was taken or released, matching the
 * encoding every other game here uses -- `tick * 4 + action` -- so the
 * server's trace reader and its interval statistics need no second
 * implementation.
 */
export const ACTION = { UP: 0, DOWN: 1, NEUTRAL: 2 };

function clamp(v, lo, hi) {
  return v < lo ? lo : (v > hi ? hi : v);
}

function overlaps(aMin, aMax, bMin, bMax) {
  return aMin <= bMax && aMax >= bMin;
}

// --------------------------------------------------------------------------
// Building a run
// --------------------------------------------------------------------------

function centerY() {
  return fdiv(HEIGHT_SU, 2);
}

function makeBall() {
  return { x: fdiv(WIDTH_SU, 2), y: centerY(), vx: 0, vy: 0, inPlay: false };
}

export function createSim(seed) {
  const sim = {
    seed: seed >>> 0,
    rng: makeRng(seed),
    tick: 0,
    state: STATE.READY,
    stateTick: 0,

    score: 0,
    lives: CONFIG.lives,
    level: 1,
    nextExtraLife: CONFIG.extraLifeAt,

    player: { y: centerY(), dir: 0 },
    ai: { y: centerY(), targetY: centerY(), reactTimer: 0 },
    ball: makeBall(),
    prevPlayerY: centerY(),
    prevAiY: centerY(),
    prevBallX: fdiv(WIDTH_SU, 2),
    prevBallY: centerY(),

    serveTimer: CONFIG.serveDelayTicks,
    missesThisLevel: 0,
    rallyHits: 0,
    aiMisses: 0,
    levelsCleared: 0,

    playStartTick: -1,
    endTick: -1,

    pending: [],  // inputs queued but not yet reached
    inputs: [],   // every input actually applied. This is the trace.
    events: [],   // drained by the presentation layer; never read back here
  };
  return sim;
}

// --------------------------------------------------------------------------
// Input
// --------------------------------------------------------------------------

export function queueInput(sim, atTick, action) {
  const t = Math.max(sim.tick, Math.floor(atTick));
  if (t > CONFIG.absoluteMaxTicks) return;
  if (sim.inputs.length + sim.pending.length >= CONFIG.maxInputTrace) return;
  sim.pending.push(t * 4 + action);
}

function drainInput(sim) {
  let i = 0;
  while (i < sim.pending.length) {
    const code = sim.pending[i];
    if (fdiv(code, 4) > sim.tick) { i++; continue; }
    sim.pending.splice(i, 1);
    sim.inputs.push(sim.tick * 4 + (code % 4));
    applyAction(sim, code % 4);
  }
}

function applyAction(sim, action) {
  if (sim.state === STATE.READY) {
    if (sim.stateTick < CONFIG.readyTicks) return;
    sim.state = STATE.PLAYING;
    sim.stateTick = 0;
    sim.playStartTick = sim.tick;
  }
  if (action === ACTION.UP) sim.player.dir = -1;
  else if (action === ACTION.DOWN) sim.player.dir = 1;
  else sim.player.dir = 0;
}

// --------------------------------------------------------------------------
// Scoring and life cycle
// --------------------------------------------------------------------------

function addScore(sim, points) {
  sim.score = Math.min(CONFIG.maxScore, sim.score + points);
  if (sim.score >= sim.nextExtraLife && sim.lives < CONFIG.maxLives) {
    sim.lives += 1;
    sim.nextExtraLife += CONFIG.extraLifeEvery;
    sim.events.push({ type: 'extralife' });
  }
}

function resetBall(sim) {
  sim.ball = makeBall();
  sim.serveTimer = CONFIG.serveDelayTicks;
}

function launchServe(sim) {
  const dir = rngInt(sim.rng, 2) === 0 ? -1 : 1;
  const vy = rngInt(sim.rng, 2 * CONFIG.serveVyRange + 1) - CONFIG.serveVyRange;
  const speed = fdiv(CONFIG.ballBaseSpeedSu * tierSpeedPct(sim.level), 100);
  sim.ball.x = fdiv(WIDTH_SU, 2);
  sim.ball.y = centerY();
  sim.ball.vx = dir * speed;
  sim.ball.vy = vy;
  sim.ball.inPlay = true;
}

function loseLife(sim) {
  sim.lives -= 1;
  sim.state = STATE.DYING;
  sim.stateTick = 0;
  sim.events.push({ type: 'miss' });
}

function opponentMissed(sim) {
  addScore(sim, CONFIG.aiMissPoints);
  sim.aiMisses += 1;
  sim.missesThisLevel += 1;
  sim.events.push({ type: 'point' });
  if (sim.missesThisLevel >= CONFIG.missesToLevelUp) {
    sim.state = STATE.CLEAR;
    sim.stateTick = 0;
    sim.events.push({ type: 'clear' });
  } else {
    resetBall(sim);
  }
}

// --------------------------------------------------------------------------
// Paddles
// --------------------------------------------------------------------------

function movePlayer(sim) {
  const p = sim.player;
  p.y = clamp(p.y + p.dir * CONFIG.paddleSpeedSu, PADDLE_Y_MIN, PADDLE_Y_MAX);
}

function moveAi(sim) {
  const ai = sim.ai;
  const ball = sim.ball;
  if (!ball.inPlay || ball.vx > 0) {
    if (ai.reactTimer <= 0) {
      ai.targetY = ball.inPlay ? ball.y : centerY();
      ai.reactTimer = aiReactTicks(sim.level);
    } else {
      ai.reactTimer -= 1;
    }
  } else {
    ai.targetY = centerY();
  }
  const speed = fdiv(CONFIG.aiBaseSpeedSu * tierSpeedPct(sim.level), 100);
  if (ai.y < ai.targetY) ai.y = Math.min(ai.y + speed, ai.targetY);
  else if (ai.y > ai.targetY) ai.y = Math.max(ai.y - speed, ai.targetY);
  ai.y = clamp(ai.y, PADDLE_Y_MIN, PADDLE_Y_MAX);
}

// --------------------------------------------------------------------------
// The ball
// --------------------------------------------------------------------------

function bounceOffPaddle(sim, paddleY, towardPlayer) {
  const offset = clamp(sim.ball.y - paddleY, -PADDLE_HALF_H_SU, PADDLE_HALF_H_SU);
  const vy = fdiv(offset * CONFIG.maxSpinSu, PADDLE_HALF_H_SU);
  const speed = Math.min(CONFIG.ballMaxSpeedSu,
    Math.abs(sim.ball.vx) + CONFIG.ballSpeedIncrementSu);
  sim.ball.vx = (towardPlayer ? -1 : 1) * speed;
  sim.ball.vy = vy;
  sim.rallyHits += 1;
  addScore(sim, CONFIG.rallyPoints);
  sim.events.push({ type: 'hit' });
}

function advanceBall(sim) {
  const ball = sim.ball;
  ball.x += ball.vx;
  ball.y += ball.vy;

  if (ball.y - BALL_HALF_SU < 0) {
    ball.y = BALL_HALF_SU;
    ball.vy = -ball.vy;
    sim.events.push({ type: 'wall' });
  } else if (ball.y + BALL_HALF_SU > HEIGHT_SU) {
    ball.y = HEIGHT_SU - BALL_HALF_SU;
    ball.vy = -ball.vy;
    sim.events.push({ type: 'wall' });
  }

  if (ball.vx < 0
      && overlaps(ball.x - BALL_HALF_SU, ball.x + BALL_HALF_SU,
        PLAYER_X_SU - PADDLE_HALF_W_SU, PLAYER_X_SU + PADDLE_HALF_W_SU)
      && overlaps(ball.y - BALL_HALF_SU, ball.y + BALL_HALF_SU,
        sim.player.y - PADDLE_HALF_H_SU, sim.player.y + PADDLE_HALF_H_SU)) {
    bounceOffPaddle(sim, sim.player.y, false);
    return;
  }
  if (ball.vx > 0
      && overlaps(ball.x - BALL_HALF_SU, ball.x + BALL_HALF_SU,
        AI_X_SU - PADDLE_HALF_W_SU, AI_X_SU + PADDLE_HALF_W_SU)
      && overlaps(ball.y - BALL_HALF_SU, ball.y + BALL_HALF_SU,
        sim.ai.y - PADDLE_HALF_H_SU, sim.ai.y + PADDLE_HALF_H_SU)) {
    bounceOffPaddle(sim, sim.ai.y, true);
    return;
  }

  if (ball.x + BALL_HALF_SU < 0) {
    loseLife(sim);
  } else if (ball.x - BALL_HALF_SU > WIDTH_SU) {
    opponentMissed(sim);
  }
}

// --------------------------------------------------------------------------
// The step
// --------------------------------------------------------------------------

export function step(sim) {
  if (sim.state === STATE.DEAD) return;

  sim.prevPlayerY = sim.player.y;
  sim.prevAiY = sim.ai.y;
  sim.prevBallX = sim.ball.x;
  sim.prevBallY = sim.ball.y;

  drainInput(sim);

  if (sim.state === STATE.READY) {
    sim.stateTick += 1;
  } else if (sim.state === STATE.PLAYING) {
    movePlayer(sim);
    moveAi(sim);
    if (sim.serveTimer > 0) {
      sim.serveTimer -= 1;
      if (sim.serveTimer === 0) launchServe(sim);
    } else {
      advanceBall(sim);
    }
  } else if (sim.state === STATE.DYING) {
    sim.stateTick += 1;
    if (sim.stateTick >= CONFIG.dyingTicks) {
      if (sim.lives <= 0) {
        sim.state = STATE.DEAD;
        sim.endTick = sim.tick;
        sim.events.push({ type: 'gameover' });
      } else {
        resetBall(sim);
        sim.state = STATE.PLAYING;
        sim.stateTick = 0;
      }
    }
  } else if (sim.state === STATE.CLEAR) {
    sim.stateTick += 1;
    if (sim.stateTick >= CONFIG.clearTicks) {
      sim.level += 1;
      sim.missesThisLevel = 0;
      sim.levelsCleared += 1;
      resetBall(sim);
      sim.state = STATE.PLAYING;
      sim.stateTick = 0;
    }
  }

  sim.tick += 1;
}

export function replay(seed, inputs, maxTicks) {
  const sim = createSim(seed);
  sim.pending.push(...inputs);
  const last = inputs.length ? fdiv(inputs[inputs.length - 1], 4) : 0;
  const ceiling = maxTicks != null ? maxTicks
    : Math.min(last + CONFIG.tailTicks, CONFIG.absoluteMaxTicks);
  while (sim.state !== STATE.DEAD && sim.tick < ceiling) step(sim);
  return sim;
}

export function durationMs(sim) {
  if (sim.playStartTick < 0) return 0;
  const end = sim.endTick >= 0 ? sim.endTick : sim.tick;
  return Math.round((end - sim.playStartTick) * CONFIG.stepMs);
}
