"""FastAPI server: private rooms and the WebSocket game loop."""

from __future__ import annotations

import asyncio
import json
import secrets
import time
import unicodedata
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse

from game import TICK, Game

INDEX = Path(__file__).parent / "static" / "index.html"
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 6
MAX_ROOMS = 500
EMPTY_ROOM_TTL = 120.0
JOIN_TIMEOUT = 10.0
MAX_MESSAGE_CHARS = 256
MAX_MESSAGES_PER_SECOND = 120
MAX_NAME_LENGTH = 12

app = FastAPI(title="Last Fly Standing", docs_url=None, redoc_url=None)
ROOMS: dict[str, "Room"] = {}


class Connection:
    """Sends only the newest state, so a slow client cannot stall a room."""

    def __init__(self, ws: WebSocket):
        self.ws = ws
        self.pending = ""
        self.ready = asyncio.Event()

    def push(self, payload: str) -> None:
        self.pending = payload
        self.ready.set()

    async def writer(self) -> None:
        while True:
            await self.ready.wait()
            self.ready.clear()
            await self.ws.send_text(self.pending)


class Room:
    def __init__(self, code: str):
        self.code = code
        self.game = Game()
        self.connections: dict[int, Connection] = {}
        self.task = asyncio.create_task(self.run())

    async def run(self) -> None:
        empty_since: float | None = None
        next_tick = time.monotonic()
        try:
            while True:
                self.game.tick(TICK)
                now = time.monotonic()
                if self.connections:
                    empty_since = None
                    payload = json.dumps(self.game.snapshot(),
                                         separators=(",", ":"), ensure_ascii=False)
                    for connection in self.connections.values():
                        connection.push(payload)
                elif empty_since is None:
                    empty_since = now
                elif now - empty_since > EMPTY_ROOM_TTL:
                    return
                next_tick += TICK
                delay = next_tick - time.monotonic()
                if delay < 0:
                    next_tick = time.monotonic()
                    delay = 0
                await asyncio.sleep(delay)
        finally:
            ROOMS.pop(self.code, None)


def clean_name(value: object) -> str:
    if not isinstance(value, str):
        return ""
    text = "".join(c for c in unicodedata.normalize("NFKC", value)
                   if unicodedata.category(c)[0] != "C")
    return " ".join(text.split())[:MAX_NAME_LENGTH].strip()


def same_origin(ws: WebSocket) -> bool:
    origin = ws.headers.get("origin")
    return origin is None or urlparse(origin).netloc == ws.headers.get("host")


def axis(value: object) -> int:
    return value if type(value) is int and value in (-1, 0, 1) else 0


@app.get("/")
@app.get("/r/{code}")
async def index(code: str | None = None) -> FileResponse:
    return FileResponse(INDEX, headers={"Cache-Control": "no-cache"})


@app.get("/healthz")
async def health() -> dict:
    return {"ok": True, "rooms": len(ROOMS)}


@app.post("/api/rooms", status_code=201)
async def create_room() -> dict:
    if len(ROOMS) >= MAX_ROOMS:
        raise HTTPException(503, "Server is full")
    while True:
        code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
        if code not in ROOMS:
            ROOMS[code] = Room(code)
            return {"code": code}


@app.get("/api/rooms/{code}")
async def room_info(code: str) -> dict:
    room = ROOMS.get(code.upper())
    if room is None:
        raise HTTPException(404, "Room not found")
    return {"code": room.code, "players": len(room.game.players)}


async def refuse(ws: WebSocket, reason: str) -> None:
    await ws.send_text(json.dumps({"t": "error", "reason": reason}))
    await ws.close()


@app.websocket("/ws/{code}")
async def play(ws: WebSocket, code: str) -> None:
    if not same_origin(ws):
        await ws.close(code=1008)
        return
    await ws.accept()
    room = ROOMS.get(code.upper())
    if room is None:
        await refuse(ws, "not_found")
        return

    try:
        raw = await asyncio.wait_for(ws.receive_text(), JOIN_TIMEOUT)
        if len(raw) > MAX_MESSAGE_CHARS:
            raise ValueError
        hello = json.loads(raw)
        if not isinstance(hello, dict) or hello.get("t") != "join":
            raise ValueError
    except (asyncio.TimeoutError, ValueError, KeyError, WebSocketDisconnect):
        await ws.close(code=1008)
        return

    game = room.game
    token = hello.get("token")
    player = game.reconnect(token) if isinstance(token, str) and token else None
    if player is None:
        name = clean_name(hello.get("name"))
        if not name:
            await refuse(ws, "bad_name")
            return
        player = game.add_player(name)

    previous = room.connections.get(player.id)
    connection = Connection(ws)
    room.connections[player.id] = connection
    if previous is not None:
        try:
            await previous.ws.close(code=4000)
        except RuntimeError:
            pass

    writer = None
    try:
        await ws.send_text(json.dumps({
            "t": "welcome", "id": player.id, "token": player.token, "code": room.code,
        }))
        writer = asyncio.create_task(connection.writer())
        window_start, count = time.monotonic(), 0
        while True:
            raw = await ws.receive_text()
            now = time.monotonic()
            if now - window_start >= 1:
                window_start, count = now, 0
            count += 1
            if count > MAX_MESSAGES_PER_SECOND or len(raw) > MAX_MESSAGE_CHARS:
                await ws.close(code=1008)
                break
            try:
                message = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(message, dict):
                continue
            kind = message.get("t")
            if kind == "input":
                game.set_input(player.id, axis(message.get("dx")), axis(message.get("dy")))
            elif kind == "start":
                game.start(player.id)
            elif kind == "attack":
                game.attack(player.id)
    except (WebSocketDisconnect, RuntimeError, KeyError):
        pass
    finally:
        if writer is not None:
            writer.cancel()
        if room.connections.get(player.id) is connection:
            del room.connections[player.id]
            game.disconnect(player.id)
