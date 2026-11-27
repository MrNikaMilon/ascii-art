#!/usr/bin/env python3
"""
Fly Dodge — a console game.

You are a fly. Arrows ( <==8 ) fly in from the right.
Move up and down to dodge them. Survive as long as you can.

Controls:
    UP / W     move up
    DOWN / S   move down
    P          pause
    Q          quit
    R          restart (after game over)
"""

import curses
import locale
import random
import time

FLY = [
    "._q0p_.",
    "'=(_)='",
    " / V \\ ",
    "(_/^\\_)",
]
ARROW = "<==8"

# Тимур — static, and very fond of the fly.
MAN = [
    "    .___________.",
    "   /   _______   \\",
    "  /   /       \\   \\",
    " |   |  o   o  |   |",
    "[|   |   ___   |   |]",
    "[|   |  \\___/  |   |]",
    " |    \\_______/    |",
    "  \\_____|||||_____/",
    "       /|||||\\",
    "     _/_______\\_",
    "    /           \\",
    "   /    ТИМУР    \\",
    "  /_______________\\",
]
MAN_SAD_MOUTH = {          # line index -> replacement when the fly is hit
    5: "[|   |  /   \\  |   |]",
}
MAN_OPEN_MOUTH = {         # line index -> replacement when catching an arrow
    5: "[|   |  (   )  |   |]",
    6: " |    \\_(___)_/    |",
}
MAN_X, MAN_Y = 1, 2
MAN_W = max(len(line) for line in MAN)
MAN_TOP, MAN_BOTTOM = MAN_Y, MAN_Y + len(MAN) - 1
MOUTH_Y = MAN_Y + 5        # arrows are pulled toward this row...
MOUTH_X = MAN_X + 10       # ...and swallowed when the tip reaches this column
OPEN_DIST = 14             # mouth opens when an arrow is this close to his face

FLY_X = MAN_X + MAN_W + 6
TICK = 1 / 30           # seconds per frame
MIN_W, MIN_H = FLY_X + 30, MAN_Y + len(MAN) + 1


def fly_cells(y):
    """Screen cells occupied by the fly's visible characters."""
    cells = set()
    for dy, line in enumerate(FLY):
        for dx, ch in enumerate(line):
            if ch != " ":
                cells.add((y + dy, FLY_X + dx))
    return cells


