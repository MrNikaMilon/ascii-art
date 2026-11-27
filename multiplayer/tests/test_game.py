import math
import random
import sys
from contextlib import ExitStack
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parents[1]))

import game as g  # noqa: E402
from app import ROOMS, app, clean_name  # noqa: E402


def started(n=2):
    game = g.Game(random.Random(1))
    players = [game.add_player(f"P{i}") for i in range(n)]
    assert game.start(players[0].id)
    for _ in range(int(g.COUNTDOWN_SECONDS / g.TICK) + 2):
        game.tick()
    assert game.phase == g.PLAYING
    game.spawn_timer = 10**9
    game.tongue_timer = 10**9
    return game, players


def test_only_host_starts():
    game = g.Game()
    host = game.add_player("A")
    other = game.add_player("B")
    assert not game.start(other.id)
    assert game.start(host.id)


def test_room_accepts_more_than_six_players_with_unique_names_and_colors():
    game = g.Game()
    players = [game.add_player("LongNickname") for _ in range(30)]
    assert len({p.name for p in players}) == 30
    assert all(len(p.name) <= 12 for p in players)
    assert len({p.color for p in players}) == 30
    assert game.start(players[0].id)
    assert len(game.alive()) == 30


@pytest.mark.parametrize("n", [1, 2, 3, 6, 7, 30])
def test_players_spawn_evenly_on_both_sides_of_centered_man(n):
    game = g.Game()
    players = [game.add_player(f"P{i}") for i in range(n)]
    assert game.start(players[0].id)
    left = [p for p in players if p.x + g.FLY_W <= g.MAN_X]
    right = [p for p in players if p.x >= g.MAN_X + g.MAN_W]
    assert len(left) == n // 2 and len(right) == (n + 1) // 2
    assert len(left) + len(right) == n
    assert abs(g.MAN_X + g.MAN_W / 2 - g.W / 2) <= 0.5
    assert abs(g.MAN_Y + g.MAN_H / 2 - g.H / 2) <= 0.5
    assert all(0 <= p.x <= g.W - g.FLY_W and
               0 <= p.y <= g.H - g.FLY_H for p in players)


@pytest.mark.parametrize("y", [0, g.MAN_Y - g.FLY_H, g.MAN_Y + g.MAN_H, g.H - g.FLY_H])
def test_flies_can_cross_entire_arena_above_and_below_man(y):
    x, row = g.move_fly(0, y, 1, 0, g.W / g.FLY_SPEED_X)
    assert x == g.W - g.FLY_W and row == y
    x, row = g.move_fly(x, row, -1, 0, g.W / g.FLY_SPEED_X)
    assert x == 0 and row == y


@pytest.mark.parametrize("x", [0, g.MAN_X - g.FLY_W, g.MAN_X + g.MAN_W, g.W - g.FLY_W])
def test_flies_can_cross_entire_arena_on_either_side_of_man(x):
    col, y = g.move_fly(x, 0, 0, 1, g.H / g.FLY_SPEED_Y)
    assert col == x and y == g.H - g.FLY_H
    col, y = g.move_fly(col, y, 0, -1, g.H / g.FLY_SPEED_Y)
    assert col == x and y == 0


@pytest.mark.parametrize("start,direction,expected", [
    ((0, g.MAN_Y), (1, 0), (g.MAN_X - g.FLY_W, g.MAN_Y)),
    ((g.W - g.FLY_W, g.MAN_Y), (-1, 0), (g.MAN_X + g.MAN_W, g.MAN_Y)),
    ((g.MAN_X, 0), (0, 1), (g.MAN_X, g.MAN_Y - g.FLY_H)),
    ((g.MAN_X, g.H - g.FLY_H), (0, -1), (g.MAN_X, g.MAN_Y + g.MAN_H)),
])
def test_flies_cannot_enter_man_from_any_side(start, direction, expected):
    assert g.move_fly(*start, *direction, 10.0) == expected


