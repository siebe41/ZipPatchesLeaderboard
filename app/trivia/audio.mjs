/**
 * Sound, synthesised rather than loaded. Same lightweight approach as every
 * other game here, trimmed to the handful of cues a quiz needs.
 */

export function createAudio(startMuted = true) {
  let ctx = null;
  let master = null;
  let muted = startMuted;

  function ensure() {
    if (muted) return null;
    if (!ctx) {
      const Ctx = window.AudioContext || window.webkitAudioContext;
      if (!Ctx) return null;
      ctx = new Ctx();
      master = ctx.createGain();
      master.gain.value = 0.18;
      master.connect(ctx.destination);
    }
    if (ctx.state === 'suspended') ctx.resume().catch(() => {});
    return ctx;
  }

  function tone(type, from, to, seconds, gain = 1) {
    const c = ensure();
    if (!c) return;
    const now = c.currentTime;
    const osc = c.createOscillator();
    const env = c.createGain();
    osc.type = type;
    osc.frequency.setValueAtTime(from, now);
    if (to !== from) osc.frequency.exponentialRampToValueAtTime(Math.max(1, to), now + seconds);
    env.gain.setValueAtTime(0.0001, now);
    env.gain.exponentialRampToValueAtTime(gain, now + 0.008);
    env.gain.exponentialRampToValueAtTime(0.0001, now + seconds);
    osc.connect(env);
    env.connect(master);
    osc.start(now);
    osc.stop(now + seconds + 0.02);
  }

  function arp(notes, step, type = 'square', gain = 0.7) {
    const c = ensure();
    if (!c) return;
    notes.forEach((hz, i) => {
      const now = c.currentTime + i * step;
      const osc = c.createOscillator();
      const env = c.createGain();
      osc.type = type;
      osc.frequency.setValueAtTime(hz, now);
      env.gain.setValueAtTime(0.0001, now);
      env.gain.exponentialRampToValueAtTime(gain, now + 0.01);
      env.gain.exponentialRampToValueAtTime(0.0001, now + step * 0.95);
      osc.connect(env);
      env.connect(master);
      osc.start(now);
      osc.stop(now + step + 0.02);
    });
  }

  const effects = {
    lockin: () => tone('square', 500, 700, 0.05, 0.25),
    correct: () => arp([523, 784, 1047], 0.07, 'square', 0.45),
    wrong: () => tone('sawtooth', 300, 100, 0.28, 0.3),
    start: () => arp([392, 523, 659, 784], 0.07, 'square', 0.4),
    final: () => arp([523, 659, 784, 1047, 1319], 0.09, 'square', 0.5),
  };

  return {
    unlock() { ensure(); },
    play(name) {
      const fn = effects[name];
      if (fn && !muted) {
        try { fn(); } catch (err) { /* a failed sound must never break the game */ }
      }
    },
    toggle() {
      muted = !muted;
      if (muted && ctx) ctx.suspend().catch(() => {}); else ensure();
      return muted;
    },
    isMuted() { return muted; },
  };
}
