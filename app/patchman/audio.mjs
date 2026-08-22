/**
 * Sound effects, synthesised at play time.
 *
 * Nothing here is a file. Oscillators and a noise buffer cost a few hundred
 * bytes of code where a set of samples would cost several requests, a format
 * decision, and an original-recording problem. It also means there is nothing
 * that could have come from somewhere it should not have.
 *
 * Muted by default, because people will open this at a desk. The context is
 * only created on the first input, which is what mobile Safari requires: an
 * AudioContext built before a user gesture starts suspended and stays that way.
 */
export function createAudio(startMuted) {
  let ctx = null;
  let master = null;
  let muted = startMuted !== false;
  // The chomp alternates pitch so a held run of patches sounds like a run
  // rather than like one note stuck on repeat.
  let chompFlip = 0;

  function unlock() {
    if (!ctx) {
      const Ctor = window.AudioContext || window.webkitAudioContext;
      if (!Ctor) return;
      ctx = new Ctor();
      master = ctx.createGain();
      master.gain.value = 0.24;
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
    osc.frequency.exponentialRampToValueAtTime(Math.max(1, to),
      at + attack + hold + release);
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
    // A short square blip. Two alternating pitches, and quiet, because it
    // fires several times a second for minutes at a time.
    patch() {
      const now = ctx.currentTime;
      chompFlip = 1 - chompFlip;
      tone('square', chompFlip ? 320 : 240, chompFlip ? 240 : 320,
        now, 0.002, 0.012, 0.03, 0.10);
    },
    // Collecting a logo: a rising arpeggio that says something good is coming.
    logo() {
      const now = ctx.currentTime;
      [440, 660, 880].forEach((f, i) => {
        tone('triangle', f, f, now + i * 0.045, 0.005, 0.03, 0.07, 0.22);
      });
    },
    // Patching a vulnerability: a downward sweep, the opposite shape to a
    // threat, plus a click of confirmation.
    patched() {
      const now = ctx.currentTime;
      tone('sawtooth', 900, 180, now, 0.004, 0.02, 0.18, 0.24);
      tone('square', 1400, 1400, now, 0.002, 0.01, 0.03, 0.12);
    },
    bonus() {
      const now = ctx.currentTime;
      [523, 659, 784, 1047].forEach((f, i) => {
        tone('square', f, f, now + i * 0.055, 0.004, 0.03, 0.06, 0.20);
      });
    },
    // Being caught: filtered noise falling away, with a low thud under it.
    breach() {
      const now = ctx.currentTime;
      const src = ctx.createBufferSource();
      src.buffer = noiseBuffer(0.5);
      const lp = ctx.createBiquadFilter();
      lp.type = 'lowpass';
      lp.frequency.setValueAtTime(2600, now);
      lp.frequency.exponentialRampToValueAtTime(140, now + 0.42);
      src.connect(lp);
      env(lp, now, 0.004, 0.06, 0.38, 0.55);
      src.start(now);
      src.stop(now + 0.52);
      tone('triangle', 260, 50, now, 0.004, 0.05, 0.3, 0.3);
    },
    level() {
      const now = ctx.currentTime;
      [523, 659, 784, 1047, 1319].forEach((f, i) => {
        tone('triangle', f, f, now + i * 0.07, 0.006, 0.04, 0.1, 0.24);
      });
    },
    badge() {
      const now = ctx.currentTime;
      [660, 880, 1320].forEach((f, i) => {
        tone('sine', f, f, now + i * 0.075, 0.006, 0.045, 0.09, 0.26);
      });
    },
    over() {
      const now = ctx.currentTime;
      [440, 349, 262].forEach((f, i) => {
        tone('sawtooth', f, f * 0.98, now + i * 0.13, 0.006, 0.06, 0.14, 0.2);
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