def test_solo_attempt_runs_until_hit_then_restarts_with_full_boss_health():
    game, (player,) = started(1)
    game.set_input(player.id, 1, 0)
    old_x = player.x
    for _ in range(30):
        game.tick()
    assert game.phase == g.PLAYING and player.alive and player.x > old_x
    assert game.boss_hp == g.BOSS_MAX_HP
    game.boss_hp = 42
    game.arrows = [g.Arrow(player.x + 2, player.y + 1, 30.0)]
    game.tick()
    assert game.phase == g.ROUND_OVER and not player.alive
    assert game.message.startswith("All flies are down!")
    for _ in range(int(g.ROUND_OVER_SECONDS / g.TICK) + 2):
        game.tick()
    assert game.phase == g.COUNTDOWN and game.round == 2 and player.alive
    assert game.boss_hp == g.BOSS_MAX_HP and not game.balls


def test_player_joining_solo_enters_multiplayer_next_round():
    game, (host,) = started(1)
    guest = game.add_player("Guest")
    game.tick()
    assert game.phase == g.PLAYING and host.alive and not guest.alive
    game._eliminate(host, "hit")
    game.tick()
    for _ in range(int(g.ROUND_OVER_SECONDS / g.TICK) + 2):
        game.tick()
    assert game.phase == g.COUNTDOWN and host.alive and guest.alive


def test_solo_disconnect_can_rejoin_next_attempt():
    game, (player,) = started(1)
    game.disconnect(player.id)
    game.tick()
    assert game.phase == g.ROUND_OVER and game.boss_hp == g.BOSS_MAX_HP
    assert game.reconnect(player.token) is player
    for _ in range(int(g.ROUND_OVER_SECONDS / g.TICK) + 2):
        game.tick()
    assert game.phase == g.COUNTDOWN and player.alive


def test_fast_arrow_cannot_skip_a_fly():
    game, (a, b) = started()
    a.x, a.y = 50.0, 5.0
    b.x, b.y = 80.0, 20.0
    game.arrows = [g.Arrow(60.0, 6, 1000.0)]
    game.tick()
    assert not a.alive and b.alive
    assert game.phase == g.PLAYING and game.boss_hp == g.BOSS_MAX_HP


@pytest.mark.parametrize("speed", [18.0, 40.0, 70.0])
@pytest.mark.parametrize("row", range(g.MAN_TOP, g.MAN_BOTTOM + 1))
def test_man_catches_arrows_at_every_row(speed, row):
    game, (a, b) = started()
    a.x, a.y = b.x, b.y = g.W - g.FLY_W - 1, 0.0
    b.y = g.H - g.FLY_H
    game.arrows = [g.Arrow(float(g.MAN_X + g.MAN_W + 16), row, speed)]
    saw_open = False
    for _ in range(200):
        game.tick()
        saw_open |= game.man_face() == "open"
        if not game.arrows:
            break
    assert not game.arrows and saw_open and a.alive and b.alive


@pytest.mark.parametrize("direction", g.DIRECTIONS)
def test_arrow_hits_fly_from_every_direction(direction):
    game, (a, b) = started()
    a.x, a.y = 60.0, 14.0
    b.x, b.y = g.W - g.FLY_W - 1, 0.0 if direction[1] >= 0 else g.H - g.FLY_H
    dx, dy = direction
    cx, cy = a.center
    game.arrows = [g.Arrow(float(cx - dx * 10), float(cy - dy * 10), 30.0, dx, dy)]
    for _ in range(60):
        game.tick()
        if not a.alive:
            break
    assert not a.alive


@pytest.mark.parametrize("direction", g.DIRECTIONS)
def test_arrows_leave_the_arena(direction):
    game, (a, b) = started()
    a.x, a.y = 90.0, 0.0
    b.x, b.y = 90.0, 26.0
    dx, dy = direction
    game.arrows = [g.Arrow(50.0, 15.0, 40.0, dx, dy)]
    for _ in range(300):
        game.tick()
        if not game.arrows or game.phase != g.PLAYING:
            break
    assert not game.arrows or game.phase != g.PLAYING


def test_arrow_from_above_is_caught_by_man():
    game, (a, b) = started()
    a.x, a.y = 90.0, 0.0
    b.x, b.y = 90.0, 26.0
    game.arrows = [g.Arrow(float(g.MOUTH_X), -1.0, 30.0, 0, 1)]
    saw_open = False
    for _ in range(120):
        game.tick()
        saw_open |= game.man_face() == "open"
        if not game.arrows:
            break
    assert not game.arrows and saw_open and a.alive and b.alive


