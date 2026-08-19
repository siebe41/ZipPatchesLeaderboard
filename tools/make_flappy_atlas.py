"""Generate the Flappy Duck sprite atlas.

Everything here is original art. Nothing is traced, sampled, or derived from
Flappy Bird's sprite sheet, which is protected. The shapes are composed from
ellipses, rectangles and hand-authored pixel grids, and the whole pipeline is
pure standard library, so the repo keeps its no-build promise: this script is a
one-time authoring tool that is never deployed and never imported by the app.

Run it from the repo root:

    python tools/make_flappy_atlas.py

It writes app/flappy/atlas.png, app/flappy/atlas.json, app/flappy/favicon.png
and app/flappy/share.png. Those generated files are committed, so a deploy is
still upload the files and restart.

There is deliberately no 2x atlas. The renderer draws with
imageSmoothingEnabled false at an integer device scale, so a mechanically
doubled asset would produce pixel-identical output while costing an extra
request, and a genuinely redrawn 2x would mean two art styles to keep in sync
for one toy.
"""

import json
import os
import struct
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(os.path.dirname(HERE), "app", "flappy")

# --------------------------------------------------------------------------- #
# Palette
# --------------------------------------------------------------------------- #

INK = (0x2A, 0x21, 0x18, 255)          # one outline colour everywhere
CLEAR = (0, 0, 0, 0)

DUCK_BASE = (0xFF, 0xD2, 0x3F, 255)
DUCK_LIGHT = (0xFF, 0xE9, 0x8A, 255)
DUCK_DARK = (0xE3, 0xA8, 0x1F, 255)
BEAK = (0xFF, 0x8C, 0x1A, 255)
BEAK_DARK = (0xD9, 0x6A, 0x00, 255)
WING = (0xF2, 0xB7, 0x05, 255)
WING_LIGHT = (0xFF, 0xE0, 0x7A, 255)
WING_DARK = (0xC9, 0x8F, 0x05, 255)
WHITE = (0xFF, 0xFF, 0xFF, 255)

# Obstacles read as a column of application tiles waiting on a patch.
TILE_FACE = (0x4E, 0x63, 0x83, 255)
TILE_LIGHT = (0x6B, 0x85, 0xAC, 255)
TILE_DARK = (0x33, 0x44, 0x5E, 255)
TILE_EDGE = (0x24, 0x30, 0x4A, 255)
TILE_GLYPH = (0x9F, 0xB6, 0xD6, 255)
PENDING = (0xE9, 0xA1, 0x3B, 255)      # the amber "still needs a patch" pip
PATCHED = (0x4E, 0xCC, 0xA3, 255)

# Sky and skyline reuse the leaderboard's own gradient so the two pages look
# like they came from the same place.
SKY_TOP = (0x1A, 0x1A, 0x2E, 255)
SKY_BOTTOM = (0x0F, 0x34, 0x60, 255)
SKYLINE_FAR = (0x1B, 0x2C, 0x4F, 255)
SKYLINE_NEAR = (0x22, 0x37, 0x60, 255)
WINDOW_LIT = (0x36, 0xA2, 0xEB, 255)
WINDOW_DIM = (0x2A, 0x4A, 0x78, 255)
STAR = (0xBF, 0xD8, 0xF5, 255)

GROUND_TOP = (0x3C, 0x50, 0x72, 255)
GROUND_FACE = (0x2B, 0x3A, 0x55, 255)
GROUND_DEEP = (0x20, 0x2C, 0x44, 255)
HAZARD_A = (0xE9, 0xA1, 0x3B, 255)
HAZARD_B = (0x2B, 0x3A, 0x55, 255)

BRONZE = (0xC8, 0x7B, 0x3E, 255)
BRONZE_LIGHT = (0xE8, 0xA8, 0x6E, 255)
SILVER = (0xB6, 0xC2, 0xD1, 255)
SILVER_LIGHT = (0xE4, 0xEC, 0xF5, 255)
GOLD = (0xE8, 0xB9, 0x2E, 255)
GOLD_LIGHT = (0xFF, 0xE9, 0x8A, 255)

