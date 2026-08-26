/**
 * Export every game sprite as a standalone transparent PNG.
 *
 * Two of the three games have no artwork to export. Patchaga and PatchMan draw
 * the duck, the bugs and the vulnerabilities from Canvas2D primitives, for the
 * reasons each renderer gives: an asset would have to be authored once per
 * colour per state, and between them they have three bug palettes, four
 * vulnerability palettes, a frightened palette and a flash frame. So the duck
 * does not exist as a file anywhere. It exists as a sequence of fill calls.
 *
 * Which leaves two ways to get a PNG of it. Redrawing each sprite by hand here
 * would be quicker to write and would start drifting from the game the first
 * time somebody adjusted a colour, and nothing would catch it, because there is
 * no test that a picture still looks like the thing it is a picture of. So this
 * runs the games' real draw functions instead, against a Node canvas rather
 * than a browser one. They are private closures inside createRenderer, so a
 * *copy* of each renderer is patched in the temp directory to hand them back.
 * The repo copy is never modified.
 *
 * Being vector, scale is free: drawing at ~40x produces a genuinely
 * high-resolution sprite rather than an upscaled small one.
 *
 * Flappy Duck is the exception and is treated as one. It really does ship art,
 * in a single atlas built by tools/make_flappy_atlas.py, so its frames are cut
 * from that file and left at native size. Upscaling pixel art is a decision for
 * whoever uses it, not for the tool that extracts it.
 *
 * Authoring tool only. Like tools/make_patchaga_assets.py needing Pillow, it
 * has a dependency the app does not: serving the games still needs nothing but
 * Python, and the exported PNGs are committed so a checkout never runs this.
 *
 *     npm install @napi-rs/canvas
 *     node tools/make_sprite_exports.mjs
 *
 * Run it when a sprite changes. It writes app/_sprites/.
 */
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

let createCanvas;
let loadImage;
try {
  ({ createCanvas, loadImage } = await import('@napi-rs/canvas'));
} catch {
  console.error('This tool needs a Canvas2D implementation to run the games\' own\n'
    + 'drawing code outside a browser:\n\n    npm install @napi-rs/canvas\n');
  process.exit(1);
}

const ROOT = path.resolve(path.dirname(new URL(import.meta.url).pathname.replace(/^\/([A-Z]:)/, '$1')), '..');
const APP = path.join(ROOT, 'app');
const OUT = path.join(APP, '_sprites');
const SCRATCH = fs.mkdtempSync(path.join(os.tmpdir(), 'sprite-export-'));

const SIZE = 900;   // working canvas; every sprite is trimmed out of it
const PAD = 6;

// The renderers size themselves against the page and build offscreen canvases.
// None of that matters for one sprite, but it has to exist or the import dies.
globalThis.window = { devicePixelRatio: 1, addEventListener() {}, removeEventListener() {} };
globalThis.document = {
  createElement: (tag) => (tag === 'canvas' ? createCanvas(16, 16) : { style: {} }),
};
globalThis.Image = class {
  set src(_v) { if (this.onerror) this.onerror(); }
};

/**
 * Copy a game's modules to scratch and expose its private draw functions.
 *
 * The replacement is asserted rather than assumed. If a renderer is
 * restructured so the anchor no longer matches, the failure has to be loud:
 * a silently unpatched module would still import, still run, and produce a
 * directory of correctly named blank images.
 */
function exposeSprites(game, apply) {
  const src = path.join(APP, game);
  const dst = path.join(SCRATCH, game);
  fs.mkdirSync(dst, { recursive: true });
  for (const f of fs.readdirSync(src)) {
    if (f.endsWith('.mjs') || f.endsWith('.json')) {
      fs.copyFileSync(path.join(src, f), path.join(dst, f));
    }
  }
  const rp = path.join(dst, 'render.mjs');
  const before = fs.readFileSync(rp, 'utf8');
  const after = apply(before);
  if (after === before) {
    throw new Error(`could not reach the draw functions in app/${game}/render.mjs -- `
      + 'its return statement has changed shape, so this tool needs updating');
  }
  fs.writeFileSync(rp, after);
  return pathToFileURL(rp).href;
}