def test_spawns_cover_all_directions_and_stay_clear():
    game, (a, b) = started()
    a.x, a.y = 60.0, 5.0
    b.x, b.y = 40.0, 20.0
    seen = set()
    for _ in range(2000):
        arrow = game._new_arrow(30.0)
        if arrow is None:
            continue
        seen.add((arrow.dx, arrow.dy))
        for p in (a, b):
            assert (abs(p.center[0] - arrow.x) >= g.SPAWN_CLEAR_X or
                    abs(p.center[1] - arrow.y) >= g.SPAWN_CLEAR_Y)
        assert not g.Arrow.in_catch_zone(arrow.x, arrow.y)
    assert seen == set(g.DIRECTIONS)


@pytest.mark.parametrize("direction", g.DIRECTIONS)
def test_centered_man_catches_arrows_from_every_direction(direction):
    game, players = started()
    players[0].x, players[0].y = 0.0, 0.0
    players[1].x, players[1].y = g.W - g.FLY_W, g.H - g.FLY_H
    dx, dy = direction
    game.arrows = [g.Arrow(float(g.MOUTH_X - dx * 25),
                          float(g.MOUTH_Y - dy * 25), 60.0, dx, dy)]
    for _ in range(180):
        game.tick()
        if not game.arrows:
            break
    assert not game.arrows and all(p.alive for p in players)


@pytest.mark.parametrize("dx", [-1, 1])
def test_man_intercepts_fast_arrows_before_they_hit_players_behind_him(dx):
    game, (behind, other) = started()
    behind.x = float(g.MAN_X + g.MAN_W + 10 if dx > 0 else g.MAN_X - g.FLY_W - 10)
    behind.y = float(g.MOUTH_Y - 1)
    other.x, other.y = 0.0, 0.0
    start_x = float(g.MAN_X - 10 if dx > 0 else g.MAN_X + g.MAN_W + 10)
    game.arrows = [g.Arrow(start_x, float(g.MOUTH_Y), 10000.0, dx, 0)]
    game.tick()
    assert behind.alive and other.alive and game.arrows[0].homing


def test_catch_zone_is_limited_to_man_body():
    assert g.Arrow.in_catch_zone(g.MOUTH_X, g.MOUTH_Y)
    assert not g.Arrow.in_catch_zone(g.MAN_X - 1, g.MOUTH_Y)
    assert not g.Arrow.in_catch_zone(g.MAN_X + g.MAN_W, g.MOUTH_Y)
    assert not g.Arrow.in_catch_zone(g.MOUTH_X, g.MAN_TOP - 1)
    assert not g.Arrow.in_catch_zone(g.MOUTH_X, g.MAN_BOTTOM + 1)


def strike(game, near, move, react_delay=0.0):
    """Run one tongue attack; the fly starts moving react_delay after the lock."""
    game.tongue_timer = 0
    game.tick()
    assert game.tongue_state == "warn" and game.tongue_target == near.id
    since_lock = None
    for _ in range(60):
        if game.tongue_state == "warn" and game.tongue_timer <= g.TONGUE_LOCK:
            since_lock = 0.0 if since_lock is None else since_lock
        if since_lock is not None:
            if since_lock >= react_delay and near.alive:
                near.dx, near.dy = move
            since_lock += g.TICK
        game.tick()
        if game.phase != g.PLAYING or game.tongue_state == "idle":
            break


@pytest.mark.parametrize("start,move", [
    ((19.0, -1.0), (1, 0)),     # offsets from the mouth: sprint horizontally
    ((29.0, 0.0), (0, 1)),      # sprint away vertically
    ((29.0, -5.0), (1, -1)),    # sprint away diagonally
])
def test_tongue_can_be_escaped_by_moving_at_the_lock(start, move):
    game, (near, far) = started()
    near.x, near.y = g.MOUTH_X + start[0], g.MOUTH_Y + start[1]
    far.x, far.y = g.W - g.FLY_W - 1, 0.0 if move[1] >= 0 else g.H - g.FLY_H
    strike(game, near, move)
    assert near.alive and far.alive


def test_tongue_catches_a_late_reaction():
    game, (near, far) = started()
    near.x, near.y = g.MOUTH_X + 19.0, g.MOUTH_Y - 1.0
    far.x, far.y = g.W - g.FLY_W - 1, g.H - g.FLY_H
    strike(game, near, (1, 0), react_delay=0.3)
    assert not near.alive and far.alive and game.phase == g.PLAYING


