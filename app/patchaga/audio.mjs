/**
 * Sound, synthesised rather than loaded.
 *
 * Every effect is a short envelope over an oscillator or a burst of noise, so
 * the game ships no audio files and the whole thing stays a few kilobytes. It
 * also means nothing has to be decoded before the first shot is fired, which
 * matters because the first shot usually happens within a second of the page
 * being opened.
 *
 * Browsers refuse to start audio until the user has interacted with the page,
 * so the context is created on demand and every call is a no-op until then.
 * Muted is the default: a game that makes noise the instant it loads is a game
 * people close in an open-plan office.
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
    // Autoplay policy suspends a context created before the first gesture.
    if (ctx.state === 'suspended') ctx.resume().catch(() => {});
    return ctx;
  }

  /** One oscillator with a linear pitch sweep and a percussive envelope. */
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

  /**
   * Filtered white noise, for the things that are impacts rather than notes.
   *
   * The buffer is rebuilt per call rather than cached because it is a few
   * thousand samples and caching it would tie the sound to a context that may
   * since have been closed.
   */
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

  /** A short run of notes, for the moments worth marking. */
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
    // Firing is the sound heard most, so it is the quietest and shortest thing
    // here. A satisfying pew at full volume becomes unbearable at six a second.
    fire: () => tone('square', 900, 1700, 0.05, 0.30),
    pop: () => { noise(0.13, 2600, 0.5); tone('square', 420, 120, 0.11, 0.34); },
    bugfire: () => tone('sawtooth', 300, 190, 0.09, 0.20),
    dive: () => tone('sawtooth', 620, 240, 0.20, 0.22),
    beam: () => tone('sine', 180, 700, 0.42, 0.26),
    fork: () => arp([740, 560, 420, 300], 0.075, 'sawtooth', 0.42),
    freed: () => arp([420, 560, 740], 0.06, 'square', 0.45),
    merge: () => arp([523, 659, 784, 1047], 0.07, 'square', 0.5),
    unmerge: () => tone('sawtooth', 500, 200, 0.18, 0.3),
    die: () => { noise(0.34, 1100, 0.6); tone('sawtooth', 380, 60, 0.42, 0.4); },
    respawn: () => arp([392, 523], 0.07, 'square', 0.32),
    clear: () => arp([523, 659, 784, 1047, 1319], 0.085, 'square', 0.5),
    perfect: () => arp([659, 784, 1047, 1319, 1568, 2093], 0.075, 'square', 0.55),
    extralife: () => arp([784, 1047, 1319], 0.06, 'sine', 0.5),
    sweep: () => tone('sine', 240, 520, 0.22, 0.24),
    enter: () => tone('sine', 720, 900, 0.035, 0.10),
    gameover: () => arp([392, 330, 262, 196], 0.16, 'sawtooth', 0.45),
  };

  return {
    /**
     * Create the context now that a gesture has happened.
     *
     * Called from the input handlers rather than on load, because a context
     * created before the first interaction starts suspended and the first few
     * effects are silently swallowed.
     */
    unlock() { ensure(); },
    /** Play one named effect. Unknown names are ignored rather than thrown. */
    play(name) {
      const fn = effects[name];
      if (fn && !muted) {
        try { fn(); } catch (err) { /* a failed sound must never stop the game */ }
      }
    },
    /** Flips mute and returns the new state, so the caller can persist it. */
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
