"""Build the three image files Patchaga serves.

Only three are needed, because everything the game draws while it is running is
drawn with canvas primitives: the duck, the bugs, the patches and the beam are
all code. What is left is the artwork a browser wants from a page rather than
from a game -- a favicon, an unfurl card, and the Patch My PC mark itself,
which the playfield uses as a watermark.

The source logo is normally supplied flattened onto solid white, and the game
is drawn on near-black, so the white has to be removed properly. Keying out
pure white leaves a pale fringe wherever the original was anti-aliased, which
at watermark size reads as a dirty halo. Instead the white is treated as what
it is, a background the artwork was composited onto, and undone: for a pixel
that is colour ``c`` over white with coverage ``a`` the file holds
``c*a + 255*(1-a)``, so ``a = 1 - min(r,g,b)/255`` recovers the coverage and the
colour follows. Artwork that already has an alpha channel is passed through
untouched, so a cleaned PNG can be handed straight back in.

    python tools/make_patchaga_assets.py "path/to/Patch My PC - Logo.png"

Run it only when the artwork changes. The three PNGs are committed, so a
checkout does not need Pillow to serve the game.
"""

import math
import os
import sys

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = os.path.join(ROOT, "app", "patchaga")

# The playfield colours, so the favicon and the share card sit on the same
# background the game does rather than on a white square nobody asked for.
BG = (11, 16, 38, 255)
GRID = (26, 38, 74, 255)
DUCK = (255, 210, 63, 255)
DUCK_DARK = (224, 168, 0, 255)
BILL = (255, 140, 26, 255)
PATCH = (122, 193, 67, 255)
ROOTKIT = (255, 107, 74, 255)
ROOTKIT_TRIM = (192, 58, 30, 255)


def unmultiply_white(src):
    """Recover colour and coverage from artwork flattened onto white."""
    if src.mode == "RGBA" and src.getextrema()[3][0] < 255:
        return src  # already has real transparency; leave it alone
    src = src.convert("RGB")
    out = Image.new("RGBA", src.size)
    spx = src.load()
    opx = out.load()
    w, h = src.size
    for y in range(h):
        for x in range(w):
            r, g, b = spx[x, y]
            a = 255 - min(r, g, b)
            if a <= 0:
                opx[x, y] = (0, 0, 0, 0)
                continue
            f = 255.0 / a
            opx[x, y] = (
                max(0, min(255, int(round((r - (255 - a)) * f)))),
                max(0, min(255, int(round((g - (255 - a)) * f)))),
                max(0, min(255, int(round((b - (255 - a)) * f)))),
                a,
            )
    return out


def trim(img):
    box = img.getbbox()
    return img.crop(box) if box else img