class Game:
    def __init__(self, h, w):
        self.h, self.w = h, w
        self.top = 1                    # row 0 is the HUD
        self.bottom = h - 1             # last usable row (exclusive)
        self.fly_y = (self.top + self.bottom - len(FLY)) // 2
        self.arrows = []                # [x (float), y, speed]
        self.score = 0
        self.frame = 0
        self.spawn_timer = 0
        self.over = False
        self.paused = False
        self.gulp = 0                   # frames to keep mouth open after a catch
        self.mouth_open = False

    @property
    def level(self):
        return 1 + self.score // 15

    def move(self, dy):
        lo, hi = self.top, self.bottom - len(FLY)
        self.fly_y = max(lo, min(hi, self.fly_y + dy))

    def spawn(self):
        y = random.randint(self.top, self.bottom - 1)
        speed = random.uniform(0.6, 1.0) + 0.15 * (self.level - 1)
        self.arrows.append([float(self.w - len(ARROW) - 1), y, speed])

    @staticmethod
    def heading_for_man(a):
        """Arrow has fully passed the fly and is level with Тимур."""
        x, y, _ = a
        return x + len(ARROW) < FLY_X and MAN_TOP <= y <= MAN_BOTTOM

    def update(self):
        if self.over or self.paused:
            return
        self.frame += 1

        self.spawn_timer -= 1
        if self.spawn_timer <= 0:
            self.spawn()
            # spawn faster as the level grows
            base = max(6, 22 - 2 * self.level)
            self.spawn_timer = random.randint(base // 2, base)

        survivors = []
        for a in self.arrows:
            a[0] -= a[2]
            if self.heading_for_man(a):
                # steer the arrow into his mouth, fast enough to arrive in time
                dy = MOUTH_Y - a[1]
                if dy:
                    frames_left = max(1.0, (a[0] - MOUTH_X) / a[2])
                    step = max(1, int(abs(dy) / frames_left + 0.999))
                    a[1] += max(-step, min(step, dy))
                if a[0] <= MOUTH_X and a[1] == MOUTH_Y:
                    self.score += 1     # Тимур caught it
                    self.gulp = 8
                    continue
            if a[0] + len(ARROW) < 0:
                self.score += 1         # dodged one
            else:
                survivors.append(a)
        self.arrows = survivors

        if self.gulp:
            self.gulp -= 1
        self.mouth_open = self.gulp > 0 or any(
            self.heading_for_man(a) and a[0] <= MAN_X + MAN_W + OPEN_DIST
            for a in self.arrows)

        body = fly_cells(self.fly_y)
        for x, y, _ in self.arrows:
            ix = int(x)
            for dx in range(len(ARROW)):
                if (y, ix + dx) in body:
                    self.over = True
                    return


def safe_add(win, y, x, text, attr=0):
    h, w = win.getmaxyx()
    if y < 0 or y >= h or x >= w:
        return
    if x < 0:
        text = text[-x:]
        x = 0
    text = text[: max(0, w - x - (1 if y == h - 1 else 0))]
    if text:
        try:
            win.addstr(y, x, text, attr)
        except curses.error:
            pass


def draw(scr, g, colors):
    scr.erase()
    hud = f" Score: {g.score}   Level: {g.level}   [Up/Down] move  [P] pause  [Q] quit "
    safe_add(scr, 0, 0, hud.ljust(g.w), curses.A_REVERSE)

    for dy, line in enumerate(MAN):
        if g.over:
            line = MAN_SAD_MOUTH.get(dy, line)
        elif g.mouth_open:
            line = MAN_OPEN_MOUTH.get(dy, line)
        safe_add(scr, MAN_Y + dy, MAN_X, line, colors["man"])

    for dy, line in enumerate(FLY):
        safe_add(scr, g.fly_y + dy, FLY_X, line, colors["fly"])

    for x, y, _ in g.arrows:
        safe_add(scr, y, int(x), ARROW, colors["arrow"])

    if g.paused:
        msg = "PAUSED — press P"
        safe_add(scr, g.h // 2, (g.w - len(msg)) // 2, msg, curses.A_BOLD)

    if g.over:
        lines = [
            "  SPLAT!  ",
            f"  Score: {g.score}  ",
            "  R = restart   Q = quit  ",
        ]
        for i, l in enumerate(lines):
            safe_add(scr, g.h // 2 - 1 + i, (g.w - len(l)) // 2, l,
                     colors["over"] | curses.A_BOLD)
    scr.refresh()


def main(scr):
    curses.curs_set(0)
    scr.nodelay(True)
    scr.keypad(True)

    colors = {"fly": 0, "arrow": 0, "over": 0, "man": 0}
    if curses.has_colors():
        curses.start_color()
        try:
            curses.use_default_colors()
            bg = -1
        except curses.error:
            bg = curses.COLOR_BLACK
        curses.init_pair(1, curses.COLOR_GREEN, bg)
        curses.init_pair(2, curses.COLOR_YELLOW, bg)
        curses.init_pair(3, curses.COLOR_RED, bg)
        colors = {"fly": curses.color_pair(1),
                  "arrow": curses.color_pair(2),
                  "over": curses.color_pair(3),
                  "man": curses.A_NORMAL}

    h, w = scr.getmaxyx()
    if h < MIN_H or w < MIN_W:
        return f"Terminal too small ({w}x{h}); need at least {MIN_W}x{MIN_H}."

    g = Game(h, w)
    while True:
        start = time.monotonic()

        while True:
            key = scr.getch()
            if key == -1:
                break
            if key in (ord("q"), ord("Q")):
                return f"Final score: {g.score}"
            if g.over:
                if key in (ord("r"), ord("R")):
                    h, w = scr.getmaxyx()
                    g = Game(h, w)
                continue
            if key in (ord("p"), ord("P")):
                g.paused = not g.paused
            elif not g.paused and key in (curses.KEY_UP, ord("w"), ord("W")):
                g.move(-1)
            elif not g.paused and key in (curses.KEY_DOWN, ord("s"), ord("S")):
                g.move(1)

        g.update()
        draw(scr, g, colors)

        elapsed = time.monotonic() - start
        if elapsed < TICK:
            time.sleep(TICK - elapsed)


if __name__ == "__main__":
    locale.setlocale(locale.LC_ALL, "")   # so "ТИМУР" renders correctly
    msg = curses.wrapper(main)
    if msg:
        print(msg)
