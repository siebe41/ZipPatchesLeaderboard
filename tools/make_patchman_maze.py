"""Author and prove the PatchMan maze.

The maze is a literal in ``app/patchman/config.mjs`` so it can be read and
tweaked by hand, but it is not drawn by hand. A maze that looks right and is
subtly wrong is the worst outcome here: one walled-off pocket makes a level
impossible to clear, and a level that cannot be cleared makes every score above
it unreachable. So the layout is described as rules, generated from them, and
then checked against the properties the game actually depends on.

The layout is original. It is built from a lattice of one-tile corridors with
chunky rectangular blocks between them, which is both what the genre needs and
what a circuit board looks like from above.

    python tools/make_patchman_maze.py            # print the maze and the report
    python tools/make_patchman_maze.py --check    # only check the checked-in one

``--check`` reads the maze back out of config.mjs and runs the same proofs, so
a hand edit that breaks the maze fails here rather than in someone's run.
"""

import argparse
import os
import re
import sys
from collections import deque

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CONFIG_JS = os.path.join(ROOT, "app", "patchman", "config.mjs")

COLS = 27
ROWS = 31

# One-tile corridors. The column list is symmetric about the centre column, 13,
# which is what lets the ghost house door sit on a real tile centre instead of
# on the boundary between two of them.
CORRIDOR_COLS = (1, 5, 9, 13, 17, 21, 25)
CORRIDOR_ROWS = (1, 5, 9, 14, 19, 24, 29)

TUNNEL_ROW = 14

# The house. Outer wall rows 15-18, cols 10-16; door at the top of the centre
# column; interior two rows deep so four vulnerabilities fit side by side.
HOUSE = {"left": 10, "right": 16, "top": 15, "bottom": 18}
DOOR = (13, 15)
HOUSE_INTERIOR = {"left": 11, "right": 15, "top": 16, "bottom": 17}

# Corridor segments filled back in. Without these the lattice is a regular grid
# with an escape at every junction, which is a poor maze: nothing to learn and
# nowhere that is dangerous. Each entry closes a whole segment between two
# corridors, so blocks merge into larger ones rather than leaving stubs.
# Both halves are listed, because writing them out is easier to check by eye
# than a mirroring rule.
CLOSED_SPANS = (
    # (col_from, col_to, row_from, row_to)
    # Between corridor rows 1 and 5 (rows 2-4): the top verticals sit on
    # columns 6-8/18-20 rather than 9/17, widening the top blocks.
    (6, 8, 2, 4), (18, 20, 2, 4),
    # Between rows 5 and 9 (rows 6-8): columns 9/17 close here instead of
    # 5/21, giving a different silhouette from the outer-vertical original.
    (9, 9, 6, 8), (17, 17, 6, 8),
    # Between rows 9 and 14 (rows 10-13), crossing corridor col 13 and
    # corridor row 9 at cols 11-15: a block above the house, narrower and
    # shifted from the original's 10-16 span.
    (11, 15, 10, 13),
    (13, 13, 10, 13),
    # The lanes level with the house (rows 15-18) run along col 9/17 instead
    # of col 5/21 -- a tighter approach to the house than the original.
    (9, 9, 15, 18), (17, 17, 15, 18),
    # Between rows 19 and 24 (rows 20-23): the verticals flanking the lower
    # centre swap from columns 9/17 to columns 5/21.
    (5, 5, 20, 23), (21, 21, 20, 23),
    # Between rows 24 and 29 (rows 25-28): the outer corners close fully,
    # rather than merging a single row the way the original did.
    (2, 4, 25, 28), (22, 24, 25, 28),
    (13, 13, 25, 28),                    # the centre column stops above the base
)

# No patch is worth anything sitting in the tunnel, and the original tradition
# of leaving the mouths bare is worth keeping: it stops the safest route on the
# board also being the most profitable one.
TUNNEL_BARE_COLS = tuple(range(0, 5)) + tuple(range(22, 27))

POWER_PELLETS = ((1, 5), (25, 5), (1, 24), (25, 24))

# Where the run starts. PatchMan sits on a four-way junction well clear of the
# house, and the bonus token appears on the corridor directly below the door.
PATCHMAN_START = (13, 24)
BONUS_TILE = (13, 19)

WALL = "#"
PATCH = "."
LOGO = "o"
DOOR_CH = "-"
EMPTY = " "