/** A canvas the renderer will accept, backed by a context we chose. */
function shimCanvas(ctx, w, h) {
  const rect = { left: 0, top: 0, right: w, bottom: h, width: w, height: h };
  const parent = { clientWidth: w, clientHeight: h, getBoundingClientRect: () => rect };
  return {
    getContext: () => ctx,
    width: w, height: h, style: {}, parentElement: parent,
    getBoundingClientRect: () => rect,
    addEventListener() {}, removeEventListener() {},
  };
}

const written = [];

/** Trim to the drawn pixels, pad, and save. A blank sprite is an error. */
function save(name, canvas, note) {
  const ctx = canvas.getContext('2d');
  const { data } = ctx.getImageData(0, 0, canvas.width, canvas.height);
  let minX = canvas.width, minY = canvas.height, maxX = -1, maxY = -1, ink = 0;
  for (let y = 0; y < canvas.height; y += 1) {
    for (let x = 0; x < canvas.width; x += 1) {
      const a = data[(y * canvas.width + x) * 4 + 3];
      if (a === 0) continue;
      if (a > 24) ink += 1;
      if (x < minX) minX = x;
      if (x > maxX) maxX = x;
      if (y < minY) minY = y;
      if (y > maxY) maxY = y;
    }
  }
  if (maxX < 0) throw new Error(`${name} drew nothing`);

  const w = maxX - minX + 1;
  const h = maxY - minY + 1;
  const out = createCanvas(w + PAD * 2, h + PAD * 2);
  out.getContext('2d').drawImage(canvas, minX, minY, w, h, PAD, PAD, w, h);
  fs.writeFileSync(path.join(OUT, name + '.png'), out.toBuffer('image/png'));
  written.push({ name, w, h, ink: (100 * ink) / (w * h), note });
}

fs.rmSync(OUT, { recursive: true, force: true });
fs.mkdirSync(OUT, { recursive: true });

// --------------------------------------------------------------------------
// Patchaga: the duck, the patch it fires, and the three bug types
// --------------------------------------------------------------------------
{
  const url = exposeSprites('patchaga', (s) => s.replace(
    /( {2}return \{\r?\n)( {4}render,)/,
    '$1    __sprites: { drawDuck, drawPatch, drawBug },\n$2'));
  const { createRenderer } = await import(url);
  const cv = createCanvas(SIZE, SIZE);
  const ctx = cv.getContext('2d');
  const s = createRenderer(shimCanvas(ctx, SIZE, SIZE), null).__sprites;

  const shot = (name, half, draw) => {
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, SIZE, SIZE);
    const k = (SIZE * 0.42) / half;
    ctx.setTransform(k, 0, 0, k, SIZE / 2, SIZE / 2);
    draw();
    save(name, cv, 'vector, redrawn at scale');
  };

  shot('patchaga-duck', 15, () => s.drawDuck(0, 0, 0, 1, 1));
  shot('patchaga-patch', 8, () => s.drawPatch(0, 0));
  // Wings mid-beat: fully down reads as a shell, fully up as a moth.
  ['drone', 'weevil', 'rootkit'].forEach((n, kind) => {
    shot('patchaga-bug-' + n, 13, () => s.drawBug(0, 0, kind, 0.9, false, 1));
  });
}

