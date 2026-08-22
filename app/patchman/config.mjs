/**
 * Every tuning constant for PatchMan, in one place.
 *
 * A maze chase game lives or dies on its numbers, so if a value that affects
 * how the game feels lives anywhere else in this folder, it is in the wrong
 * place.
 *
 * On units. The simulation never uses pixels and never uses floating point for
 * position. An entity's x and y are integers in *sub-units*, and one tile is
 * `cell` of them. That is not fussiness: the server replays every submitted run
 * in Python to decide the score, and integer arithmetic is the only kind that
 * two languages agree on without argument. It also makes "is this entity
 * exactly on a tile centre" an equality test rather than a tolerance, which is
 * what keeps turns and collisions from depending on the frame rate.
 *
 * Rendering is the only place pixels appear. `tile` is a drawing constant.
 */

export const CONFIG = {
  // --- Grid ---------------------------------------------------------------
  cols: 27,
  rows: 31,
  cell: 64,   // sub-units per tile, in both axes
  tile: 16,   // pixels per tile, rendering only

  // --- Canvas -------------------------------------------------------------
  width: 27 * 16,
  mazeTop: 40,          // room for the score strip
  height: 40 + 31 * 16 + 24,

  // --- Simulation ---------------------------------------------------------
  stepMs: 1000 / 120,
  maxCatchUpSteps: 480, // a backgrounded tab hands back minutes; do not chase it

  // --- Speeds, in sub-units per tick --------------------------------------
  // 4 sub-units at 120 Hz is 60 logical pixels a second, which is seven and a
  // half tiles a second: the pace the genre settled on decades ago. Vulnerable
  // things move at three quarters of that, which is the ratio that makes a
  // chase down a long corridor survivable and a chase into a corner not.
  //
  // Indexed by speed tier, which levels map onto below. Nothing here is
  // allowed to be fractional, because a fraction of a sub-unit is where the
  // two engines would start to disagree.
  speeds: [
    { patchman: 4, energized: 5, vuln: 3, elroy: 4, frightened: 2, eyes: 8, tunnel: 2 },
    { patchman: 4, energized: 5, vuln: 3, elroy: 4, frightened: 2, eyes: 8, tunnel: 2 },
    { patchman: 5, energized: 5, vuln: 4, elroy: 5, frightened: 2, eyes: 8, tunnel: 3 },
    { patchman: 5, energized: 6, vuln: 4, elroy: 5, frightened: 3, eyes: 8, tunnel: 3 },
  ],
  // level -> index into speeds. Anything past the end uses the last tier.
  speedTier: [0, 0, 1, 1, 2, 2, 2, 3],

  // --- Scoring ------------------------------------------------------------
  patchPoints: 10,
  logoPoints: 50,
  // Patching a vulnerability while the logo is up. The value doubles for each
  // one caught in the same window, which is the whole reason to chase them.
  vulnPoints: [200, 400, 800, 1600],
  // Clearing every patch on a board. Enough to be worth finishing a level
  // cleanly rather than dying on the last few.
  levelBonus: 500,

  // --- Timing, in ticks ---------------------------------------------------
  readyTicks: 150,        // the pause before a board starts
  respawnTicks: 120,      // the pause after a death, before the board restarts
  deathTicks: 180,        // the death animation
  levelClearTicks: 150,   // the board flash after the last patch
  eatFreezeTicks: 45,     // everything stops while a score pops up
  bonusTicks: 1080,       // how long a bonus token stays on the board

  // How long the logo keeps vulnerabilities patchable, by level, in ticks.
  // It shrinks because a constant window makes late levels easier than early
  // ones, which is backwards. Ticks rather than seconds on purpose: the server
  // replays every run, and a value both engines read as an integer cannot
  // round differently in one of them.
  frightenedTicks: [960, 840, 720, 600, 480, 360, 300, 240, 180, 120],
  // The last stretch of that window is spent flashing, as the only warning.
  frightenedFlashTicks: 240,

  // Scatter and chase, in ticks. A vulnerability that only ever chased would
  // corner you in the first ten seconds of every board, so they periodically
  // give up and go home. Reversing on the switch is what makes the change
  // readable from across the maze. A length of 0 means "and stay there".
  phasesEarly: [
    ['scatter', 840], ['chase', 2400], ['scatter', 840], ['chase', 2400],
    ['scatter', 600], ['chase', 2400], ['scatter', 600], ['chase', 0],
  ],
  phasesLate: [
    ['scatter', 600], ['chase', 2400], ['scatter', 600], ['chase', 3000],
    ['scatter', 600], ['chase', 3000], ['scatter', 360], ['chase', 0],
  ],
  phasesLateFromLevel: 5,

  // --- The house ----------------------------------------------------------
  // How many patches have to be collected before each vulnerability is let
  // out, and how long a lull is allowed before one is let out anyway. Without
  // the lull rule a player who stops eating keeps the board to themselves.
  releaseAt: [0, 0, 20, 50],
  releaseIdleTicks: 480,
  houseLaneRow: 16,       // the row inside the house the lane runs along
  houseExitRow: 14,       // the corridor tile directly above the door
  doorCol: 13,
  homeCols: [13, 13, 11, 15],
  houseBobUnits: 16,      // how far a waiting vulnerability drifts, each way

  // --- Cruise mode --------------------------------------------------------
  // With most of the board cleared, RCE stops scattering and speeds up. It is
  // the difference between a board that peters out and one that closes in.
  elroyAt: 40,
  elroyAtHarder: 18,

  // --- Bonus tokens -------------------------------------------------------
  bonusAt: [70, 170],     // patches collected when a token appears
  bonusTile: [13, 19],
  bonuses: [
    { key: 'hotfix', label: 'HOTFIX', points: 100 },
    { key: 'tuesday', label: 'PATCH TUESDAY', points: 300 },
    { key: 'servicepack', label: 'SERVICE PACK', points: 500 },
    { key: 'cvefix', label: 'CVE FIX', points: 700 },
    { key: 'zerodayfix', label: 'ZERO-DAY FIX', points: 1000 },
    { key: 'goldbuild', label: 'GOLD BUILD', points: 2000 },
    { key: 'ltsc', label: 'LTSC BRANCH', points: 3000 },
    { key: 'evergreen', label: 'EVERGREEN', points: 5000 },
  ],

  // --- Lives --------------------------------------------------------------
  lives: 3,

  // --- The vulnerabilities ------------------------------------------------
  // Four of them, four temperaments, which is what turns a maze into a game.
  // The scatter corners are deliberately outside the walls: a target nothing
  // can stand on is what makes them circle it instead of settling on it.
  vulns: [
    {
      key: 'rce', label: 'RCE', name: 'Remote Code Execution',
      color: '#ff4d5e', scatter: [25, 0],
      blurb: 'Comes straight at you.',
    },
    {
      key: 'xss', label: 'XSS', name: 'Cross-Site Scripting',
      color: '#ff8fd6', scatter: [1, 0],
      blurb: 'Aims four tiles ahead of you.',
    },
    {
      key: 'sqli', label: 'SQLI', name: 'SQL Injection',
      color: '#4fd6e8', scatter: [25, 30],
      blurb: 'Pivots off RCE to flank you.',
    },
    {
      key: 'zday', label: '0DAY', name: 'Zero Day',
      color: '#ffb03a', scatter: [1, 30],
      blurb: 'Bold at range, shy up close.',
    },
  ],
  // How far ahead XSS aims, and the lever SQLI pivots on.
  ambushTiles: 4,
  flankTiles: 2,
  // Under this many tiles away, 0DAY loses its nerve and heads for its corner.
  timidTiles: 8,

  // --- Presentation -------------------------------------------------------
  // A patchable vulnerability is Patch My PC green, because that is what the
  // logo just did to it. Blue was the obvious choice and the wrong one: these
  // walls are already blue, so a blue vulnerability disappeared into them at
  // exactly the moment the player most needs to see where it is.
  frightColor: '#7ac143',
  frightFlashColor: '#f2fbe8',
  chompFrames: 4,
  chompFrameMs: 55,
  bobHz: 1.6,

  // --- Scoring badges -----------------------------------------------------
  badges: [
    { at: 50000, key: 'badge_zero', label: 'ZERO OUTSTANDING CVES' },
    { at: 25000, key: 'badge_fleet', label: 'FLEET FULLY PATCHED' },
    { at: 10000, key: 'badge_hero', label: 'PATCH TUESDAY HERO' },
    { at: 5000, key: 'badge_compliant', label: 'COMPLIANCE MET' },
  ],

  // --- Storage ------------------------------------------------------------
  bestKey: 'patchman.best',
  playerKey: 'patchman.player',
  mutedKey: 'patchman.muted',

  // --- Submission ---------------------------------------------------------
  // The server caps this too; agreeing avoids a silent trim.
  maxInputTrace: 4000,
  // A run cannot outlast this. It is a limit on what the server is willing to
  // replay as much as it is a limit on the game, and both engines apply it, so
  // an endless run ends the same way on both. Keep it equal to
  // ABSOLUTE_MAX_TICKS in patchman.py.
  maxTicks: 120 * 60 * 12,

  /**
   * The board.
   *
   * Generated and proved by `tools/make_patchman_maze.py`, which checks it is
   * left-right symmetric, sealed apart from the tunnel and the door, free of
   * open two-by-two rooms and dead ends, and that every patch can be reached.
   * Run that script with `--check` after editing this by hand; a maze with one
   * walled-off pocket is a board that can never be cleared.
   *
   *   #  wall        .  patch        o  Patch My PC logo
   *   -  house door  (space) open floor, nothing on it
   *
   * The design is original. Corridors are one tile wide, the blocks between
   * them are chunky and rectangular, and the whole thing is meant to read as a
   * board layout seen from above.
   */
  maze: [
    '###########################',
    '#.........................#',
    '#.###.#######.#######.###.#',
    '#.###.#######.#######.###.#',
    '#.###.#######.#######.###.#',
    '#o###.................###o#',
    '#.#######.#######.#######.#',
    '#.#######.#######.#######.#',
    '#.#######.#######.#######.#',
    '#.........#######.........#',
    '#.###.###.#######.###.###.#',
    '#.###.###.#######.###.###.#',
    '#.###.###.#######.###.###.#',
    '#.###.###.#######.###.###.#',
    '     .................     ',
    '#.#######.###-###.#######.#',
    '#.#######.#     #.#######.#',
    '#.#######.#     #.#######.#',
    '#.#######.#######.#######.#',
    '#.....###.... ....###.....#',
    '#.###.#######.#######.###.#',
    '#.###.#######.#######.###.#',
    '#.###.#######.#######.###.#',
    '#.###.#######.#######.###.#',
    '#o###........ ........###o#',
    '#.###.###.#######.###.###.#',
    '#.###.###.#######.###.###.#',
    '#.###.###.#######.###.###.#',
    '#.###.###.#######.###.###.#',
    '#.........................#',
    '###########################',
  ],

  // Where PatchMan starts each life. A four-way junction, well clear of the
  // door, so the first second of a life is never a coin toss.
  startTile: [13, 24],
};

export const TUNNEL_ROW = 14;