def build():
    grid = [[WALL] * COLS for _ in range(ROWS)]

    def open_tile(c, r, ch=PATCH):
        if 0 <= c < COLS and 0 <= r < ROWS:
            grid[r][c] = ch

    for col in CORRIDOR_COLS:
        for row in range(1, ROWS - 1):
            open_tile(col, row)
    for row in CORRIDOR_ROWS:
        for col in range(1, COLS - 1):
            open_tile(col, row)

    for c0, c1, r0, r1 in CLOSED_SPANS:
        for r in range(r0, r1 + 1):
            for c in range(c0, c1 + 1):
                grid[r][c] = WALL

    # The tunnel runs off both edges and wraps. The mouths carry no patches.
    open_tile(0, TUNNEL_ROW, EMPTY)
    open_tile(COLS - 1, TUNNEL_ROW, EMPTY)
    for c in TUNNEL_BARE_COLS:
        if grid[TUNNEL_ROW][c] != WALL:
            grid[TUNNEL_ROW][c] = EMPTY

    # The house is stamped last so nothing above can leave a patch inside it.
    for r in range(HOUSE["top"], HOUSE["bottom"] + 1):
        for c in range(HOUSE["left"], HOUSE["right"] + 1):
            grid[r][c] = WALL
    for r in range(HOUSE_INTERIOR["top"], HOUSE_INTERIOR["bottom"] + 1):
        for c in range(HOUSE_INTERIOR["left"], HOUSE_INTERIOR["right"] + 1):
            grid[r][c] = EMPTY
    grid[DOOR[1]][DOOR[0]] = DOOR_CH

    for c, r in POWER_PELLETS:
        grid[r][c] = LOGO

    grid[PATCHMAN_START[1]][PATCHMAN_START[0]] = EMPTY
    grid[BONUS_TILE[1]][BONUS_TILE[0]] = EMPTY

    return ["".join(row) for row in grid]


# --------------------------------------------------------------------------- #
# Proofs
# --------------------------------------------------------------------------- #

def walkable(ch):
    return ch != WALL


def neighbours(c, r, rows):
    """Four-way, with the tunnel wrapping horizontally and nothing wrapping
    vertically. The wrap is part of the maze, so it has to be part of the
    reachability proof too."""
    for dc, dr in ((0, -1), (-1, 0), (0, 1), (1, 0)):
        nc, nr = c + dc, r + dr
        if nr < 0 or nr >= len(rows):
            continue
        nc %= COLS
        if walkable(rows[nr][nc]):
            yield nc, nr