// --------------------------------------------------------------------------
// PatchMan: the four vulnerabilities, their frightened states, and PatchMan
// --------------------------------------------------------------------------
{
  const url = exposeSprites('patchman', (s) => s.replace(
    / {2}return \{ resize, render, hitsMute, drawLoading \};/,
    '  return { resize, render, hitsMute, drawLoading, __sprites: {\n'
    + '    drawBeetle, beetleEyes, beetleCrosses, vulnEyes, drawPac,\n'
    + '    SHELL_MARKS, shade, CONFIG, px, py, STATE, T } };'));
  const { createRenderer } = await import(url);
  const cv = createCanvas(SIZE, SIZE);
  const ctx = cv.getContext('2d');
  const s = createRenderer(shimCanvas(ctx, SIZE, SIZE), null).__sprites;
  const { CONFIG, T } = s;
  const r = T * 0.44;

  // The palette drawVulns builds, so a colour change in the game reaches here.
  const palette = (i, fright, flashing) => {
    let fill = CONFIG.vulns[i].color;
    let edge = 'rgba(255, 255, 255, 0.28)';
    let dark = 'rgba(9, 12, 26, 0.85)';
    if (fright) {
      fill = flashing ? CONFIG.frightFlashColor : CONFIG.frightColor;
      edge = flashing ? '#ffffff' : '#d4f2a8';
      dark = flashing ? '#1d3f0c' : 'rgba(9, 26, 12, 0.85)';
    }
    return {
      dark,
      opts: {
        fill, edge, head: dark, leg: s.shade(fill, 0.42),
        mark: dark, marks: s.SHELL_MARKS[i], phase: i,
      },
    };
  };

  const beetle = (name, i, fright, flashing) => {
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, SIZE, SIZE);
    const k = (SIZE * 0.40) / (r * 1.8);
    ctx.setTransform(k, 0, 0, k, SIZE / 2, SIZE / 2);
    const { opts, dark } = palette(i, fright, flashing);
    s.drawBeetle(ctx, r, 0, 0, opts);
    if (fright) s.beetleCrosses(ctx, r, dark);
    else s.beetleEyes(ctx, r, '#131a2c');
    save(name, cv, 'vector, redrawn at scale');
  };

  CONFIG.vulns.forEach((v, i) => beetle('patchman-vuln-' + v.key, i, false, false));
  beetle('patchman-vuln-frightened', 0, true, false);
  beetle('patchman-vuln-frightened-flash', 0, true, true);

  // A patched vulnerability, reduced to the eyes still walking home.
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.clearRect(0, 0, SIZE, SIZE);
  {
    const k = (SIZE * 0.40) / (r * 1.4);
    ctx.setTransform(k, 0, 0, k, SIZE / 2, SIZE / 2);
    s.vulnEyes(ctx, 0, 0, r, 3, '#36a2eb');
    save('patchman-vuln-eyes', cv, 'vector, redrawn at scale');
  }

  // PatchMan himself. drawPac reads a simulation, so it gets the smallest one
  // that is a valid frame: alive, playing, facing right, not mid-death. The
  // chomp is sampled part-open, because a closed mouth is a disc and a fully
  // open one is a wedge, and neither reads as the character.
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.clearRect(0, 0, SIZE, SIZE);
  {
    const k = (SIZE * 0.42) / (T * 0.42);
    ctx.setTransform(k, 0, 0, k, SIZE / 2 - s.px(0) * k, SIZE / 2 - s.py(0) * k);
    s.drawPac(
      { pac: { prevX: 0, x: 0, prevY: 0, y: 0, dir: 3 }, state: s.STATE.PLAYING, deathTicks: 0 },
      1,
      CONFIG.chompFrameMs * Math.round(CONFIG.chompFrames * 0.72),
      false,
    );
    save('patchman-patchman', cv, 'vector, redrawn at scale');
  }
}

// --------------------------------------------------------------------------
// Flappy Duck: cut from the atlas, because here the art is real
// --------------------------------------------------------------------------
{
  const dir = path.join(APP, 'flappy');
  const meta = JSON.parse(fs.readFileSync(path.join(dir, 'atlas.json'), 'utf8'));
  const atlas = await loadImage(path.join(dir, meta.image));
  for (const [frame, f] of Object.entries(meta.frames).sort()) {
    const cv = createCanvas(f.w, f.h);
    cv.getContext('2d').drawImage(atlas, f.x, f.y, f.w, f.h, 0, 0, f.w, f.h);
    save('flappy-' + frame, cv, 'pixel art, cut from atlas.png');
  }
}

// --------------------------------------------------------------------------
// An index, so the set can be checked at a glance rather than file by file
// --------------------------------------------------------------------------
written.sort((a, b) => a.name.localeCompare(b.name));
{
  const CELL = 190;
  const LABEL = 26;
  const COLS = 5;
  const rows = Math.ceil(written.length / COLS);
  const sheet = createCanvas(COLS * CELL, rows * (CELL + LABEL) + 10);
  const g = sheet.getContext('2d');
  g.fillStyle = '#16213e';
  g.fillRect(0, 0, sheet.width, sheet.height);
  g.textAlign = 'center';
  g.font = '13px Segoe UI, Arial, sans-serif';

  for (let i = 0; i < written.length; i += 1) {
    const { name } = written[i];
    const img = await loadImage(path.join(OUT, name + '.png'));
    const inner = CELL - 22;
    let k = Math.min(inner / img.width, inner / img.height);
    // Pixel art only ever scales by whole numbers, or the grid goes soft.
    if (name.startsWith('flappy-') && k >= 1) k = Math.floor(k);
    const w = Math.max(1, Math.round(img.width * k));
    const h = Math.max(1, Math.round(img.height * k));
    const cx = (i % COLS) * CELL;
    const cy = Math.floor(i / COLS) * (CELL + LABEL);
    g.imageSmoothingEnabled = !name.startsWith('flappy-');
    g.drawImage(img, cx + (CELL - w) / 2, cy + (CELL - h) / 2, w, h);
    g.fillStyle = '#bec8d7';
    g.fillText(name, cx + CELL / 2, cy + CELL + 16);
  }
  fs.writeFileSync(path.join(OUT, '_index.png'), sheet.toBuffer('image/png'));
}