UI_TEXT = (0xEE, 0xEE, 0xEE, 255)
MUTE_SLASH = (0xE9, 0x45, 0x60, 255)


# --------------------------------------------------------------------------- #
# Tiny pixel canvas
# --------------------------------------------------------------------------- #


class Img:
    def __init__(self, w, h, fill=CLEAR):
        self.w = w
        self.h = h
        self.px = [fill] * (w * h)

    def get(self, x, y):
        if 0 <= x < self.w and 0 <= y < self.h:
            return self.px[y * self.w + x]
        return CLEAR

    def set(self, x, y, c):
        if 0 <= x < self.w and 0 <= y < self.h:
            self.px[y * self.w + x] = c

    def rect(self, x, y, w, h, c):
        for yy in range(y, y + h):
            for xx in range(x, x + w):
                self.set(xx, yy, c)

    def ellipse(self, cx, cy, rx, ry, c):
        """Filled ellipse. Centres may be half-integers."""
        if rx <= 0 or ry <= 0:
            return
        for yy in range(int(cy - ry) - 1, int(cy + ry) + 2):
            for xx in range(int(cx - rx) - 1, int(cx + rx) + 2):
                dx = (xx + 0.5 - cx) / rx
                dy = (yy + 0.5 - cy) / ry
                if dx * dx + dy * dy <= 1.0:
                    self.set(xx, yy, c)

    def blit(self, other, x, y):
        for yy in range(other.h):
            for xx in range(other.w):
                c = other.get(xx, yy)
                if c[3]:
                    self.set(x + xx, y + yy, c)

    def outline(self, color=INK, diagonal=True):
        """Wrap the silhouette in a one pixel border.

        Running this as a pass over the finished shape is what keeps the duck,
        the tiles and the badges looking like one artist drew them.
        """
        offsets = [(-1, 0), (1, 0), (0, -1), (0, 1)]
        if diagonal:
            offsets += [(-1, -1), (1, -1), (-1, 1), (1, 1)]
        targets = []
        for y in range(self.h):
            for x in range(self.w):
                if self.get(x, y)[3]:
                    continue
                for dx, dy in offsets:
                    if self.get(x + dx, y + dy)[3]:
                        targets.append((x, y))
                        break
        for x, y in targets:
            self.set(x, y, color)

    def grid(self, rows, key, x=0, y=0):
        """Stamp hand-authored pixel art. A dot is transparent."""
        for yy, row in enumerate(rows):
            for xx, ch in enumerate(row):
                if ch == "." or ch not in key:
                    continue
                self.set(x + xx, y + yy, key[ch])

    def to_bytes(self):
        out = bytearray()
        for c in self.px:
            out += bytes(c)
        return out


def write_png(path, img):
    raw = bytearray()
    data = img.to_bytes()
    stride = img.w * 4
    for y in range(img.h):
        raw.append(0)  # filter type none; these images are tiny and mostly flat
        raw += data[y * stride:(y + 1) * stride]

    def chunk(tag, payload):
        return (struct.pack(">I", len(payload)) + tag + payload
                + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF))

    header = struct.pack(">IIBBBBB", img.w, img.h, 8, 6, 0, 0, 0)
    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", header)
           + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
           + chunk(b"IEND", b""))
    with open(path, "wb") as fh:
        fh.write(png)


