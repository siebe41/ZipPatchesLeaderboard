/**
 * Sound effects, synthesised at play time.
 *
 * Four short effects, none of them a file. Oscillators and a noise buffer cost
 * a few hundred bytes of code where four audio files would cost four requests,
 * a format decision, and an original-recording problem. It also means there is
 * nothing to accidentally lift from somewhere else.
 *
 * Muted by default, because people will open this at a desk. The context is
 * only created on the first input, which is what mobile Safari requires: an
 * AudioContext built before a user gesture starts suspended and stays that way.
 */
export function createAudio(startMuted) {
  let ctx = null;
  let master = null;
  let muted = startMuted !== false;

  function unlock() {
    if (!ctx) {
      const Ctor = window.AudioContext || window.webkitAudioContext;
      if (!Ctor) return;
      ctx = new Ctor();
      master = ctx.createGain();
      master.gain.value = 0.28;
      master.connect(ctx.destination);
    }
    if (ctx.state === 'suspended') ctx.resume();
  }

  function env(node, at, attack, hold, release, peak) {
    const g = ctx.createGain();
    g.gain.setValueAtTime(0.0001, at);
    g.gain.exponentialRampToValueAtTime(peak, at + attack);
    g.gain.setValueAtTime(peak, at + attack + hold);
    g.gain.exponentialRampToValueAtTime(0.0001, at + attack + hold + release);
    node.connect(g);
    g.connect(master);
    return g;
  }

  function tone(type, from, to, at, attack, hold, release, peak) {
    const osc = ctx.createOscillator();
    osc.type = type;
    osc.frequency.setValueAtTime(from, at);
    osc.frequency.exponentialRampToValueAtTime(Math.max(1, to), at + attack + hold + release);
    env(osc, at, attack, hold, release, peak);
    osc.start(at);
    osc.stop(at + attack + hold + release + 0.02);
    return osc;
  }

  function noiseBuffer(seconds) {
    const len = Math.floor(ctx.sampleRate * seconds);
    const buf = ctx.createBuffer(1, len, ctx.sampleRate);
    const data = buf.getChannelData(0);
    let s = 1;
    for (let i = 0; i < len; i += 1) {
      s = (s * 1103515245 + 12345) & 0x7fffffff;
      data[i] = (s / 0x3fffffff) - 1;
    }
    return buf;
  }

  const effects = {
    // A quack is a buzzy downward glide with a nasal formant on top of it.
    flap() {
      const now = ctx.currentTime;
      const osc = ctx.createOscillator();
      osc.type = 'sawtooth';
      osc.frequency.setValueAtTime(520, now);
      osc.frequency.exponentialRampToValueAtTime(190, now + 0.11);
      const band = ctx.createBiquadFilter();
      band.type = 'bandpass';
      band.frequency.setValueAtTime(1100, now);
      band.frequency.exponentialRampToValueAtTime(620, now + 0.11);
      band.Q.value = 4.5;
      osc.connect(band);
      env(band, now, 0.008, 0.03, 0.075, 0.5);
      osc.start(now);
      osc.stop(now + 0.16);
    },
    score() {
      const now = ctx.currentTime;
      tone('square', 880, 880, now, 0.004, 0.03, 0.05, 0.22);
      tone('square', 1320, 1320, now + 0.055, 0.004, 0.03, 0.06, 0.22);
    },
    crash() {
      const now = ctx.currentTime;
      const src = ctx.createBufferSource();
      src.buffer = noiseBuffer(0.4);
      const lp = ctx.createBiquadFilter();
      lp.type = 'lowpass';
      lp.frequency.setValueAtTime(2400, now);
      lp.frequency.exponentialRampToValueAtTime(180, now + 0.34);
      src.connect(lp);
      env(lp, now, 0.004, 0.06, 0.3, 0.6);
      src.start(now);
      src.stop(now + 0.42);
      tone('triangle', 220, 60, now, 0.004, 0.04, 0.24, 0.3);
    },
    badge() {
      const now = ctx.currentTime;
      [660, 880, 1320].forEach((f, i) => {
        tone('sine', f, f, now + i * 0.075, 0.006, 0.045, 0.09, 0.26);
      });
    },
  };

  return {
    unlock,
    isMuted: () => muted,
    setMuted(value) { muted = !!value; },
    toggle() { muted = !muted; if (!muted) unlock(); return muted; },
    play(name) {
      if (muted) return;
      unlock();
      if (!ctx || !effects[name]) return;
      try { effects[name](); } catch (err) { /* a dead context is not fatal */ }
    },
  };
}
