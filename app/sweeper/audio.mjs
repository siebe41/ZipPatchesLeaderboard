/**
 * Sound, synthesised rather than loaded.
 *
 * Every effect is a short envelope over an oscillator or a burst of noise, so
 * the game ships no audio files. Muted is the default: a game that makes
 * noise the instant it loads is a game people close in an open-plan office.
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
    if (to !== from) osc.frequency.exponentialRampToValueAtTime(Math.max(1, to),
      now + seconds);
    env.gain.setValueAtTime(0.0001, now);
    env.gain.exponentialRampToValueAtTime(gain, now + 0.008);
    env.gain.exponentialRampToValueAtTime(0.0001, now + seconds);
    osc.connect(env);
    env.connect(master);
    osc.start(now);
    osc.stop(now + seconds + 0.02);
  }

  function noise(seconds, cutoff, gain = 1) {
    const c = ensure();
    if (!c) return;
    const now = c.currentTime;
    const frames = Math.max(1, Math.floor(c.sampleRate * seconds));
    const buffer = c.createBuffer(1, frames, c.sampleRate);
    const data = buffer.getChannelData(0);
    for (let i = 0; i < frames; i++) {
      data[i] = (Math.random() * 2 - 1) * (1 - i / frames);
    }
    const src = c.createBufferSource();
    src.buffer = buffer;
    const filter = c.createBiquadFilter();
    filter.type = 'lowpass';
    filter.frequency.value = cutoff;
    const env = c.createGain();
    env.gain.setValueAtTime(gain, now);
    env.gain.exponentialRampToValueAtTime(0.0001, now + seconds);
    src.connect(filter);
    filter.connect(env);
    env.connect(master);
    src.start(now);
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
    fire: () => tone('square', 900, 1500, 0.045, 0.28),
    break: () => { noise(0.14, 2400, 0.45); tone('square', 380, 100, 0.12, 0.3); },
    thrust: () => tone('sawtooth', 90, 70, 0.05, 0.06),
    die: () => { noise(0.3, 1000, 0.55); tone('sawtooth', 340, 60, 0.4, 0.35); },
    clear: () => arp([523, 659, 784, 1047, 1319], 0.085, 'square', 0.5),
    extralife: () => arp([784, 1047, 1319], 0.06, 'sine', 0.5),
    gameover: () => arp([392, 330, 262, 196], 0.16, 'sawtooth', 0.45),
  };

  return {
    unlock() { ensure(); },
    play(name) {
      const fn = effects[name];
      if (fn && !muted) {
        try { fn(); } catch (err) { /* a failed sound must never stop the game */ }
      }
    },
    toggle() {
      muted = !muted;
      if (muted && ctx) {
        ctx.suspend().catch(() => {});
      } else {
        ensure();
      }
      return muted;
    },
    isMuted() { return muted; },
  };
}
