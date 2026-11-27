"""Server-side rules and mode rotation for Last Fly Standing."""

from __future__ import annotations

import math
import random
import secrets
from dataclasses import asdict, dataclass
from itertools import count

W, H = 200, 60
TICK = 1 / 30

BOSS_BATTLE, SURVIVAL = "boss", "survival"
MODE_SECONDS = 60.0
WINS_TO_TAKE_MATCH = 3
BOSS_MAX_HP = 300
ATTACK_COOLDOWN = 0.5
BALL_SPEED = 70.0

MAN_W, MAN_H = 21, 13
MAN_X, MAN_Y = (W - MAN_W) // 2, (H - MAN_H) // 2
MAN_TOP, MAN_BOTTOM = MAN_Y, MAN_Y + MAN_H - 1
MOUTH_X, MOUTH_Y = MAN_X + 10, MAN_Y + 5

ARROW_LEN = 4
ARROW_RAMP_SECONDS = 120.0
ARROW_START_SPEED = 20.0
ARROW_START_GAP = 0.42
# The browser client uses this same order to pick each arrow's drawing.
DIRECTIONS = ((-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (1, -1), (-1, 1), (1, 1))
SPAWN_CLEAR_X, SPAWN_CLEAR_Y = 14, 7  # no arrow appears this close to a fly
FLY_W, FLY_H = 7, 4
FLY_ROW_SPANS = ((0, 6), (0, 6), (1, 5), (0, 6))  # visible columns per row
FLY_SPEED_X, FLY_SPEED_Y = 38.0, 19.0

COUNTDOWN_SECONDS = 3.0
ROUND_OVER_SECONDS = 3.0
SAD_SECONDS = 1.5
GULP_SECONDS = 0.27
OPEN_DISTANCE = 14

# Tongue timing stays at its initial difficulty throughout the fight.
TONGUE_WAIT = (4.0, 7.0)
TONGUE_WARN = 0.6
SURVIVAL_TONGUE_RAMP_SECONDS = 60.0
SURVIVAL_TONGUE_WAIT_END = (2.0, 3.5)
SURVIVAL_TONGUE_WARN_END = 0.4
TONGUE_LOCK = 0.18          # aim follows the target until this long before the strike
TONGUE_EXTEND = 0.1
TONGUE_HOLD = 0.4           # after landing, the tip chases its target this long
TONGUE_CHASE_SPEED = 42.0   # on-screen units/s; a fly moves at 38
TONGUE_RETRACT = 0.15
TONGUE_SPLASH = ((-1, -1), (0, -1), (1, -1),
                 (-2, 0), (-1, 0), (0, 0), (1, 0), (2, 0),
                 (-1, 1), (0, 1), (1, 1))

LOBBY, COUNTDOWN, PLAYING, ROUND_OVER, MATCH_OVER = (
    "lobby", "countdown", "playing", "round_over", "match_over")


@dataclass(frozen=True)
class ArenaLayout:
    width: int
    height: int
    man_x: int
    man_y: int
    fly_min_x: int = 0

    @property
    def mouth(self) -> tuple[int, int]:
        return self.man_x + 10, self.man_y + 5

    def in_catch_zone(self, x: float, y: float) -> bool:
        if not self.man_y <= math.floor(y) < self.man_y + MAN_H:
            return False
        if self.fly_min_x:
            return x + ARROW_LEN < self.fly_min_x
        return self.man_x <= math.floor(x) < self.man_x + MAN_W


BOSS_LAYOUT = ArenaLayout(W, H, MAN_X, MAN_Y)
SURVIVAL_LAYOUT = ArenaLayout(100, 30, 1, 8, 1 + MAN_W + 2)


def move_fly(x: float, y: float, dx: int, dy: int, dt: float,
             layout: ArenaLayout = BOSS_LAYOUT) -> tuple[float, float]:
    """Keep a fly in its arena and slide along Тимур's body."""
    man_x, man_y = layout.man_x, layout.man_y
    new_x = max(layout.fly_min_x, min(layout.width - FLY_W, x + dx * FLY_SPEED_X * dt))
    if y < man_y + MAN_H and y + FLY_H > man_y:
        if x + FLY_W <= man_x and new_x + FLY_W > man_x:
            new_x = man_x - FLY_W
        elif x >= man_x + MAN_W and new_x < man_x + MAN_W:
            new_x = man_x + MAN_W
    new_y = max(0.0, min(layout.height - FLY_H, y + dy * FLY_SPEED_Y * dt))
    if new_x < man_x + MAN_W and new_x + FLY_W > man_x:
        if y + FLY_H <= man_y and new_y + FLY_H > man_y:
            new_y = man_y - FLY_H
        elif y >= man_y + MAN_H and new_y < man_y + MAN_H:
            new_y = man_y + MAN_H
    return new_x, new_y


@dataclass
class Player:
    id: int
    name: str
    color: int
    token: str
    connected: bool = True


@dataclass
class Fly:
    player: Player
    x: float = 0.0
    y: float = 0.0
    dx: int = 0
    dy: int = 0
    alive: bool = False
    next_attack_at: float = 0.0
    wins: int = 0

    def touches(self, col: int, row: int) -> bool:
        r = row - int(self.y)
        if not 0 <= r < FLY_H:
            return False
        start, end = FLY_ROW_SPANS[r]
        return int(self.x) + start <= col <= int(self.x) + end

    @property
    def center(self) -> tuple[int, int]:
        return int(self.x) + FLY_W // 2, int(self.y) + FLY_H // 2


@dataclass
class Ball:
    x: float
    y: float
    vx: float
    vy: float
    color: int


@dataclass
class Arrow:
    x: float      # position of the arrow's tip
    y: float
    speed: float  # columns per second
    dx: int = -1
    dy: int = 0
    homing: bool = False  # Тимур has it and it is curving into his mouth

    @property
    def velocity(self) -> tuple[float, float]:
        # Rows are about twice as tall as columns are wide. Measuring length on
        # screen keeps every direction equally fast, and moving dx:dy in cells
        # keeps diagonal arrows travelling along the line they are drawn on.
        length = math.hypot(self.dx, self.dy * 2) or 1.0
        return self.speed * self.dx / length, self.speed * self.dy / length

    def cells(self, x: float, y: float) -> list[tuple[int, int]]:
        return [(math.floor(x) - i * self.dx, math.floor(y) - i * self.dy)
                for i in range(ARROW_LEN)]

    def approaching_man(self, layout: ArenaLayout) -> bool:
        if self.homing:
            return True
        vx, vy = self.velocity
        return any(layout.in_catch_zone(self.x + vx / self.speed * k,
                                        self.y + vy / self.speed * k)
                   for k in range(OPEN_DISTANCE + 1))

    def off_screen(self, layout: ArenaLayout) -> bool:
        tail_x = self.x - (ARROW_LEN - 1) * self.dx
        tail_y = self.y - (ARROW_LEN - 1) * self.dy
        return ((self.dx < 0 and tail_x < 0) or (self.dx > 0 and tail_x >= layout.width) or
                (self.dy < 0 and tail_y < 0) or (self.dy > 0 and tail_y >= layout.height))


class Game:
    def __init__(self, rng: random.Random | None = None):
        rng = rng or random.Random()
        survival_rng = random.Random()
        survival_rng.setstate(rng.getstate())
        self.arenas = {
            BOSS_BATTLE: Arena(BOSS_BATTLE, BOSS_LAYOUT, rng),
            SURVIVAL: Arena(SURVIVAL, SURVIVAL_LAYOUT, survival_rng),
        }
        self.arena = self.arenas[BOSS_BATTLE]
        self.players: dict[int, Player] = {}
        self.next_id = 1
        self.host_id: int | None = None
        self.rotating = False
        self.mode_remaining = MODE_SECONDS

    def add_player(self, name: str) -> Player:
        used = {p.color for p in self.players.values()}
        color = next(i for i in count() if i not in used)
        player = Player(self.next_id, self._unique_name(name), color,
                        secrets.token_urlsafe(18))
        self.next_id += 1
        self.players[player.id] = player
        for arena in self.arenas.values():
            arena.flies[player.id] = Fly(player)
        if self.host_id is None:
            self.host_id = player.id
        return player

    def _unique_name(self, name: str) -> str:
        names = {p.name for p in self.players.values()}
        if name not in names:
            return name
        for n in count(2):
            suffix = f" {n}"
            candidate = f"{name[:12 - len(suffix)]}{suffix}"
            if candidate not in names:
                return candidate

    def reconnect(self, token: str) -> Player | None:
        for p in self.players.values():
            if secrets.compare_digest(p.token, token):
                p.connected = True
                if self.host_id is None:
                    self.host_id = p.id
                return p
        return None

    def disconnect(self, pid: int) -> None:
        p = self.players.get(pid)
        if p is None:
            return
        p.connected = False
        self.arena.forfeit(self.arena.flies[pid])
        if self.arena.phase == LOBBY and not self.rotating:
            del self.players[pid]
            for arena in self.arenas.values():
                del arena.flies[pid]
        if self.host_id == pid:
            self.host_id = next(
                (q.id for q in self.players.values() if q.connected), None)

    def set_input(self, pid: int, dx: int, dy: int) -> None:
        fly = self.arena.flies.get(pid)
        if fly is not None and fly.player.connected:
            fly.dx, fly.dy = dx, dy

    def attack(self, pid: int) -> bool:
        return self.arena.attack(pid)

    def start(self, pid: int) -> bool:
        if pid != self.host_id or self.arena.phase not in (LOBBY, MATCH_OVER):
            return False
        self.arena.start()
        return True

    def tick(self, dt: float = TICK) -> None:
        if not self.rotating:
            self.arena.tick(dt)
            self.rotating = self.arena.phase == PLAYING
            return
        while dt > 1e-9:
            step = min(dt, self.mode_remaining)
            self.arena.tick(step)
            self.mode_remaining -= step
            dt -= step
            if self.mode_remaining <= 1e-9:
                self._switch_mode()

    def _switch_mode(self) -> None:
        next_mode = SURVIVAL if self.arena.mode == BOSS_BATTLE else BOSS_BATTLE
        self.arena = self.arenas[next_mode]
        self.mode_remaining = MODE_SECONDS
        if self.arena.phase == LOBBY:
            self.arena.start()
        for fly in self.arena.flies.values():
            fly.dx = fly.dy = 0
            if not fly.player.connected:
                self.arena.forfeit(fly)

    def snapshot(self) -> dict:
        state = self.arena.snapshot()
        state["host"] = self.host_id
        state["mode_seconds"] = math.ceil(self.mode_remaining - 1e-9) if self.rotating else None
        return state


class Arena:
    def __init__(self, mode: str, layout: ArenaLayout, rng: random.Random):
        self.mode = mode
        self.layout = layout
        self.rng = rng
        self.flies: dict[int, Fly] = {}
        self.phase = LOBBY
        self.timer = 0.0
        self.round = 0
        self.message = ""
        self.feed = ""
        self.feed_timer = 0.0
        self.competitive = False
        self.match_winner: Fly | None = None
        self.last_round_drawn = False
        self._clear_arena()

    def _clear_arena(self) -> None:
        self.arrows: list[Arrow] = []
        self.balls: list[Ball] = []
        self.boss_hp = BOSS_MAX_HP
        self.boss_hit = 0.0
        self.elapsed = 0.0
        self.spawn_timer = 0.4
        self.gulp = 0.0
        self.sad = 0.0
        self.tongue_state = "idle"
        self.tongue_timer = self._tongue_wait()
        self.tongue_spot = self.layout.mouth
        self.tongue_tip = self.layout.mouth
        self.tongue_pos = self.layout.mouth
        self.tongue_target: Fly | None = None

    def forfeit(self, fly: Fly) -> None:
        fly.dx = fly.dy = 0
        if self.phase in (COUNTDOWN, PLAYING) and fly.alive:
            self._eliminate(fly, f"{fly.player.name} left")

    def attack(self, pid: int) -> bool:
        fly = self.flies.get(pid)
        if (self.mode != BOSS_BATTLE or self.phase != PLAYING or self.boss_hp <= 0 or
                fly is None or not fly.alive or not fly.player.connected or
                self.elapsed + 1e-9 < fly.next_attack_at):
            return False
        x, y = fly.center
        to_x = self.layout.man_x + MAN_W / 2 - x
        to_y = self.layout.man_y + MAN_H / 2 - y
        distance = math.hypot(to_x, to_y * 2) or 1.0
        self.balls.append(Ball(float(x), float(y), BALL_SPEED * to_x / distance,
                               BALL_SPEED * to_y / distance, fly.player.color))
        fly.next_attack_at = self.elapsed + ATTACK_COOLDOWN
        return True

    def connected(self) -> list[Fly]:
        return [fly for fly in self.flies.values() if fly.player.connected]

    def alive(self) -> list[Fly]:
        return [fly for fly in self.flies.values() if fly.alive]

    # ---- match flow --------------------------------------------------

    def start(self) -> None:
        self.round = 0
        self.last_round_drawn = False
        self.match_winner = None
        for fly in self.flies.values():
            fly.wins = 0
        self._begin_round()

    def _begin_round(self) -> None:
        if not self.last_round_drawn:
            self.round += 1
        self.last_round_drawn = False
        self._clear_arena()
        racers = self.connected()
        self.competitive = self.mode == SURVIVAL and len(racers) >= 2
        for fly in self.flies.values():
            fly.alive = False
            fly.dx = fly.dy = 0
            fly.next_attack_at = 0.0
        if self.mode == SURVIVAL:
            self._spawn_flies(racers, 62.0)
        else:
            # Alternate sides so odd groups differ by only one player.
            self._spawn_flies(racers[::2], (self.layout.man_x + MAN_W + self.layout.width - FLY_W) / 2)
            self._spawn_flies(racers[1::2], (self.layout.man_x - FLY_W) / 2)
        self.phase = COUNTDOWN
        self.timer = COUNTDOWN_SECONDS
        self.message = ""
        self.feed = ""

    def _spawn_flies(self, group: list[Fly], x: float) -> None:
        height = self.layout.height
        for i, fly in enumerate(group):
            fly.alive = True
            fly.x = x
            y = round((i + 0.5) * height / len(group) - FLY_H / 2)
            fly.y = float(max(0, min(height - FLY_H, y)))

    def _resolve_round(self) -> None:
        if self.mode == BOSS_BATTLE and self.boss_hp <= 0:
            self.phase = MATCH_OVER
            self.message = "Victory! Тимур defeated!"
            self.timer = 0.0
            self.tongue_state = "idle"
            self.arrows.clear()
            self.balls.clear()
            for fly in self.flies.values():
                fly.dx = fly.dy = 0
            return
        survivors = self.alive()
        if len(survivors) >= (2 if self.competitive else 1):
            return
        if self.mode == BOSS_BATTLE:
            self.message = "All flies are down! Trying again..."
        elif not self.competitive:
            self.message = "Practice over. Trying again..."
        elif survivors:
            winner = survivors[0]
            winner.wins += 1
            self.message = f"{winner.player.name} wins the round!"
            if winner.wins >= WINS_TO_TAKE_MATCH:
                self.match_winner = winner
        else:
            self.last_round_drawn = True
            self.message = "Draw! Replaying the round"
        self.phase = ROUND_OVER
        self.timer = ROUND_OVER_SECONDS
        self.tongue_state = "idle"

    def _after_round(self) -> None:
        if self.match_winner is not None:
            self.phase = MATCH_OVER
            self.message = f"{self.match_winner.player.name} wins the match!"
            for fly in self.flies.values():
                fly.alive = False
        elif self.connected():
            self._begin_round()

    # ---- simulation --------------------------------------------------

    def tick(self, dt: float = TICK) -> None:
        if self.feed_timer > 0:
            self.feed_timer -= dt
            if self.feed_timer <= 0:
                self.feed = ""
        if self.phase == COUNTDOWN:
            if len(self.alive()) < (2 if self.competitive else 1):
                self._resolve_round()
                return
            self.timer = max(0.0, self.timer - dt)
            if self.timer <= 1e-9:
                self.phase = PLAYING
        elif self.phase == PLAYING:
            self.elapsed += dt
            self.sad = max(0.0, self.sad - dt)
            self.gulp = max(0.0, self.gulp - dt)
            self.boss_hit = max(0.0, self.boss_hit - dt)
            self._move_flies(dt)
            if self.mode == BOSS_BATTLE:
                self._move_balls(dt)
                if self.boss_hp <= 0:
                    self._resolve_round()
                    return
            self._spawn_arrows(dt)
            self._move_arrows(dt)
            self._update_tongue(dt)
            self._resolve_round()
        elif self.phase == ROUND_OVER:
            self.sad = max(0.0, self.sad - dt)
            self.timer = max(0.0, self.timer - dt)
            if self.timer <= 1e-9:
                self._after_round()

    def _move_flies(self, dt: float) -> None:
        for fly in self.alive():
            fly.x, fly.y = move_fly(fly.x, fly.y, fly.dx, fly.dy, dt, self.layout)

    def _move_balls(self, dt: float) -> None:
        kept: list[Ball] = []
        for ball in self.balls:
            if self.boss_hp <= 0:
                break
            new_x, new_y = ball.x + ball.vx * dt, ball.y + ball.vy * dt
            steps = max(1, math.ceil(max(abs(ball.vx), abs(ball.vy)) * dt))
            hit = any(self.layout.in_catch_zone(ball.x + (new_x - ball.x) * i / steps,
                                                ball.y + (new_y - ball.y) * i / steps)
                      for i in range(steps + 1))
            if hit:
                self.boss_hp = max(0, self.boss_hp - 1)
                self.boss_hit = 0.12
            elif 0 <= new_x < self.layout.width and 0 <= new_y < self.layout.height:
                ball.x, ball.y = new_x, new_y
                kept.append(ball)
        self.balls = kept

    def _spawn_arrows(self, dt: float) -> None:
        self.spawn_timer -= dt
        while self.spawn_timer <= 0:
            if self.mode == SURVIVAL:
                base = min(58.0, ARROW_START_SPEED + self.elapsed * 0.7)
                gap = max(0.09, ARROW_START_GAP - self.elapsed * 0.009)
            else:
                # Ease in and out over two minutes, capping speed and spawn rate at 2x.
                progress = max(0.0, min(1.0, self.elapsed / ARROW_RAMP_SECONDS))
                multiplier = 1.0 + progress * progress * (3.0 - 2.0 * progress)
                base = ARROW_START_SPEED * multiplier
                gap = ARROW_START_GAP / multiplier
            speed = base * self.rng.uniform(0.85, 1.2)
            arrow = self._new_arrow(speed)
            if arrow is not None:
                self.arrows.append(arrow)
            self.spawn_timer += self.rng.uniform(gap * 0.6, gap * 1.2)

    def _new_arrow(self, speed: float) -> Arrow | None:
        """Pick a direction and an edge, avoiding spots right next to a fly."""
        layout = self.layout
        for _ in range(8):
            dx, dy = self.rng.choice(DIRECTIONS)
            if dy == 0 or (dx != 0 and self.rng.random() < 0.5):
                x = -1 if dx > 0 else layout.width
                if self.mode == SURVIVAL and dx > 0:
                    clear_rows = [y for y in range(layout.height) if not layout.in_catch_zone(x, y)]
                    y = self.rng.choice(clear_rows)
                else:
                    y = self.rng.randrange(layout.height)
            else:
                x = self.rng.randrange(layout.width)
                y = -1 if dy > 0 else layout.height
            if all(abs(p.center[0] - x) >= SPAWN_CLEAR_X or
                   abs(p.center[1] - y) >= SPAWN_CLEAR_Y for p in self.alive()):
                return Arrow(float(x), float(y), speed, dx, dy)
        return None

    def _move_arrows(self, dt: float) -> None:
        kept: list[Arrow] = []
        survival = self.mode == SURVIVAL
        for a in self.arrows:
            if a.homing:
                if not self._curve_into_mouth(a, dt):
                    kept.append(a)
                continue
            vx, vy = a.velocity
            new_x, new_y = a.x + vx * dt, a.y + vy * dt
            # Sweep the whole path so fast arrows cannot skip over a fly.
            steps = max(1, math.ceil(max(abs(vx), abs(vy)) * dt))
            for i in range(steps + 1):
                x = a.x + (new_x - a.x) * i / steps
                y = a.y + (new_y - a.y) * i / steps
                cells = a.cells(x, y)
                if not survival and any(self.layout.in_catch_zone(col, row) for col, row in cells):
                    a.x, a.y = x, y
                    a.homing = True
                    break
                for p in self.alive():
                    if any(p.touches(col, row) for col, row in cells):
                        self._eliminate(p, f"{p.player.name} got hit by an arrow")
                        self.sad = SAD_SECONDS
            if not a.homing:
                a.x, a.y = new_x, new_y
                if survival and self.layout.in_catch_zone(a.x, a.y):
                    a.homing = True
            if a.homing or not a.off_screen(self.layout):
                kept.append(a)
        self.arrows = kept

    def _curve_into_mouth(self, a: Arrow, dt: float) -> bool:
        """Move a caught arrow toward the mouth; True once swallowed."""
        mx, my = self.layout.mouth
        to_x, to_y = mx - a.x, my - a.y
        distance = math.hypot(to_x, to_y)
        step = a.speed * dt
        if distance <= max(step, 0.5):
            self.gulp = GULP_SECONDS
            return True
        a.x += to_x / distance * step
        a.y += to_y / distance * step
        a.dx = (to_x > 0.5) - (to_x < -0.5)
        a.dy = (to_y > 0.5) - (to_y < -0.5)
        return False

    def _eliminate(self, p: Fly, text: str) -> None:
        p.alive = False
        p.dx = p.dy = 0
        self._post(text)

    def _post(self, text: str) -> None:
        self.feed = text
        self.feed_timer = 2.5

    def _closest_fly(self) -> Fly | None:
        # Terminal cells are about twice as tall as they are wide.
        mx, my = self.layout.mouth

        def distance(p: Fly) -> float:
            cx, cy = p.center
            return math.hypot(cx - mx, (cy - my) * 2)
        return min(self.alive(), key=distance, default=None)

    def _tip_at(self, progress: float) -> tuple[int, int]:
        sx, sy = self.tongue_spot
        mx, my = self.layout.mouth
        return (round(mx + (sx - mx) * progress), round(my + (sy - my) * progress))

    def _tongue_catch(self) -> None:
        tx, ty = self.tongue_tip
        mx, my = self.layout.mouth
        steps = max(abs(tx - mx), abs(ty - my), 1)
        cells = {(round(mx + (tx - mx) * i / steps),
                  round(my + (ty - my) * i / steps))
                 for i in range(steps + 1)}
        cells |= {(tx + cx, ty + cy) for cx, cy in TONGUE_SPLASH}
        for p in self.alive():
            if any(p.touches(c, r) for c, r in cells):
                self._eliminate(p, f"Тимур's tongue caught {p.player.name}")

    def _tongue_ramp(self) -> float:
        return min(1.0, self.elapsed / SURVIVAL_TONGUE_RAMP_SECONDS) if self.mode == SURVIVAL else 0.0

    def _tongue_wait(self) -> float:
        ramp = self._tongue_ramp()
        low, high = (start + (end - start) * ramp
                     for start, end in zip(TONGUE_WAIT, SURVIVAL_TONGUE_WAIT_END))
        return self.rng.uniform(low, high)

    def _tongue_prey(self) -> Fly | None:
        p = self.tongue_target
        return p if p is not None and p.alive else None

    def _chase_prey(self, dt: float) -> None:
        prey = self._tongue_prey()
        if prey is None:
            return
        x, y = self.tongue_pos
        to_x, to_y = prey.center[0] - x, prey.center[1] - y
        distance = math.hypot(to_x, to_y * 2)   # rows are twice as tall on screen
        step = TONGUE_CHASE_SPEED * dt
        if distance <= step:
            self.tongue_pos = (float(prey.center[0]), float(prey.center[1]))
        else:
            self.tongue_pos = (x + to_x / distance * step, y + to_y / distance * step)
        self.tongue_tip = (round(self.tongue_pos[0]), round(self.tongue_pos[1]))

    def _update_tongue(self, dt: float) -> None:
        self.tongue_timer -= dt
        if self.tongue_state == "idle":
            if self.tongue_timer <= 0:
                target = self._closest_fly()
                if target is not None:
                    self.tongue_target = target
                    self.tongue_spot = target.center
                    self.tongue_state = "warn"
                    self.tongue_timer = TONGUE_WARN + (SURVIVAL_TONGUE_WARN_END - TONGUE_WARN) * self._tongue_ramp()
        elif self.tongue_state == "warn":
            prey = self._tongue_prey()
            if prey is None:                # target died: pick the next closest
                prey = self._closest_fly()
                self.tongue_target = prey
            if prey is not None and self.tongue_timer > TONGUE_LOCK:
                self.tongue_spot = prey.center
            if self.tongue_timer <= 0:
                self.tongue_state = "extend"
                self.tongue_timer = TONGUE_EXTEND
        elif self.tongue_state == "extend":
            progress = min(1.0, 1 - self.tongue_timer / TONGUE_EXTEND)
            self.tongue_tip = self._tip_at(progress)
            self._tongue_catch()
            if self.tongue_timer <= 0:
                self.tongue_state = "hold"
                self.tongue_timer = TONGUE_HOLD
                self.tongue_tip = self.tongue_spot
                self.tongue_pos = (float(self.tongue_spot[0]), float(self.tongue_spot[1]))
        elif self.tongue_state == "hold":
            self._chase_prey(dt)
            self._tongue_catch()
            if self.tongue_timer <= 0:
                self.tongue_state = "retract"
                self.tongue_timer = TONGUE_RETRACT
                self.tongue_spot = self.tongue_tip
        elif self.tongue_state == "retract":
            self.tongue_tip = self._tip_at(max(0.0, self.tongue_timer / TONGUE_RETRACT))
            if self.tongue_timer <= 0:
                self.tongue_state = "idle"
                self.tongue_target = None
                self.tongue_tip = self.layout.mouth
                self.tongue_timer = self._tongue_wait()

    # ---- output ------------------------------------------------------

    def man_face(self) -> str:
        if self.tongue_state in ("extend", "hold", "retract"):
            return "tongue"
        if self.tongue_state == "warn":
            return "warn"
        if self.sad > 0:
            return "sad"
        if self.gulp > 0 or any(a.approaching_man(self.layout) for a in self.arrows):
            return "open"
        return "smile"

    def snapshot(self) -> dict:
        survival = self.mode == SURVIVAL
        target = self.tongue_target
        return {
            "t": "state",
            "mode": self.mode,
            "arena": asdict(self.layout),
            "phase": self.phase,
            "timer": round(max(0.0, self.timer), 2),
            "round": self.round,
            "boss": {"hp": self.boss_hp, "max_hp": BOSS_MAX_HP,
                     "hit": self.boss_hit > 0} if not survival else None,
            "target": WINS_TO_TAKE_MATCH if survival else None,
            "practice": survival and not self.competitive,
            "message": self.message,
            "feed": self.feed,
            "face": self.man_face(),
            "players": [{
                "id": p.player.id, "name": p.player.name, "color": p.player.color,
                "x": math.floor(p.x * 10) / 10, "y": math.floor(p.y * 10) / 10,
                "alive": p.alive, "connected": p.player.connected, "wins": p.wins,
            } for p in self.flies.values()],
            "arrows": [[math.floor(a.x), math.floor(a.y),
                        DIRECTIONS.index((a.dx, a.dy)) if (a.dx, a.dy) in DIRECTIONS else 0]
                       for a in self.arrows],
            "balls": [[round(b.x, 2), round(b.y, 2), b.color] for b in self.balls],
            "tongue": {
                "state": self.tongue_state,
                "locked": self.tongue_state == "warn" and self.tongue_timer <= TONGUE_LOCK,
                "spot": self.tongue_spot,
                "tip": self.tongue_tip,
                "name": target.player.name if target else "",
            },
        }