def test_aim_tracks_target_until_lock():
    game, (near, far) = started()
    near.x, near.y = g.MOUTH_X + 19.0, g.MOUTH_Y - 1.0
    far.x, far.y = g.W - g.FLY_W - 1, g.H - g.FLY_H
    game.tongue_timer = 0
    game.tick()
    near.x = g.MOUTH_X + 29.0
    game.tick()
    assert game.tongue_spot == near.center
    while game.tongue_timer > g.TONGUE_LOCK:
        game.tick()
    locked = game.tongue_spot
    near.y = 2.0
    game.tick()
    assert game.tongue_spot == locked and game.snapshot()["tongue"]["locked"]


@pytest.mark.parametrize("elapsed", [0, 30, 60, 120, 10000])
def test_tongue_keeps_initial_wait_and_warning_throughout_fight(elapsed):
    game, _ = started()
    game.elapsed = elapsed
    game.rng.seed(7)
    waits = [game._tongue_wait() for _ in range(200)]
    initial_rng = random.Random(7)
    assert waits == [initial_rng.uniform(4.0, 7.0) for _ in range(200)]
    game.tongue_timer = 0
    game.tick()
    assert game.tongue_state == "warn"
    assert game.tongue_timer == pytest.approx(0.6)


def test_arrows_ramp_smoothly_over_two_minutes_and_stop_at_double_difficulty():
    speeds, gaps = [], []
    for elapsed in [0, 1, 30, 60, 90, 119, 120, 600, 10000]:
        game = g.Game(random.Random(7))
        game.elapsed = elapsed
        game.spawn_timer = 0
        game._spawn_arrows(0)
        assert len(game.arrows) == 1
        speeds.append(game.arrows[0].speed)
        gaps.append(game.spawn_timer)
    assert speeds == sorted(speeds)
    assert gaps == sorted(gaps, reverse=True)
    assert speeds[3] == pytest.approx(speeds[0] * 1.5)
    assert gaps[3] == pytest.approx(gaps[0] / 1.5)
    for speed, gap in zip(speeds[6:], gaps[6:]):
        assert speed == pytest.approx(speeds[0] * 2)
        assert gap == pytest.approx(gaps[0] / 2)
    # Growth starts and ends gently, without a sudden jump in difficulty.
    assert speeds[1] - speeds[0] < (speeds[3] - speeds[2]) / 30
    assert speeds[6] - speeds[5] < (speeds[4] - speeds[3]) / 30


def test_tongue_catches_fly_that_stays():
    game, (near, far) = started()
    near.x, near.y = g.MOUTH_X + 19.0, g.MOUTH_Y - 1.0
    far.x, far.y = g.W - g.FLY_W - 1, g.H - g.FLY_H
    game.tongue_timer = 0
    for _ in range(45):
        game.tick()
        if game.phase != g.PLAYING:
            break
    assert not near.alive and far.alive and game.phase == g.PLAYING


def test_simultaneous_elimination_starts_a_new_attempt():
    game, (a, b) = started()
    a.x, a.y = 50.0, 5.0
    b.x, b.y = 50.0, 5.0
    game.arrows = [g.Arrow(52.0, 6, 30.0)]
    game.tick()
    assert game.phase == g.ROUND_OVER and game.boss_hp > 0
    round_number = game.round
    for _ in range(int(g.ROUND_OVER_SECONDS / g.TICK) + 2):
        game.tick()
    assert game.phase == g.COUNTDOWN and game.round == round_number + 1


def test_last_surviving_player_keeps_fighting_boss():
    game, players = started(3)
    game._eliminate(players[1], "x")
    game._eliminate(players[2], "x")
    game.tick()
    assert game.phase == g.PLAYING and players[0].alive and game.boss_hp > 0
    assert game.attack(players[0].id)


def test_disconnect_loses_round_then_rejoins_next_round():
    game, players = started(3)
    gone = players[2]
    game.disconnect(gone.id)
    assert not gone.alive
    assert game.reconnect(gone.token) is gone
    game._eliminate(players[1], "x")
    game._eliminate(players[0], "x")
    game.tick()
    for _ in range(int(g.ROUND_OVER_SECONDS / g.TICK) + 2):
        game.tick()
    assert game.phase == g.COUNTDOWN and gone.alive