fs.rmSync(SCRATCH, { recursive: true, force: true });

// ---------------------------------------------------------------------------
// A zip of the whole set, for handing to somebody who just wants the pictures.
//
// Built here rather than by hand, because a zip made once alongside the PNGs is
// wrong the moment a sprite changes and nothing would ever say so. Written with
// the standard library: entries are stored rather than deflated, since PNG is
// already compressed and squeezing it again saves about one percent. Timestamps
// are pinned to the start of the DOS epoch, so re-running with no sprite changes
// produces a byte-identical file and git reports nothing to commit.
// ---------------------------------------------------------------------------

const CRC_TABLE = (() => {
  const t = new Int32Array(256);
  for (let n = 0; n < 256; n += 1) {
    let c = n;
    for (let k = 0; k < 8; k += 1) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    t[n] = c;
  }
  return t;
})();

function crc32(buf) {
  let c = -1;
  for (let i = 0; i < buf.length; i += 1) c = CRC_TABLE[(c ^ buf[i]) & 0xff] ^ (c >>> 8);
  return (c ^ -1) >>> 0;
}

function writeZip(zipPath, prefix, names) {
  const DOS_TIME = 0;
  const DOS_DATE = 33; // 1980-01-01, the earliest a DOS timestamp can express.
  const parts = [];
  const central = [];
  let offset = 0;

  for (const name of names) {
    const body = fs.readFileSync(path.join(OUT, name));
    const entry = Buffer.from(prefix + name, 'utf8');
    const sum = crc32(body);

    const local = Buffer.alloc(30);
    local.writeUInt32LE(0x04034b50, 0);
    local.writeUInt16LE(20, 4);
    local.writeUInt16LE(DOS_TIME, 10);
    local.writeUInt16LE(DOS_DATE, 12);
    local.writeUInt32LE(sum, 14);
    local.writeUInt32LE(body.length, 18);
    local.writeUInt32LE(body.length, 22);
    local.writeUInt16LE(entry.length, 26);
    parts.push(local, entry, body);

    const dir = Buffer.alloc(46);
    dir.writeUInt32LE(0x02014b50, 0);
    dir.writeUInt16LE(20, 4);
    dir.writeUInt16LE(20, 6);
    dir.writeUInt16LE(DOS_TIME, 12);
    dir.writeUInt16LE(DOS_DATE, 14);
    dir.writeUInt32LE(sum, 16);
    dir.writeUInt32LE(body.length, 20);
    dir.writeUInt32LE(body.length, 24);
    dir.writeUInt16LE(entry.length, 28);
    dir.writeUInt32LE(offset, 42);
    central.push(dir, entry);

    offset += local.length + entry.length + body.length;
  }

  const directory = Buffer.concat(central);
  const end = Buffer.alloc(22);
  end.writeUInt32LE(0x06054b50, 0);
  end.writeUInt16LE(names.length, 8);
  end.writeUInt16LE(names.length, 10);
  end.writeUInt32LE(directory.length, 12);
  end.writeUInt32LE(offset, 16);

  fs.writeFileSync(zipPath, Buffer.concat([...parts, directory, end]));
}

const zipNames = [...written.map((s) => `${s.name}.png`), '_index.png'].sort();
const ZIP = path.join(APP, '_sprites.zip');
writeZip(ZIP, '_sprites/', zipNames);

console.log('%s  %s  %s  %s', 'sprite'.padEnd(32), 'size'.padStart(11), 'ink'.padStart(6), 'source');
console.log('-'.repeat(78));
for (const s of written) {
  console.log('%s  %s  %s  %s',
    s.name.padEnd(32),
    `${s.w} x ${s.h}`.padStart(11),
    `${s.ink.toFixed(1)}%`.padStart(6),
    s.note);
}
console.log('\n%d sprites and an index written to app/_sprites/, zipped to app/_sprites.zip',
  written.length);
