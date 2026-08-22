"""Turn the supplied Patch My PC logo into the assets PatchMan needs.

The logo arrives as a large PNG drawn on solid white. The game is drawn on a
near-black board, so the white has to go, and it has to go properly: simply
keying out pure white leaves a pale fringe everywhere the original was
anti-aliased, which at pellet size reads as a dirty halo rather than a logo.

Instead the white is treated as what it is, a background the artwork was
composited onto, and undone. For a pixel that is colour `c` over white with
coverage `a`, the file holds `c*a + 255*(1-a)`. The most saturated channel is
the one that travelled furthest from white, so `a = 1 - min(r,g,b)/255`
recovers the coverage, and the original colour follows. Edges then fade to
transparent instead of to white, and the mark stays clean down to sixteen
pixels across.

The white inside the monitor goes transparent too, which is intended: on a dark
board the screen reads as an unlit screen, and the green check still carries the
shape.

    python tools/make_patchman_assets.py "path/to/Patch My PC - Logo.png"
"""

import os
import sys

from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = os.path.join(ROOT, "app", "patchman")

# Board colours, so the share card and favicon sit on the same background the
# game does rather than on a white square nobody asked for.
BG = (14, 16, 34, 255)
ACCENT = (78, 204, 163, 255)


def unmultiply_white(src):
    """Recover colour and coverage from artwork flattened onto white."""
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
    """Pad to a square so every later resize keeps the circle circular."""
    w, h = img.size
    side = max(w, h)
    out = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    out.paste(img, ((side - w) // 2, (side - h) // 2))
    return out


def on_board(img, side, inset):
    """The logo centred on a board-coloured tile, for the icon and share card."""
    card = Image.new("RGBA", (side, side), BG)
    inner = side - inset * 2
    card.paste(img.resize((inner, inner), Image.LANCZOS), (inset, inset), img.resize((inner, inner), Image.LANCZOS))
    return card


def share_card(img):
    """A 1200x630 card, because that is what every unfurler crops to."""
    card = Image.new("RGBA", (1200, 630), BG)
    draw = ImageDraw.Draw(card)
    for i in range(0, 1200, 40):          # a faint circuit grid, as on the board
        draw.line([(i, 0), (i, 630)], fill=(30, 44, 74, 255))
    for i in range(0, 630, 40):
        draw.line([(0, i), (1200, i)], fill=(30, 44, 74, 255))
    draw.rectangle([0, 0, 1199, 8], fill=ACCENT)
    mark = img.resize((360, 360), Image.LANCZOS)
    card.paste(mark, (420, 135), mark)
    return card


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    src = Image.open(sys.argv[1])
    logo = square(trim(unmultiply_white(src)))

    # 256 is plenty: the largest the game ever draws it is the ready screen at
    # about 96 CSS pixels, and a browser downscales far better than it upscales.
    logo.resize((256, 256), Image.LANCZOS).save(os.path.join(OUT, "logo.png"))
    on_board(logo, 64, 6).save(os.path.join(OUT, "favicon.png"))
    share_card(logo).convert("RGB").save(os.path.join(OUT, "share.png"))

    for name in ("logo.png", "favicon.png", "share.png"):
        path = os.path.join(OUT, name)
        print("%-12s %6d bytes  %s" % (name, os.path.getsize(path),
                                       Image.open(path).size))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
