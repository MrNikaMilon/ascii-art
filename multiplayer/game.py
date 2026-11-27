"""Server-side rules for Last Fly Standing. No networking lives here."""

from __future__ import annotations

import math
import random
import secrets
from dataclasses import dataclass
from itertools import count

W, H = 200, 60
TICK = 1 / 30

MIN_PLAYERS = 1
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


def move_fly(x: float, y: float, dx: int, dy: int, dt: float) -> tuple[float, float]:
    """Move within the arena, sliding along Тимур instead of entering his body."""
    new_x = max(0.0, min(W - FLY_W, x + dx * FLY_SPEED_X * dt))
    if y < MAN_Y + MAN_H and y + FLY_H > MAN_Y:
        if x + FLY_W <= MAN_X and new_x + FLY_W > MAN_X:
            new_x = MAN_X - FLY_W
        elif x >= MAN_X + MAN_W and new_x < MAN_X + MAN_W:
            new_x = MAN_X + MAN_W
    new_y = max(0.0, min(H - FLY_H, y + dy * FLY_SPEED_Y * dt))
    if new_x < MAN_X + MAN_W and new_x + FLY_W > MAN_X:
        if y + FLY_H <= MAN_Y and new_y + FLY_H > MAN_Y:
            new_y = MAN_Y - FLY_H
        elif y >= MAN_Y + MAN_H and new_y < MAN_Y + MAN_H:
            new_y = MAN_Y + MAN_H
    return new_x, new_y


@dataclass
class Player:
    id: int
    name: str
    color: int
    token: str
    connected: bool = True
    x: float = 0.0
    y: float = 0.0
    dx: int = 0
    dy: int = 0
    alive: bool = False
    next_attack_at: float = 0.0

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

    @staticmethod
    def in_catch_zone(x: float, y: float) -> bool:
        return (MAN_X <= math.floor(x) < MAN_X + MAN_W and
                MAN_TOP <= math.floor(y) <= MAN_BOTTOM)

    @property
    def approaching_man(self) -> bool:
        if self.homing:
            return True
        vx, vy = self.velocity
        return any(self.in_catch_zone(self.x + vx / self.speed * k,
                                      self.y + vy / self.speed * k)
                   for k in range(OPEN_DISTANCE + 1))

    @property
    def off_screen(self) -> bool:
        tail_x = self.x - (ARROW_LEN - 1) * self.dx
        tail_y = self.y - (ARROW_LEN - 1) * self.dy
        return ((self.dx < 0 and tail_x < 0) or (self.dx > 0 and tail_x >= W) or
                (self.dy < 0 and tail_y < 0) or (self.dy > 0 and tail_y >= H))