def square(img):
    """Pad to a square so every later resize keeps the proportions."""
    w, h = img.size
    side = max(w, h)
    out = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    out.paste(img, ((side - w) // 2, (side - h) // 2))
    return out


def draw_duck(draw, cx, cy, s):
    """The player's ship, at ``s`` pixels per game unit, pointing up."""
    draw.ellipse([cx - 9 * s, cy - 3.5 * s, cx + 9 * s, cy + 9.5 * s], fill=DUCK)
    draw.polygon([(cx - 8 * s, cy + 2 * s), (cx - 13 * s, cy - 1 * s),
                  (cx - 8 * s, cy + 6 * s)], fill=DUCK)
    draw.ellipse([cx - 2.7 * s, cy - 10.2 * s, cx + 7.7 * s, cy + 0.2 * s], fill=DUCK)
    draw.polygon([(cx + 0.5 * s, cy - 9 * s), (cx + 4.5 * s, cy - 9 * s),
                  (cx + 2.5 * s, cy - 14 * s)], fill=BILL)
    draw.ellipse([cx - 7.5 * s, cy + 4.6 * s, cx + 7.5 * s, cy + 9.4 * s],
                 fill=DUCK_DARK)
    draw.ellipse([cx + 2.85 * s, cy - 7.35 * s, cx + 5.15 * s, cy - 5.05 * s],
                 fill=(26, 26, 46, 255))


def draw_bug(draw, cx, cy, s):
    """A rootkit, the one worth putting on a poster."""
    for side in (-1, 1):
        draw.ellipse([cx + side * 5 * s - 7 * s, cy - 4.5 * s,
                      cx + side * 5 * s + 7 * s, cy + 3.5 * s],
                     fill=ROOTKIT_TRIM)
    for i in range(3):
        ly = cy + (-3 + i * 3.2) * s
        for side in (-1, 1):
            draw.line([(cx + side * 3 * s, ly),
                       (cx + side * 8.5 * s, ly + 2.4 * s)],
                      fill=ROOTKIT_TRIM, width=max(1, int(1.4 * s)))
    draw.ellipse([cx - 6 * s, cy - 6.5 * s, cx + 6 * s, cy + 8.5 * s], fill=ROOTKIT)
    draw.line([(cx, cy - 4 * s), (cx, cy + 7 * s)],
              fill=ROOTKIT_TRIM, width=max(1, int(s)))
    draw.ellipse([cx - 3.6 * s, cy - 9.6 * s, cx + 3.6 * s, cy - 2.4 * s],
                 fill=ROOTKIT_TRIM)
    for side in (-1, 1):
        draw.ellipse([cx + side * 1.7 * s - 1.3 * s, cy - 7.7 * s,
                      cx + side * 1.7 * s + 1.3 * s, cy - 5.1 * s],
                     fill=(255, 255, 255, 255))
        draw.ellipse([cx + side * 1.7 * s - 0.6 * s, cy - 7 * s,
                      cx + side * 1.7 * s + 0.6 * s, cy - 5.8 * s],
                     fill=(42, 11, 6, 255))


def draw_patch(draw, cx, cy, s):
    """One of the duck's shots: the mark, small enough to read as a bullet."""
    draw.rounded_rectangle([cx - 4 * s, cy - 6 * s, cx + 4 * s, cy + 4 * s],
                           radius=2.5 * s, fill=PATCH)
    draw.rounded_rectangle([cx - 1.6 * s, cy - 3.6 * s, cx + 1.6 * s, cy + 1.6 * s],
                           radius=1.2 * s, fill=(255, 255, 255, 255))


def starfield(draw, w, h, step):
    for i in range(0, w, step):
        draw.line([(i, 0), (i, h)], fill=GRID)
    for i in range(0, h, step):
        draw.line([(0, i), (w, i)], fill=GRID)


def favicon(logo, side=64):
    """The duck alone. At sixteen pixels the logo is a smudge; a duck is not."""
    scale = 4  # drawn large and downsampled, because PIL does not anti-alias
    card = Image.new("RGBA", (side * scale, side * scale), BG)
    draw = ImageDraw.Draw(card)
    draw_duck(draw, side * scale / 2, side * scale * 0.55, side * scale / 30.0)
    return card.resize((side, side), Image.LANCZOS)


def share_card(logo):
    """A 1200x630 card, because that is what every unfurler crops to."""
    scale = 2
    w, h = 1200 * scale, 630 * scale
    card = Image.new("RGBA", (w, h), BG)
    draw = ImageDraw.Draw(card)
    starfield(draw, w, h, 40 * scale)
    draw.rectangle([0, 0, w - 1, 8 * scale], fill=PATCH)

    # A row of bugs above, the duck below, and a patch in flight between them.
    for i in range(5):
        draw_bug(draw, (300 + i * 150) * scale, 250 * scale, 4.0 * scale)
    draw_duck(draw, 600 * scale, 520 * scale, 5.0 * scale)
    for y in (350, 410):
        draw_patch(draw, 600 * scale, y * scale, 3.4 * scale)

    # The name, because a card that is only a picture tells a reader in a chat
    # window nothing about what they are being sent.
    title, tag = _fonts(scale)
    draw.text((60 * scale, 60 * scale), "PATCHAGA", font=title, fill=DUCK)
    draw.text((62 * scale, 128 * scale),
              "Patch the bugs before the bugs patch you.",
              font=tag, fill=(154, 164, 178, 255))

    mark = logo.resize((110 * scale, 110 * scale), Image.LANCZOS)
    card.paste(mark, (w - 160 * scale, 40 * scale), mark)
    return card.resize((1200, 630), Image.LANCZOS)


def _fonts(scale):
    """A title and a caption face, falling back rather than failing.

    The card is generated on whatever machine happens to run the script, so no
    specific font file can be assumed to exist. A card with plainer lettering is
    a great deal better than no card at all.
    """
    candidates = ("segoeuib.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf")
    for name in candidates:
        try:
            return (ImageFont.truetype(name, 56 * scale),
                    ImageFont.truetype(name.replace("b.ttf", ".ttf"), 22 * scale))
        except OSError:
            continue
    try:
        return (ImageFont.load_default(56 * scale),
                ImageFont.load_default(22 * scale))
    except TypeError:
        return (ImageFont.load_default(), ImageFont.load_default())


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    logo = square(trim(unmultiply_white(Image.open(sys.argv[1]))))

    # 256 is plenty. The largest the game ever draws it is a 180 pixel
    # watermark, and a browser downscales far better than it upscales.
    logo.resize((256, 256), Image.LANCZOS).save(os.path.join(OUT, "logo.png"))
    favicon(logo).save(os.path.join(OUT, "favicon.png"))
    share_card(logo).convert("RGB").save(os.path.join(OUT, "share.png"))

    for name in ("logo.png", "favicon.png", "share.png"):
        path = os.path.join(OUT, name)
        print("%-12s %6d bytes  %s" % (name, os.path.getsize(path),
                                       Image.open(path).size))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