def scaled(img, factor):
    """Nearest neighbour upscale, used only for the share image and favicon."""
    out = Img(img.w * factor, img.h * factor)
    for y in range(out.h):
        for x in range(out.w):
            out.set(x, y, img.get(x // factor, y // factor))
    return out


def lerp_color(a, b, t):
    return (round(a[0] + (b[0] - a[0]) * t),
            round(a[1] + (b[1] - a[1]) * t),
            round(a[2] + (b[2] - a[2]) * t),
            255)


def rng(seed):
    """Tiny LCG so the generated art is byte-identical on every run."""
    state = seed & 0xFFFFFFFF

    def nxt(n):
        nonlocal state
        state = (1664525 * state + 1013904223) & 0xFFFFFFFF
        return (state >> 8) % n
    return nxt


# --------------------------------------------------------------------------- #
# Duck
# --------------------------------------------------------------------------- #

DUCK_W, DUCK_H = 34, 24

# Wing pose per frame: centre x, centre y, radius x, radius y.
WING_POSES = [
    (13.0, 11.5, 6.0, 3.2),   # up
    (13.0, 14.5, 6.5, 3.0),   # mid
    (13.0, 17.0, 5.6, 3.6),   # down
]


def make_duck(frame):
    img = Img(DUCK_W, DUCK_H)

    # Body and head are one silhouette so the outline pass wraps the union
    # instead of drawing a seam where they overlap.
    img.ellipse(14.5, 15.0, 12.0, 6.6, DUCK_BASE)
    img.ellipse(23.5, 8.5, 7.0, 7.0, DUCK_BASE)

    # Tail: a short stepped wedge off the back so it reads as a flick.
    for i in range(4):
        img.rect(2 + i, 9 + i, 6 - i, 3, DUCK_BASE)

    # Belly shade and two highlights.
    img.ellipse(14.5, 19.5, 9.5, 2.6, DUCK_DARK)
    img.ellipse(10.0, 11.0, 6.0, 1.8, DUCK_LIGHT)
    img.ellipse(20.5, 4.5, 4.0, 2.2, DUCK_LIGHT)

    # Beak: flat and wide, which is the whole rubber duck read.
    img.rect(29, 7, 4, 1, BEAK)
    img.rect(28, 8, 6, 4, BEAK)
    img.rect(28, 12, 5, 2, BEAK_DARK)

    # Eye.
    img.rect(23, 4, 4, 5, WHITE)
    img.rect(25, 5, 2, 3, INK)

    # Wing: its own outline first, then the fill inset by one, so it stays
    # legible against the body instead of melting into it.
    cx, cy, rx, ry = WING_POSES[frame]
    img.ellipse(cx, cy, rx + 1, ry + 1, INK)
    img.ellipse(cx, cy, rx, ry, WING)
    img.ellipse(cx - 0.5, cy - 0.8, rx * 0.62, ry * 0.5, WING_LIGHT)
    img.ellipse(cx + 1.5, cy + 1.0, rx * 0.5, ry * 0.4, WING_DARK)

    img.outline()
    return img


# --------------------------------------------------------------------------- #
# Obstacle: a column of application tiles waiting on a patch
# --------------------------------------------------------------------------- #

TILE_W = 52
CAP_W = 60
BODY_H = 24
CAP_H = 26
CAP_LIP = 8

# A 9x9 application icon stamped into each segment.
APP_GLYPH = [
    ".#######.",
    "#.......#",
    "#..###..#",
    "#.#...#.#",
    "#.#...#.#",
    "#..###..#",
    "#.......#",
    "#.......#",
    ".#######.",
]


def tile_shading(img, x, y, w, h):
    img.rect(x, y, w, h, TILE_FACE)
    img.rect(x, y, w, 1, TILE_LIGHT)
    img.rect(x, y, 1, h, TILE_LIGHT)
    img.rect(x, y + h - 1, w, 1, TILE_DARK)
    img.rect(x + w - 1, y, 1, h, TILE_DARK)


def make_tile_body():
    """One vertically tileable segment of the column."""
    img = Img(TILE_W, BODY_H)
    tile_shading(img, 0, 0, TILE_W, BODY_H)
    img.rect(0, 0, TILE_W, 1, TILE_EDGE)
    img.rect(0, BODY_H - 1, TILE_W, 1, TILE_EDGE)
    img.rect(0, 1, 2, BODY_H - 2, TILE_EDGE)
    img.rect(TILE_W - 2, 1, 2, BODY_H - 2, TILE_EDGE)

    # App icon on the left, a stub of a version string in the middle, and the
    # amber pip on the right that says this one is still waiting.
    img.grid(APP_GLYPH, {"#": TILE_GLYPH}, 6, 7)
    img.rect(19, 9, 8, 2, TILE_LIGHT)
    img.rect(19, 13, 5, 2, TILE_LIGHT)
    img.rect(31, 8, 5, 5, PENDING)
    img.rect(31, 8, 5, 1, GOLD_LIGHT)
    img.rect(40, 9, 6, 2, TILE_LIGHT)
    img.rect(40, 13, 6, 2, TILE_LIGHT)
    return img


def make_tile_cap(pointing_up):
    """The lip at the mouth of the maintenance window."""
    img = Img(CAP_W, CAP_H)
    body_h = CAP_H - CAP_LIP
    body_y = CAP_LIP if pointing_up else 0

    tile_shading(img, 4, body_y, TILE_W, body_h)
    img.rect(4, body_y, TILE_W, 1, TILE_EDGE)
    img.rect(4, body_y + body_h - 1, TILE_W, 1, TILE_EDGE)
    img.rect(4, body_y, 2, body_h, TILE_EDGE)
    img.rect(TILE_W + 2, body_y, 2, body_h, TILE_EDGE)

    lip_y = 0 if pointing_up else body_h
    tile_shading(img, 0, lip_y, CAP_W, CAP_LIP)
    img.rect(0, lip_y, CAP_W, 1, TILE_EDGE)
    img.rect(0, lip_y + CAP_LIP - 1, CAP_W, 1, TILE_EDGE)
    img.rect(0, lip_y, 2, CAP_LIP, TILE_EDGE)
    img.rect(CAP_W - 2, lip_y, 2, CAP_LIP, TILE_EDGE)

    # A green band on the lip marks the edge of the window you are aiming for.
    band = lip_y + 2 if pointing_up else lip_y + CAP_LIP - 4
    img.rect(4, band, CAP_W - 8, 2, PATCHED)

    img.grid(APP_GLYPH, {"#": TILE_GLYPH}, 9, body_y + 5)
    img.rect(24, body_y + 6, 20, 2, TILE_LIGHT)
    img.rect(24, body_y + 10, 13, 2, TILE_LIGHT)
    img.rect(46, body_y + 6, 6, 6, PENDING)
    return img


# --------------------------------------------------------------------------- #
# Background and ground
# --------------------------------------------------------------------------- #

BG_W, BG_H = 288, 448
GROUND_W, GROUND_H = 288, 64


def make_background():
    img = Img(BG_W, BG_H)
    for y in range(BG_H):
        img.rect(0, y, BG_W, 1, lerp_color(SKY_TOP, SKY_BOTTOM, y / (BG_H - 1)))

    nxt = rng(0x5EED)
    for _ in range(70):
        img.set(nxt(BG_W), nxt(200), STAR)

    # Two bands of server racks. Nothing crosses x = 0, so the strip repeats
    # horizontally without a torn building at the seam.
    for color, base_y, min_h, span in [
        (SKYLINE_FAR, 300, 40, 56),
        (SKYLINE_NEAR, 344, 46, 62),
    ]:
        x = 2
        while x < BG_W - 12:
            w = 18 + nxt(16)
            if x + w > BG_W - 2:
                w = BG_W - 2 - x
            if w < 10:
                break
            h = min_h + nxt(span)
            img.rect(x, base_y - h, w, h + (BG_H - base_y), color)
            for wy in range(base_y - h + 4, base_y - 4, 6):
                for wx in range(x + 3, x + w - 3, 5):
                    img.rect(wx, wy, 3, 3,
                             WINDOW_LIT if nxt(5) == 0 else WINDOW_DIM)
            x += w + 3 + nxt(6)

    # A soft glow where the racks meet the ground.
    for i in range(18):
        t = (1.0 - i / 18.0) * 0.35
        y = BG_H - 1 - i
        for x in range(BG_W):
            img.set(x, y, lerp_color(img.get(x, y), SKYLINE_NEAR, t))
    return img


def make_ground():
    img = Img(GROUND_W, GROUND_H)
    img.rect(0, 0, GROUND_W, GROUND_H, GROUND_FACE)
    img.rect(0, 0, GROUND_W, 3, GROUND_TOP)
    img.rect(0, 3, GROUND_W, 1, TILE_EDGE)

    # Hazard chevrons: the boundary you do not want to touch.
    for x in range(GROUND_W):
        for y in range(4, 13):
            img.set(x, y, HAZARD_A if ((x + y) // 6) % 2 == 0 else HAZARD_B)
    img.rect(0, 13, GROUND_W, 1, TILE_EDGE)

    # Rack floor plates below, on a 24 px repeat that divides 288 evenly.
    for x in range(0, GROUND_W, 24):
        img.rect(x + 1, 16, 22, GROUND_H - 20, GROUND_DEEP)
        img.rect(x + 1, 16, 22, 1, GROUND_TOP)
        for i in range(3):
            img.rect(x + 4, 21 + i * 8, 16, 3, GROUND_FACE)
    img.rect(0, GROUND_H - 3, GROUND_W, 3, GROUND_DEEP)
    return img


# --------------------------------------------------------------------------- #
# Bitmap font: 5x7 uppercase, digits and the punctuation the UI needs
# --------------------------------------------------------------------------- #

FONT_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 .,:!?-+/'()%#>"
GLYPH_W, GLYPH_H = 5, 7

FONT_ROWS = {
    "A": [".###.", "#...#", "#...#", "#####", "#...#", "#...#", "#...#"],
    "B": ["####.", "#...#", "#...#", "####.", "#...#", "#...#", "####."],
    "C": [".####", "#....", "#....", "#....", "#....", "#....", ".####"],
    "D": ["####.", "#...#", "#...#", "#...#", "#...#", "#...#", "####."],
    "E": ["#####", "#....", "#....", "####.", "#....", "#....", "#####"],
    "F": ["#####", "#....", "#....", "####.", "#....", "#....", "#...."],
    "G": [".####", "#....", "#....", "#..##", "#...#", "#...#", ".###."],
    "H": ["#...#", "#...#", "#...#", "#####", "#...#", "#...#", "#...#"],
    "I": ["#####", "..#..", "..#..", "..#..", "..#..", "..#..", "#####"],
    "J": ["....#", "....#", "....#", "....#", "#...#", "#...#", ".###."],
    "K": ["#...#", "#..#.", "#.#..", "##...", "#.#..", "#..#.", "#...#"],
    "L": ["#....", "#....", "#....", "#....", "#....", "#....", "#####"],
    "M": ["#...#", "##.##", "#.#.#", "#.#.#", "#...#", "#...#", "#...#"],
    "N": ["#...#", "##..#", "#.#.#", "#.#.#", "#..##", "#...#", "#...#"],
    "O": [".###.", "#...#", "#...#", "#...#", "#...#", "#...#", ".###."],
    "P": ["####.", "#...#", "#...#", "####.", "#....", "#....", "#...."],
    "Q": [".###.", "#...#", "#...#", "#...#", "#.#.#", "#..#.", ".##.#"],
    "R": ["####.", "#...#", "#...#", "####.", "#.#..", "#..#.", "#...#"],
    "S": [".####", "#....", "#....", ".###.", "....#", "....#", "####."],
    "T": ["#####", "..#..", "..#..", "..#..", "..#..", "..#..", "..#.."],
    "U": ["#...#", "#...#", "#...#", "#...#", "#...#", "#...#", ".###."],
    "V": ["#...#", "#...#", "#...#", "#...#", "#...#", ".#.#.", "..#.."],
    "W": ["#...#", "#...#", "#...#", "#.#.#", "#.#.#", "##.##", "#...#"],
    "X": ["#...#", "#...#", ".#.#.", "..#..", ".#.#.", "#...#", "#...#"],
    "Y": ["#...#", "#...#", ".#.#.", "..#..", "..#..", "..#..", "..#.."],
    "Z": ["#####", "....#", "...#.", "..#..", ".#...", "#....", "#####"],
    "0": [".###.", "#...#", "#..##", "#.#.#", "##..#", "#...#", ".###."],
    "1": ["..#..", ".##..", "..#..", "..#..", "..#..", "..#..", ".###."],
    "2": [".###.", "#...#", "....#", "...#.", "..#..", ".#...", "#####"],
    "3": ["####.", "....#", "....#", ".###.", "....#", "....#", "####."],
    "4": ["...#.", "..##.", ".#.#.", "#..#.", "#####", "...#.", "...#."],
    "5": ["#####", "#....", "####.", "....#", "....#", "#...#", ".###."],
    "6": [".###.", "#....", "#....", "####.", "#...#", "#...#", ".###."],
    "7": ["#####", "....#", "...#.", "..#..", ".#...", ".#...", ".#..."],
    "8": [".###.", "#...#", "#...#", ".###.", "#...#", "#...#", ".###."],
    "9": [".###.", "#...#", "#...#", ".####", "....#", "....#", ".###."],
    " ": [".....", ".....", ".....", ".....", ".....", ".....", "....."],
    ".": [".....", ".....", ".....", ".....", ".....", ".##..", ".##.."],
    ",": [".....", ".....", ".....", ".....", ".##..", ".##..", "#...."],
    ":": [".....", ".##..", ".##..", ".....", ".##..", ".##..", "....."],
    "!": ["..#..", "..#..", "..#..", "..#..", "..#..", ".....", "..#.."],
    "?": [".###.", "#...#", "....#", "...#.", "..#..", ".....", "..#.."],
    "-": [".....", ".....", ".....", "#####", ".....", ".....", "....."],
    "+": [".....", "..#..", "..#..", "#####", "..#..", "..#..", "....."],
    "/": ["....#", "....#", "...#.", "..#..", ".#...", "#....", "#...."],
    "'": ["..#..", "..#..", ".....", ".....", ".....", ".....", "....."],
    "(": ["...#.", "..#..", ".#...", ".#...", ".#...", "..#..", "...#."],
    ")": [".#...", "..#..", "...#.", "...#.", "...#.", "..#..", ".#..."],
    "%": ["##..#", "##.#.", "..#..", ".#...", "#..##", "..#.#", "#..##"],
    "#": [".#.#.", "#####", ".#.#.", ".#.#.", "#####", ".#.#.", "....."],
    ">": ["#....", ".#...", "..#..", "...#.", "..#..", ".#...", "#...."],
}


def make_font_strip():
    img = Img(GLYPH_W * len(FONT_CHARS), GLYPH_H)
    for i, ch in enumerate(FONT_CHARS):
        rows = FONT_ROWS.get(ch)
        if rows is None:
            raise KeyError("no glyph authored for " + repr(ch))
        img.grid(rows, {"#": WHITE}, i * GLYPH_W, 0)
    return img


# --------------------------------------------------------------------------- #
# Big score numerals
# --------------------------------------------------------------------------- #

BIG_W, BIG_H = 12, 18

BIG_DIGITS = {
    "0": ["..######..", ".#......#.", "#........#", "#........#", "#........#",
          "#........#", "#........#", "#........#", "#........#", "#........#",
          "#........#", "#........#", "#........#", "#........#", ".#......#.",
          "..######.."],
    "1": ["....##....", "...###....", "..####....", ".##.##....", "....##....",
          "....##....", "....##....", "....##....", "....##....", "....##....",
          "....##....", "....##....", "....##....", "....##....", "..######..",
          "..######.."],
    "2": ["..######..", ".#......#.", "#........#", "#........#", ".........#",
          ".........#", "........#.", "......##..", "....##....", "..##......",
          ".#........", "#.........", "#.........", "#.........", "#########.",
          "##########"],
    "3": ["..######..", ".#......#.", "#........#", ".........#", ".........#",
          "........#.", "...#####..", "........#.", ".........#", ".........#",
          ".........#", "#........#", "#........#", "#........#", ".#......#.",
          "..######.."],
    "4": ["......###.", ".....####.", "....#.###.", "...#..###.", "..#...###.",
          ".#....###.", "#.....###.", "#.....###.", "##########", "##########",
          "......###.", "......###.", "......###.", "......###.", "......###.",
          "......###."],
    "5": ["##########", "#.........", "#.........", "#.........", "#.........",
          "########..", "#......##.", "........##", ".........#", ".........#",
          ".........#", "#........#", "#........#", "#........#", ".#......#.",
          "..######.."],
    "6": ["...#####..", "..#.....#.", ".#........", "#.........", "#.........",
          "#.........", "#.#####...", "##.....##.", "#........#", "#........#",
          "#........#", "#........#", "#........#", "#........#", ".#......#.",
          "..######.."],
    "7": ["##########", "#........#", ".........#", "........#.", ".......#..",
          "......#...", ".....#....", "....##....", "....##....", "....##....",
          "....##....", "....##....", "....##....", "....##....", "....##....",
          "....##...."],
    "8": ["..######..", ".#......#.", "#........#", "#........#", "#........#",
          ".#......#.", "..######..", ".#......#.", "#........#", "#........#",
          "#........#", "#........#", "#........#", "#........#", ".#......#.",
          "..######.."],
    "9": ["..######..", ".#......#.", "#........#", "#........#", "#........#",
          "#........#", "#........#", ".#.....##.", "..#####.#.", ".........#",
          ".........#", ".........#", "........#.", ".......#..", ".#####....",
          "..####...."],
}


def make_digit_strip():
    """Score numerals: white face wrapped in the same ink outline as the duck."""
    img = Img(BIG_W * 10, BIG_H)
    for i, ch in enumerate("0123456789"):
        cell = Img(BIG_W, BIG_H)
        cell.grid(BIG_DIGITS[ch], {"#": WHITE}, 1, 1)
        cell.outline()
        img.blit(cell, i * BIG_W, 0)
    return img


# --------------------------------------------------------------------------- #
# Badges, icons, button
# --------------------------------------------------------------------------- #

BADGE = 44


def make_badge(base, light, pips):
    img = Img(BADGE, BADGE)
    img.ellipse(BADGE / 2, BADGE / 2, 20, 20, base)
    img.ellipse(BADGE / 2, BADGE / 2 - 2, 17, 16, light)
    img.ellipse(BADGE / 2, BADGE / 2, 15, 15, base)
    # A ring of pips, one per deployment threshold reached.
    for px, py in [(22, 10), (10, 22), (34, 22), (22, 34)][:pips]:
        img.ellipse(px, py, 3.4, 3.4, light)
    # A patch mark in the middle: the plus sign, in patched green.
    img.rect(20, 15, 4, 14, PATCHED)
    img.rect(15, 20, 14, 4, PATCHED)
    img.outline()
    return img


SPEAKER = [
    ".....#......",
    "....##......",
    "...###......",
    "######......",
    "######......",
    "######......",
    "...###......",
    "....##......",
    ".....#......",
]

SOUND_WAVES = [
    "............",
    ".........#..",
    ".......#..#.",
    ".......#..#.",
    ".......#..#.",
    ".......#..#.",
    ".......#..#.",
    ".........#..",
    "............",
]


def make_icon(muted):
    img = Img(16, 16)
    img.grid(SPEAKER, {"#": UI_TEXT}, 2, 3)
    if muted:
        # A cross where the waves would be, in the same red the app uses for a
        # failed state, so the muted icon reads at a glance.
        for i in range(5):
            img.set(2 + 8 + i, 3 + 2 + i, MUTE_SLASH)
            img.set(2 + 12 - i, 3 + 2 + i, MUTE_SLASH)
    else:
        img.grid(SOUND_WAVES, {"#": UI_TEXT}, 2, 3)
    img.outline()
    return img


def make_button():
    img = Img(84, 26)
    img.rect(2, 0, 80, 26, PATCHED)
    img.rect(0, 2, 84, 22, PATCHED)
    img.rect(2, 2, 80, 3, GOLD_LIGHT)
    img.rect(2, 21, 80, 3, (0x2E, 0x9B, 0x78, 255))
    img.outline()
    return img


# --------------------------------------------------------------------------- #
# Atlas packing
# --------------------------------------------------------------------------- #


def pack(frames, padding=1):
    """Shelf packer. Padding stops neighbours bleeding at fractional scale."""
    max_w = 512
    x = y = shelf_h = 0
    placed = {}
    for name, img in frames:
        if x + img.w + padding > max_w:
            x = 0
            y += shelf_h + padding
            shelf_h = 0
        placed[name] = (x, y, img.w, img.h)
        x += img.w + padding
        shelf_h = max(shelf_h, img.h)
    atlas = Img(max_w, y + shelf_h)
    for name, img in frames:
        px, py, _, _ = placed[name]
        atlas.blit(img, px, py)
    return atlas, placed


def draw_text(img, font, text, x, y, scale, color):
    for i, ch in enumerate(text.upper()):
        idx = FONT_CHARS.find(ch)
        if idx < 0:
            continue
        for gy in range(GLYPH_H):
            for gx in range(GLYPH_W):
                if font.get(idx * GLYPH_W + gx, gy)[3]:
                    img.rect(x + (i * (GLYPH_W + 1) + gx) * scale,
                             y + gy * scale, scale, scale, color)


def make_share_image(duck, font):
    """1200x630 link preview, composed from the same art as the game."""
    w, h = 1200, 630
    img = Img(w, h)
    for y in range(h):
        img.rect(0, y, w, 1, lerp_color(SKY_TOP, SKY_BOTTOM, y / (h - 1)))

    nxt = rng(0xDEC0DE)
    for _ in range(160):
        img.set(nxt(w), nxt(h - 220), STAR)

    big_ground = scaled(make_ground(), 4)
    for x in range(0, w, big_ground.w):
        img.blit(big_ground, x, h - big_ground.h + 40)

    img.blit(scaled(duck, 9), 110, 210)

    draw_text(img, font, "FLAPPY DUCK", 470, 200, 10, DUCK_BASE)
    draw_text(img, font, "PATCH THE STACK. MISS NOTHING.", 470, 320, 4, UI_TEXT)
    draw_text(img, font, "TAP TO DEPLOY", 470, 380, 4, PATCHED)
    return img


def build():
    os.makedirs(OUT_DIR, exist_ok=True)

    ducks = [make_duck(i) for i in range(3)]
    font = make_font_strip()

    frames = [
        ("duck_0", ducks[0]),
        ("duck_1", ducks[1]),
        ("duck_2", ducks[2]),
        ("tile_body", make_tile_body()),
        ("tile_cap_up", make_tile_cap(True)),
        ("tile_cap_down", make_tile_cap(False)),
        ("badge_bronze", make_badge(BRONZE, BRONZE_LIGHT, 1)),
        ("badge_silver", make_badge(SILVER, SILVER_LIGHT, 2)),
        ("badge_gold", make_badge(GOLD, GOLD_LIGHT, 4)),
        ("icon_sound_on", make_icon(False)),
        ("icon_sound_off", make_icon(True)),
        ("button", make_button()),
        ("digits", make_digit_strip()),
        ("font", font),
        ("ground", make_ground()),
        ("bg", make_background()),
    ]

    atlas, placed = pack(frames)
    write_png(os.path.join(OUT_DIR, "atlas.png"), atlas)

    meta = {
        "image": "atlas.png",
        "width": atlas.w,
        "height": atlas.h,
        "frames": {n: {"x": p[0], "y": p[1], "w": p[2], "h": p[3]}
                   for n, p in placed.items()},
        "font": {"chars": FONT_CHARS, "cellW": GLYPH_W, "cellH": GLYPH_H,
                 "tracking": 1},
        "digits": {"cellW": BIG_W, "cellH": BIG_H},
        "tile": {"bodyW": TILE_W, "bodyH": BODY_H, "capW": CAP_W,
                 "capH": CAP_H, "lip": CAP_LIP},
    }
    with open(os.path.join(OUT_DIR, "atlas.json"), "w", encoding="utf-8") as fh:
        json.dump(meta, fh, indent=2, sort_keys=True)
        fh.write("\n")

    # Favicon: the duck's head, cropped and quadrupled.
    head = Img(16, 16)
    for y in range(16):
        for x in range(16):
            head.set(x, y, ducks[1].get(x + 17, y + 1))
    write_png(os.path.join(OUT_DIR, "favicon.png"), scaled(head, 4))

    write_png(os.path.join(OUT_DIR, "share.png"), make_share_image(ducks[1], font))

    print("atlas.png " + str(atlas.w) + "x" + str(atlas.h)
          + ", " + str(len(frames)) + " frames")
    for name in sorted(placed):
        p = placed[name]
        print("  " + name.ljust(16) + str(p[2]) + "x" + str(p[3])
              + " @ " + str(p[0]) + "," + str(p[1]))


if __name__ == "__main__":
    build()
