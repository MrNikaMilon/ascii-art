# AGENTS.md

Use the [README](README.md) for setup, run commands, controls, and multiplayer test commands. On Windows, use `py -3.13` to select the Python version used by CI and Docker.

## Code ownership

- `fly_game.py` is the standalone `curses` game and has no dependency on the multiplayer code.
- `multiplayer/game.py` owns the fixed-timestep simulation and snapshot schema. `Game` manages the shared roster, host, and mode rotation. Each `Arena` owns its mode's gameplay state.
- `multiplayer/app.py` serves the client at `/` and `/r/{code}`, manages rooms through `/api/rooms`, runs each room's tick loop, and broadcasts snapshots over `/ws/{code}`.
- `multiplayer/static/index.html` contains the UI, input handling, client-side prediction, and canvas rendering. The browser loads it directly with no build step.
- `multiplayer/tests/test_game.py` covers simulation rules and the room and WebSocket flows.

## Multiplayer contracts

- Keep gameplay decisions on the server. For movement or arena changes, keep `move_fly()` and the browser's `moveFly()` aligned, including sprite bounds and collision geometry. Keep server `DIRECTIONS` and browser `ARROWS` in the same order because snapshots carry the direction index.
- Update the browser and relevant tests when changing WebSocket messages or snapshot fields. Send reconnect tokens only in the private `welcome` message. The client stores them in `sessionStorage` per room, and broadcast snapshots must exclude them.

## Verification

- For terminal game changes, run `python -m py_compile fly_game.py` as CI does and check gameplay in an interactive terminal with `curses` support.
- Write deterministic simulation tests with a seeded `random.Random` and advance `Game.tick()` directly. Use FastAPI's `TestClient` for room and WebSocket behavior.
- For browser or WebSocket changes, use two tabs to verify room creation, joining by invite link, and gameplay input. For session changes, verify refresh and reconnect in the original tab.

## Dependencies and deployment

- Put runtime dependencies in `multiplayer/requirements.txt` and test-only dependencies in `multiplayer/requirements-dev.txt`. Keep `.test-runtime/` local.
- Compose runs Uvicorn behind Caddy and checks `/healthz` before starting the proxy. Keep one Uvicorn worker while rooms live in process memory.
- When adding or moving a runtime file, check the copy lists in `multiplayer/Dockerfile` and `multiplayer/deploy.sh` and the allowlist in `multiplayer/.dockerignore`. Running `multiplayer/deploy.sh` restarts the remote service and ends active games.
