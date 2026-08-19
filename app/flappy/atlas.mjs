/**
 * Sprite atlas loading and drawing.
 *
 * One image plus a frame map, which keeps the request count at one and avoids
 * the seams you get when neighbouring sprites are sampled at a fractional
 * scale. Nothing here knows about game rules.
 */

export async function loadAtlas(base) {
  const res = await fetch(base + 'atlas.json', { cache: 'no-cache' });
  if (!res.ok) throw new Error('atlas.json ' + res.status);
  const meta = await res.json();
  const image = await new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error('could not load ' + meta.image));
    img.src = base + meta.image;
  });
  return new Atlas(image, meta);
}

class Atlas {
  constructor(image, meta) {
    this.image = image;
    this.meta = meta;
    this.frames = meta.frames;
    this.font = meta.font;
    this.digitCell = meta.digits;
    this.tile = meta.tile;
    this._tints = new Map();
  }

  frame(name) {
    const f = this.frames[name];
    if (!f) throw new Error('no atlas frame named ' + name);
    return f;
  }

  /** Draw a whole frame at its native size. */
  draw(ctx, name, x, y) {
    const f = this.frame(name);
    ctx.drawImage(this.image, f.x, f.y, f.w, f.h, x, y, f.w, f.h);
  }

  /** Draw a horizontal slice of a frame, used for the tiling strips. */
  drawStrip(ctx, name, offsetX, x, y, w) {
    const f = this.frame(name);
    ctx.drawImage(this.image, f.x + offsetX, f.y, w, f.h, x, y, w, f.h);
  }

  /**
   * The font is authored in white so it can be tinted.
   *
   * Tinting per glyph every frame would mean a composite operation per
   * character, so each colour is baked once into its own offscreen strip and
   * reused. There are only a handful of colours in the whole game.
   */
  tinted(color) {
    let strip = this._tints.get(color);
    if (strip) return strip;
    const f = this.frame('font');
    strip = document.createElement('canvas');
    strip.width = f.w;
    strip.height = f.h;
    const c = strip.getContext('2d');
    c.imageSmoothingEnabled = false;
    c.drawImage(this.image, f.x, f.y, f.w, f.h, 0, 0, f.w, f.h);
    c.globalCompositeOperation = 'source-in';
    c.fillStyle = color;
    c.fillRect(0, 0, f.w, f.h);
    this._tints.set(color, strip);
    return strip;
  }

  textWidth(text, scale = 1) {
    const { cellW, tracking } = this.font;
    const n = text.length;
    if (!n) return 0;
    return (n * (cellW + tracking) - tracking) * scale;
  }

  /**
   * Draw uppercase pixel text. Anything without a glyph is skipped rather
   * than substituted, so a missing character shows up as a hole in testing
   * instead of a wrong letter in production.
   */
  text(ctx, text, x, y, opts = {}) {
    const scale = opts.scale || 1;
    const color = opts.color || '#ffffff';
    const { chars, cellW, cellH, tracking } = this.font;
    const upper = String(text).toUpperCase();
    let left = x;
    if (opts.align === 'center') left = Math.round(x - this.textWidth(upper, scale) / 2);
    else if (opts.align === 'right') left = Math.round(x - this.textWidth(upper, scale));

    if (opts.shadow) {
      this.text(ctx, upper, left + scale, y + scale,
        { scale, color: opts.shadow, align: 'left' });
    }

    const strip = this.tinted(color);
    for (let i = 0; i < upper.length; i += 1) {
      const idx = chars.indexOf(upper[i]);
      if (idx < 0) continue;
      ctx.drawImage(strip, idx * cellW, 0, cellW, cellH,
        left + i * (cellW + tracking) * scale, y, cellW * scale, cellH * scale);
    }
    return left;
  }

  /** The big score numerals. They carry their own outline, so no tinting. */
  number(ctx, value, x, y, opts = {}) {
    const scale = opts.scale || 1;
    const { cellW, cellH } = this.digitCell;
    const f = this.frame('digits');
    const str = String(Math.max(0, Math.round(value)));
    const width = str.length * cellW * scale;
    let left = x;
    if (opts.align === 'center') left = Math.round(x - width / 2);
    else if (opts.align === 'right') left = Math.round(x - width);
    for (let i = 0; i < str.length; i += 1) {
      const d = str.charCodeAt(i) - 48;
      ctx.drawImage(this.image, f.x + d * cellW, f.y, cellW, cellH,
        left + i * cellW * scale, y, cellW * scale, cellH * scale);
    }
    return width;
  }
}