def test_refresh_during_countdown_keeps_the_match():
    game = g.Game(random.Random(1))
    a, b = game.add_player("A"), game.add_player("B")
    assert game.start(a.id)
    game.disconnect(b.id)
    game.tick()
    assert game.phase == g.COUNTDOWN and a.alive and not b.alive
    assert game.reconnect(b.token) is b
    for _ in range(int(g.COUNTDOWN_SECONDS / g.TICK) + 2):
        game.tick()
    assert game.phase == g.PLAYING and a.alive and not b.alive
    game._eliminate(a, "x")
    game.tick()
    for _ in range(int(g.ROUND_OVER_SECONDS / g.TICK) + 2):
        game.tick()
    assert game.phase == g.COUNTDOWN and a.alive and b.alive


def test_host_moves_when_host_leaves():
    game = g.Game()
    a, b = game.add_player("A"), game.add_player("B")
    game.disconnect(a.id)
    assert game.host_id == b.id and a.id not in game.players


def test_clean_name_strips_control_chars():
    assert clean_name("  Ти\x00мур\n ") == "Тимур"
    assert len(clean_name("x" * 50)) == 12
    assert clean_name(123) == ""


def test_websocket_room_flow_hides_tokens():
    with TestClient(app) as client:
        code = client.post("/api/rooms").json()["code"]
        with client.websocket_connect(f"/ws/{code}") as one, \
                client.websocket_connect(f"/ws/{code}") as two:
            one.send_json({"t": "join", "name": "Ana"})
            first = one.receive_json()
            two.send_json({"t": "join", "name": "Max"})
            second = two.receive_json()
            assert first["t"] == second["t"] == "welcome"
            two.send_json({"t": "start"})   # not the host: ignored
            one.send_json({"t": "start"})
            for _ in range(60):
                state = one.receive_json()
                if state["phase"] == "countdown":
                    break
            assert state["phase"] == "countdown"
            assert first["token"] not in str(state) and second["token"] not in str(state)
        ROOMS[code].task.cancel()


def test_websocket_host_can_start_solo():
    with TestClient(app) as client:
        code = client.post("/api/rooms").json()["code"]
        with client.websocket_connect(f"/ws/{code}") as ws:
            ws.send_json({"t": "join", "name": "Solo"})
            assert ws.receive_json()["t"] == "welcome"
            ws.send_json({"t": "start"})
            for _ in range(60):
                state = ws.receive_json()
                if state["phase"] == "countdown":
                    break
            assert state["phase"] == "countdown"
            assert len(state["players"]) == 1
        ROOMS[code].task.cancel()


def test_websocket_room_accepts_more_than_six_players():
    with TestClient(app) as client:
        code = client.post("/api/rooms").json()["code"]
        with ExitStack() as stack:
            for _ in range(8):
                ws = stack.enter_context(client.websocket_connect(f"/ws/{code}"))
                ws.send_json({"t": "join", "name": "SameName"})
                assert ws.receive_json()["t"] == "welcome"
            for _ in range(60):
                state = ws.receive_json()
                if len(state["players"]) == 8:
                    break
            assert len(state["players"]) == 8
        ROOMS[code].task.cancel()


def test_attack_cooldown_is_half_a_second_and_spam_is_ignored():
    game, (player,) = started(1)
    assert game.attack(player.id)
    for _ in range(100):
        assert not game.attack(player.id)
    assert len(game.balls) == 1 and game.boss_hp == 300
    for _ in range(14):
        game.tick()
    assert not game.attack(player.id)
    game.tick()
    assert game.attack(player.id)


def test_attack_cooldown_is_independent_for_each_player_and_survives_reconnect():
    game, (a, b) = started()
    assert game.attack(a.id) and game.attack(b.id)
    assert len(game.balls) == 2
    assert game.reconnect(a.token) is a
    assert not game.attack(a.id)


@pytest.mark.parametrize("phase", [g.LOBBY, g.COUNTDOWN, g.ROUND_OVER, g.MATCH_OVER])
def test_attack_is_rejected_outside_combat(phase):
    game, (player,) = started(1)
    game.phase = phase
    assert not game.attack(player.id)
    assert not game.balls and game.boss_hp == 300


