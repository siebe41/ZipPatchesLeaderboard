/**
 * Replay PatchMan runs in the browser's engine and print the results as JSON.
 *
 * Driven by tools/check_patchman_parity.py, which feeds cases in on stdin and
 * compares every field against its own Python replay. Kept deliberately dumb:
 * it must not normalise, round or tidy anything, because a difference this hid
 * would be a difference the server and the client would go on to have.
 */
import { readFileSync } from 'node:fs';
import { createSim, queueTurn, step, durationMs, STATE }
  from '../app/patchman/sim.mjs';

const input = JSON.parse(readFileSync(0, 'utf8'));

function replayCase(seed, turns, maxTicks) {
  const sim = createSim(seed);
  let next = 0;
  while (sim.state !== STATE.DEAD && sim.tick < maxTicks) {
    while (next < turns.length && Math.floor(turns[next] / 4) <= sim.tick) {
      queueTurn(sim, Math.floor(turns[next] / 4), turns[next] % 4);
      next += 1;
    }
    step(sim);
  }
  return sim;
}

const out = input.cases.map((c) => {
  const sim = replayCase(c.seed, c.turns, c.maxTicks);
  return {
    score: sim.score,
    duration_ms: durationMs(sim),
    tick: sim.tick,
    end_tick: sim.endTick,
    play_start_tick: sim.playStartTick,
    state: sim.state,
    level: sim.level,
    lives: sim.lives,
    patches_left: sim.patchesLeft,
    total_patches: sim.totalPatches,
    pac_x: sim.pac.x,
    pac_y: sim.pac.y,
    pac_dir: sim.pac.dir,
    pac_want: sim.pac.want,
    phase_index: sim.phaseIndex,
    phase_kind: sim.phaseKind,
    fright_ticks: sim.frightTicks,
    fright_chain: sim.frightChain,
    vulns_patched: sim.vulnsPatched,
    elroy_stage: sim.elroyStage,
    freeze_ticks: sim.freezeTicks,
    bonus_state: sim.bonusState,
    bonuses_shown: sim.bonusesShown,
    house_idle: sim.houseIdle,
    vulns: sim.vulns.map((g) => [g.x, g.y, g.dir, g.state, g.fright ? 1 : 0]),
    turns: sim.turns,
    tiles: sim.tiles.join(''),
  };
});

process.stdout.write(JSON.stringify(out));