class Game:
    def __init__(self, rng: random.Random | None = None):
        self.rng = rng or random.Random()
        self.players: dict[int, Player] = {}
        self.next_id = 1
        self.host_id: int | None = None
        self.phase = LOBBY
        self.timer = 0.0
        self.round = 0
        self.message = ""
        self.feed = ""
        self.feed_timer = 0.0
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
        self.tongue_spot = (MOUTH_X, MOUTH_Y)
        self.tongue_tip = (MOUTH_X, MOUTH_Y)
        self.tongue_pos = (float(MOUTH_X), float(MOUTH_Y))
        self.tongue_target: int | None = None

    # ---- players -----------------------------------------------------

    def add_player(self, name: str) -> Player:
        used = {p.color for p in self.players.values()}
        color = next(i for i in count() if i not in used)
        player = Player(self.next_id, self._unique_name(name), color,
                        secrets.token_urlsafe(18))
        self.next_id += 1
        self.players[player.id] = player
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
        p.dx = p.dy = 0
        if self.phase in (COUNTDOWN, PLAYING) and p.alive:
            p.alive = False
            self._post(f"{p.name} left")
        if self.phase == LOBBY:
            del self.players[pid]
        if self.host_id == pid:
            self.host_id = next(
                (q.id for q in self.players.values() if q.connected), None)

    def set_input(self, pid: int, dx: int, dy: int) -> None:
        p = self.players.get(pid)
        if p is not None:
            p.dx, p.dy = dx, dy

    def attack(self, pid: int) -> bool:
        p = self.players.get(pid)
        if (self.phase != PLAYING or self.boss_hp <= 0 or p is None or
                not p.alive or not p.connected or self.elapsed + 1e-9 < p.next_attack_at):
            return False
        x, y = p.center
        to_x, to_y = MAN_X + MAN_W / 2 - x, MAN_Y + MAN_H / 2 - y
        distance = math.hypot(to_x, to_y * 2) or 1.0
        self.balls.append(Ball(float(x), float(y), BALL_SPEED * to_x / distance,
                               BALL_SPEED * to_y / distance, p.color))
        p.next_attack_at = self.elapsed + ATTACK_COOLDOWN
        return True

    def connected(self) -> list[Player]:
        return [p for p in self.players.values() if p.connected]

    def alive(self) -> list[Player]:
        return [p for p in self.players.values() if p.alive]

    # ---- match flow --------------------------------------------------

    def start(self, pid: int) -> bool:
        if pid != self.host_id or self.phase not in (LOBBY, MATCH_OVER):
            return False
        for p in [p for p in self.players.values() if not p.connected]:
            del self.players[p.id]
        if len(self.players) < MIN_PLAYERS:
            return False
        self.round = 0
        self._begin_round()
        return True

    def _begin_round(self) -> None:
        self.round += 1
        self._clear_arena()
        racers = self.connected()
        for p in self.players.values():
            p.alive = False
            p.dx = p.dy = 0
            p.next_attack_at = 0.0
        # Alternate sides so odd groups differ by only one player.
        for side, group in enumerate((racers[::2], racers[1::2])):
            for i, p in enumerate(group):
                p.alive = True
                p.x = ((MAN_X + MAN_W + W - FLY_W) / 2 if side == 0
                       else (MAN_X - FLY_W) / 2)
                p.y = float(round((i + 0.5) * H / len(group) - FLY_H / 2))
                p.y = max(0.0, min(H - FLY_H, p.y))
        self.phase = COUNTDOWN
        self.timer = COUNTDOWN_SECONDS
        self.message = ""
        self.feed = ""

    def _to_lobby(self, message: str) -> None:
        self.phase = LOBBY
        self.message = message
        self.round = 0
        self._clear_arena()
        for p in [p for p in self.players.values() if not p.connected]:
            del self.players[p.id]
        for p in self.players.values():
            p.alive = False
        if self.host_id not in self.players:
            self.host_id = next(iter(self.players), None)

    def _resolve_round(self) -> None:
        if self.boss_hp <= 0:
            self.phase = MATCH_OVER
            self.message = "Victory! Тимур defeated!"
            self.timer = 0.0
            self.tongue_state = "idle"
            self.arrows.clear()
            self.balls.clear()
            for p in self.players.values():
                p.dx = p.dy = 0
            return
        if self.alive():
            return
        self.message = "All flies are down! Trying again..."
        self.phase = ROUND_OVER
        self.timer = ROUND_OVER_SECONDS
        self.tongue_state = "idle"

    def _after_round(self) -> None:
        if len(self.connected()) >= MIN_PLAYERS:
            self._begin_round()
        else:
            self._to_lobby("Not enough players to continue")

    # ---- simulation --------------------------------------------------

    def tick(self, dt: float = TICK) -> None:
        if self.feed_timer > 0:
            self.feed_timer -= dt
            if self.feed_timer <= 0:
                self.feed = ""
        if self.phase == COUNTDOWN:
            if not self.alive():
                self._resolve_round()
                return
            self.timer -= dt
            if self.timer <= 0:
                self.phase = PLAYING
        elif self.phase == PLAYING:
            self.elapsed += dt
            self.sad = max(0.0, self.sad - dt)
            self.gulp = max(0.0, self.gulp - dt)
            self.boss_hit = max(0.0, self.boss_hit - dt)
            self._move_flies(dt)
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
            self.timer -= dt
            if self.timer <= 0:
                self._after_round()

    def _move_flies(self, dt: float) -> None:
        for p in self.alive():
            p.x, p.y = move_fly(p.x, p.y, p.dx, p.dy, dt)

    def _move_balls(self, dt: float) -> None:
        kept: list[Ball] = []
        for ball in self.balls:
            if self.boss_hp <= 0:
                break
            new_x, new_y = ball.x + ball.vx * dt, ball.y + ball.vy * dt
            steps = max(1, math.ceil(max(abs(ball.vx), abs(ball.vy)) * dt))
            hit = any(Arrow.in_catch_zone(ball.x + (new_x - ball.x) * i / steps,
                                         ball.y + (new_y - ball.y) * i / steps)
                      for i in range(steps + 1))
            if hit:
                self.boss_hp = max(0, self.boss_hp - 1)
                self.boss_hit = 0.12
            elif 0 <= new_x < W and 0 <= new_y < H:
                ball.x, ball.y = new_x, new_y
                kept.append(ball)
        self.balls = kept

    def _spawn_arrows(self, dt: float) -> None:
        self.spawn_timer -= dt
        while self.spawn_timer <= 0:
            # Ease in and out over two minutes, capping speed and spawn rate at 2x.
            progress = max(0.0, min(1.0, self.elapsed / ARROW_RAMP_SECONDS))
            multiplier = 1.0 + progress * progress * (3.0 - 2.0 * progress)
            base = ARROW_START_SPEED * multiplier
            speed = base * self.rng.uniform(0.85, 1.2)
            arrow = self._new_arrow(speed)
            if arrow is not None:
                self.arrows.append(arrow)
            gap = ARROW_START_GAP / multiplier
            self.spawn_timer += self.rng.uniform(gap * 0.6, gap * 1.2)

    def _new_arrow(self, speed: float) -> Arrow | None:
        """Pick a direction and an edge, avoiding spots right next to a fly."""
        for _ in range(8):
            dx, dy = self.rng.choice(DIRECTIONS)
            if dy == 0 or (dx != 0 and self.rng.random() < 0.5):
                x = -1 if dx > 0 else W
                y = self.rng.randrange(H)
            else:
                x = self.rng.randrange(W)
                y = -1 if dy > 0 else H
            if all(abs(p.center[0] - x) >= SPAWN_CLEAR_X or
                   abs(p.center[1] - y) >= SPAWN_CLEAR_Y for p in self.alive()):
                return Arrow(float(x), float(y), speed, dx, dy)
        return None

    def _move_arrows(self, dt: float) -> None:
        kept: list[Arrow] = []
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
                if any(a.in_catch_zone(col, row) for col, row in cells):
                    a.x, a.y = x, y
                    a.homing = True
                    break
                for p in self.alive():
                    if any(p.touches(col, row) for col, row in cells):
                        self._eliminate(p, f"{p.name} got hit by an arrow")
                        self.sad = SAD_SECONDS
            if not a.homing:
                a.x, a.y = new_x, new_y
            if a.homing or not a.off_screen:
                kept.append(a)
        self.arrows = kept

    def _curve_into_mouth(self, a: Arrow, dt: float) -> bool:
        """Move a caught arrow toward the mouth; True once swallowed."""
        to_x, to_y = MOUTH_X - a.x, MOUTH_Y - a.y
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

    def _eliminate(self, p: Player, text: str) -> None:
        p.alive = False
        p.dx = p.dy = 0
        self._post(text)

    def _post(self, text: str) -> None:
        self.feed = text
        self.feed_timer = 2.5

    def _closest_fly(self) -> Player | None:
        # Terminal cells are about twice as tall as they are wide.
        def distance(p: Player) -> float:
            cx, cy = p.center
            return math.hypot(cx - MOUTH_X, (cy - MOUTH_Y) * 2)
        return min(self.alive(), key=distance, default=None)

    def _tip_at(self, progress: float) -> tuple[int, int]:
        sx, sy = self.tongue_spot
        return (round(MOUTH_X + (sx - MOUTH_X) * progress),
                round(MOUTH_Y + (sy - MOUTH_Y) * progress))

    def _tongue_catch(self) -> None:
        tx, ty = self.tongue_tip
        steps = max(abs(tx - MOUTH_X), abs(ty - MOUTH_Y), 1)
        cells = {(round(MOUTH_X + (tx - MOUTH_X) * i / steps),
                  round(MOUTH_Y + (ty - MOUTH_Y) * i / steps))
                 for i in range(steps + 1)}
        cells |= {(tx + cx, ty + cy) for cx, cy in TONGUE_SPLASH}
        for p in self.alive():
            if any(p.touches(c, r) for c, r in cells):
                self._eliminate(p, f"Тимур's tongue caught {p.name}")

    def _tongue_wait(self) -> float:
        return self.rng.uniform(*TONGUE_WAIT)

    def _tongue_prey(self) -> Player | None:
        p = self.players.get(self.tongue_target) if self.tongue_target else None
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
                    self.tongue_target = target.id
                    self.tongue_spot = target.center
                    self.tongue_state = "warn"
                    self.tongue_timer = TONGUE_WARN
        elif self.tongue_state == "warn":
            prey = self._tongue_prey()
            if prey is None:                # target died: pick the next closest
                prey = self._closest_fly()
                self.tongue_target = prey.id if prey else None
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
                self.tongue_tip = (MOUTH_X, MOUTH_Y)
                self.tongue_timer = self._tongue_wait()

    # ---- output ------------------------------------------------------

    def man_face(self) -> str:
        if self.tongue_state in ("extend", "hold", "retract"):
            return "tongue"
        if self.tongue_state == "warn":
            return "warn"
        if self.sad > 0:
            return "sad"
        if self.gulp > 0 or any(a.approaching_man for a in self.arrows):
            return "open"
        return "smile"

    def snapshot(self) -> dict:
        target = self.players.get(self.tongue_target) if self.tongue_target else None
        return {
            "t": "state",
            "phase": self.phase,
            "timer": round(max(0.0, self.timer), 2),
            "round": self.round,
            "boss": {"hp": self.boss_hp, "max_hp": BOSS_MAX_HP,
                     "hit": self.boss_hit > 0},
            "host": self.host_id,
            "message": self.message,
            "feed": self.feed,
            "face": self.man_face(),
            "players": [{
                "id": p.id, "name": p.name, "color": p.color,
                "x": math.floor(p.x * 10) / 10, "y": math.floor(p.y * 10) / 10,
                "alive": p.alive, "connected": p.connected,
            } for p in self.players.values()],
            "arrows": [[math.floor(a.x), math.floor(a.y),
                        DIRECTIONS.index((a.dx, a.dy)) if (a.dx, a.dy) in DIRECTIONS else 0]
                       for a in self.arrows],
            "balls": [[round(b.x, 2), round(b.y, 2), b.color] for b in self.balls],
            "tongue": {
                "state": self.tongue_state,
                "locked": self.tongue_state == "warn" and self.tongue_timer <= TONGUE_LOCK,
                "spot": self.tongue_spot,
                "tip": self.tongue_tip,
                "name": target.name if target else "",
            },
        }