def test_dead_disconnected_and_unknown_players_cannot_attack():
    game, (a, b) = started()
    a.alive = False
    b.connected = False
    assert not game.attack(a.id)
    assert not game.attack(b.id)
    assert not game.attack(99999)
    assert not game.balls


@pytest.mark.parametrize("start", [
    (20, 28), (170, 28), (96, 2), (96, 50),
    (20, 2), (170, 2), (20, 50), (170, 50),
])
def test_ball_flies_toward_boss_from_every_side_and_deals_one_damage(start):
    game, (player,) = started(1)
    player.x, player.y = start
    assert game.attack(player.id)
    ball = game.balls[0]
    x, y = ball.x, ball.y
    distance_before = math.hypot(x - (g.MAN_X + g.MAN_W / 2),
                                 (y - (g.MAN_Y + g.MAN_H / 2)) * 2)
    game.tick()
    distance_after = math.hypot(ball.x - (g.MAN_X + g.MAN_W / 2),
                                (ball.y - (g.MAN_Y + g.MAN_H / 2)) * 2)
    assert distance_after < distance_before
    for _ in range(180):
        game.tick()
        if not game.balls:
            break
    assert not game.balls and game.boss_hp == 299
    assert player.alive and game.phase == g.PLAYING
    for _ in range(30):
        game.tick()
    assert game.boss_hp == 299


def test_fast_ball_cannot_skip_boss_and_does_not_damage_other_players():
    game, (a, b) = started()
    b.x, b.y = 50.0, float(g.MOUTH_Y - 1)
    game.balls = [g.Ball(20.0, float(g.MOUTH_Y), 10000.0, 0.0, a.color)]
    game.tick()
    assert game.boss_hp == 299 and not game.balls and a.alive and b.alive


def test_three_hundred_hits_win_and_rematch_resets_health_projectiles_and_cooldowns():
    game, (player,) = started(1)
    for _ in range(300):
        assert game.attack(player.id)
        for _ in range(15):
            game.tick()
    for _ in range(120):
        game.tick()
        if game.phase == g.MATCH_OVER:
            break
    assert game.boss_hp == 0 and game.phase == g.MATCH_OVER
    assert game.message == "Victory! Тимур defeated!"
    assert not game.attack(player.id) and not game.balls and not game.arrows
    assert game.snapshot()["boss"]["hp"] == 0
    assert game.start(player.id)
    assert game.boss_hp == 300 and not game.balls
    for _ in range(int(g.COUNTDOWN_SECONDS / g.TICK) + 2):
        game.tick()
    assert game.attack(player.id)


def test_simultaneous_final_hits_cannot_reduce_boss_health_below_zero():
    game, (a, b) = started()
    game.boss_hp = 1
    game.balls = [g.Ball(float(g.MOUTH_X), float(g.MOUTH_Y), 1.0, 0.0, p.color)
                  for p in (a, b)]
    game.tick()
    assert game.boss_hp == 0 and game.phase == g.MATCH_OVER and not game.balls


def test_websocket_attack_uses_server_cooldown_and_publishes_boss_health():
    with TestClient(app) as client:
        code = client.post("/api/rooms").json()["code"]
        with client.websocket_connect(f"/ws/{code}") as ws:
            ws.send_json({"t": "join", "name": "Shooter"})
            welcome = ws.receive_json()
            game = ROOMS[code].game
            assert game.start(welcome["id"])
            game.timer = 0.0
            game.tick()
            game.spawn_timer = game.tongue_timer = 10**9
            for _ in range(10):
                ws.send_json({"t": "attack", "damage": 1000})
            saw_ball = False
            for _ in range(120):
                state = ws.receive_json()
                saw_ball |= bool(state["balls"])
                if state["boss"]["hp"] < 300:
                    break
            assert saw_ball and state["boss"]["hp"] == 299
            assert state["boss"]["max_hp"] == 300
        ROOMS[code].task.cancel()


def test_unknown_room_is_refused():
    with TestClient(app) as client:
        with client.websocket_connect("/ws/ZZZZZZ") as ws:
            assert ws.receive_json() == {"t": "error", "reason": "not_found"}
