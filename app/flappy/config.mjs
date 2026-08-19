/**
 * Every tuning constant for Flappy Duck, in one place.
 *
 * Playtesting a flappy clone is a numbers exercise, not a code exercise. If a
 * value that affects feel lives anywhere else in this folder, it is in the
 * wrong place. Distances are logical pixels on the 288x512 canvas and speeds
 * are per second, so a value here reads the same as it does in the scope doc.
 *
 * Difficulty is deliberately constant. The original does not ramp, and a ramp
 * makes a long run feel arbitrary rather than earned.
 */
export const CONFIG = {
  // --- Canvas -------------------------------------------------------------
  width: 288,
  height: 512,
  groundY: 448, // top edge of the ground strip, and the surface you die on

  // --- Simulation ---------------------------------------------------------
  stepMs: 1000 / 120, // fixed timestep
  // A backgrounded tab hands back a gap of minutes. Without a clamp the loop
  // would try to catch up in one frame and lock the page.
  maxCatchUpSteps: 240,

  // --- Duck ---------------------------------------------------------------
  duckX: 62,
  duckW: 34,
  duckH: 24,
  hitW: 30, // inset from the sprite so near misses read as fair
  hitH: 20,
  startY: 214,
  gravity: 1200,
  // Replaces vertical velocity rather than adding to it. That single detail
  // is what makes the control feel right.
  flapImpulse: -350,
  terminalFall: 400,
  ceilingY: -12, // the ceiling clamps instead of killing, matching the original

  // --- Obstacles ----------------------------------------------------------
  scrollSpeed: 110,
  gapHeight: 100,
  spacing: 160,
  firstObstacleX: 340, // lead-in before the first application tile arrives
  gapCenterMin: 110,
  gapCenterMax: 370,
  // Playtest finding, not in the original brief. The full 110-370 range lets
  // two neighbouring gaps sit 260 px apart, and a duck climbing flat out only
  // covers about 250 px in the 1.45 s between obstacles. That gap is not hard,
  // it is unreachable, and losing to it feels arbitrary rather than earned.
  // Clamping the step between neighbours keeps the whole range in play while
  // guaranteeing every placement can actually be flown.
  gapCenterMaxDelta: 130,
  tileW: 52, // collision width; the cap art is wider and overhangs harmlessly
  capW: 60,
  capH: 26,
  bodyH: 24,

  // --- Presentation -------------------------------------------------------
  bgParallax: 0.28, // fraction of the scroll speed the skyline moves at
  groundParallax: 1.0,
  bgH: 448,
  groundH: 64,
  rotateUpDeg: -25,
  rotateDownDeg: 90,
  rotateEase: 9, // per second, how fast the tilt chases its target
  wingFrameMs: 90,
  wingHoldMs: 260, // wings snap open on a flap, then settle while falling
  shakeMs: 320,
  shakeAmp: 5,
  flashMs: 110,
  bobAmp: 4.5,
  bobHz: 1.1,

  // --- Scoring and theme --------------------------------------------------
  badges: [
    { at: 50, key: 'badge_gold', label: 'GOLD DEPLOYMENT' },
    { at: 25, key: 'badge_silver', label: 'SILVER DEPLOYMENT' },
    { at: 10, key: 'badge_bronze', label: 'BRONZE DEPLOYMENT' },
  ],

  // --- Storage ------------------------------------------------------------
  bestKey: 'flappyduck.best',
  playerKey: 'flappyduck.player',
  mutedKey: 'flappyduck.muted',

  // --- Submission ---------------------------------------------------------
  maxFlapTrace: 5000, // the server caps this too; agreeing avoids a silent trim
};

export const SIM_DT = CONFIG.stepMs / 1000;