def check(rows):
    problems = []
    notes = []

    if len(rows) != ROWS:
        problems.append("expected %d rows, found %d" % (ROWS, len(rows)))
        return problems, notes
    for i, row in enumerate(rows):
        if len(row) != COLS:
            problems.append("row %d is %d wide, expected %d" % (i, len(row), COLS))
    if problems:
        return problems, notes

    for i, row in enumerate(rows):
        if row != row[::-1]:
            problems.append("row %d is not left-right symmetric" % i)

    allowed = set(WALL + PATCH + LOGO + DOOR_CH + EMPTY)
    for i, row in enumerate(rows):
        for j, ch in enumerate(row):
            if ch not in allowed:
                problems.append("row %d col %d holds %r" % (i, j, ch))

    # The border, minus the tunnel mouths.
    for c in range(COLS):
        if rows[0][c] != WALL or rows[ROWS - 1][c] != WALL:
            problems.append("the top or bottom border is open at column %d" % c)
    for r in range(ROWS):
        edge_open = r == TUNNEL_ROW
        for c in (0, COLS - 1):
            if walkable(rows[r][c]) != edge_open:
                problems.append("the side border at row %d column %d is wrong" % (r, c))

    # Everything a player can walk on is one region, counting the tunnel. A
    # second region is a pocket of patches that can never be collected, which
    # would make the level impossible to finish.
    start = None
    for r in range(ROWS):
        for c in range(COLS):
            if walkable(rows[r][c]) and (c, r) not in {DOOR}:
                start = (c, r)
                break
        if start:
            break

    seen = {start}
    queue = deque([start])
    while queue:
        c, r = queue.popleft()
        for nc, nr in neighbours(c, r, rows):
            if (nc, nr) in seen:
                continue
            # The door is one-way for the player: it is only ever crossed by a
            # vulnerability leaving or returning, so it is not a route between
            # the house and the maze for reachability purposes.
            if (nc, nr) == DOOR or _inside_house(nc, nr):
                continue
            seen.add((nc, nr))
            queue.append((nc, nr))

    outside = {(c, r) for r in range(ROWS) for c in range(COLS)
               if walkable(rows[r][c]) and not _inside_house(c, r) and (c, r) != DOOR}
    stranded = outside - seen
    if stranded:
        problems.append("%d tiles cannot be reached, first is %r"
                        % (len(stranded), sorted(stranded)[0]))

    # The house has to be sealed apart from its door, or a vulnerability walks
    # out through the wall and the release order stops meaning anything.
    for r in range(HOUSE["top"], HOUSE["bottom"] + 1):
        for c in range(HOUSE["left"], HOUSE["right"] + 1):
            edge = (r in (HOUSE["top"], HOUSE["bottom"])
                    or c in (HOUSE["left"], HOUSE["right"]))
            if edge and (c, r) != DOOR and walkable(rows[r][c]):
                problems.append("the house wall is open at %r" % ((c, r),))
    if rows[DOOR[1]][DOOR[0]] != DOOR_CH:
        problems.append("the door is not where it should be")
    if walkable(rows[DOOR[1] - 1][DOOR[0]]) is False:
        problems.append("there is no corridor above the door")

    # A patch has to sit on a tile a player can stand on, and there has to be a
    # sensible number of them.
    patches = sum(row.count(PATCH) for row in rows)
    logos = sum(row.count(LOGO) for row in rows)
    if logos != len(POWER_PELLETS):
        problems.append("expected %d logos, found %d" % (len(POWER_PELLETS), logos))
    if patches < 180:
        problems.append("only %d patches, a level would be over too quickly" % patches)

    # Two-tile-wide corridors read as rooms rather than corridors and make the
    # chase trivial, so the lattice should never produce one.
    for r in range(ROWS - 1):
        for c in range(COLS - 1):
            block = [rows[r][c], rows[r][c + 1], rows[r + 1][c], rows[r + 1][c + 1]]
            if all(walkable(ch) for ch in block) and not _inside_house(c, r):
                problems.append("open 2x2 block at %r" % ((c, r),))

    junctions = 0
    dead_ends = []
    for r in range(ROWS):
        for c in range(COLS):
            if not walkable(rows[r][c]) or _inside_house(c, r) or (c, r) == DOOR:
                continue
            ways = len(list(neighbours(c, r, rows)))
            if ways >= 3:
                junctions += 1
            if ways <= 1:
                dead_ends.append((c, r))
    if dead_ends:
        problems.append("dead ends at %r" % (dead_ends[:6],))

    notes.append("%d patches, %d logos, %d junctions" % (patches, logos, junctions))
    notes.append("%d walkable tiles outside the house" % len(outside))
    return problems, notes


def _inside_house(c, r):
    return (HOUSE_INTERIOR["left"] <= c <= HOUSE_INTERIOR["right"]
            and HOUSE_INTERIOR["top"] <= r <= HOUSE_INTERIOR["bottom"])


def read_checked_in():
    """Pull the maze back out of config.mjs, so --check tests the real thing.

    Deliberately a text scan rather than anything clever: the literal is the
    single source of truth for both engines, and a parser with its own idea of
    what the file means could agree with itself while disagreeing with the
    game. Take the rows between ``maze: [`` and its closing bracket, and take
    them literally.
    """
    with open(CONFIG_JS, "r", encoding="utf-8") as fh:
        text = fh.read()

    start = text.find("maze: [")
    if start < 0:
        raise SystemExit("could not find the maze array in " + CONFIG_JS)
    end = text.find("]", start)
    if end < 0:
        raise SystemExit("the maze array in %s is not closed" % CONFIG_JS)

    rows = re.findall(r"'([^']*)'", text[start:end])
    if not rows:
        raise SystemExit("the maze array in %s is empty" % CONFIG_JS)
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                        help="check the maze already in config.mjs")
    args = parser.parse_args()

    rows = read_checked_in() if args.check else build()
    problems, notes = check(rows)

    if not args.check:
        print("// generated by tools/make_patchman_maze.py")
        for row in rows:
            print("    '%s'," % row)
        print()

    for note in notes:
        print(note)
    if problems:
        for p in problems[:20]:
            print("FAIL " + p)
        print("%d problems." % len(problems))
        return 1
    print("The maze is symmetric, sealed, fully reachable and free of rooms.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
