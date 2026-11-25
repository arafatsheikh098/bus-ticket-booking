"""Live seat maps.

Every API instance keeps one dedicated Postgres connection that LISTENs on the seat
channel, and fans each notification out to the browsers watching that trip. Because the
notification travels through Postgres, a seat changed on instance A reaches viewers
connected to instance B, with no extra broker to run.
"""

import asyncio
import json
import logging
from collections import defaultdict

import asyncpg
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app import metrics
from app.events import SEAT_CHANNEL
from app.services.trips import seat_snapshot

log = logging.getLogger("busticket.realtime")

RESYNC = object()  # Marker telling a client loop to send a fresh full snapshot.


class Subscriber:
    def __init__(self, trip_id: int, maxsize: int = 256):
        self.trip_id = trip_id
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=maxsize)

    def offer(self, item) -> None:
        try:
            self.queue.put_nowait(item)
        except asyncio.QueueFull:
            # A slow client fell behind. Drop its backlog and send one fresh snapshot
            # instead of letting memory grow without limit.
            while not self.queue.empty():
                self.queue.get_nowait()
            self.queue.put_nowait(RESYNC)


class SeatHub:
    def __init__(self, dsn: str):
        self.dsn = dsn
        self._subs: dict[int, set[Subscriber]] = defaultdict(set)
        self._task: asyncio.Task | None = None
        self.ready = asyncio.Event()

    def subscribe(self, trip_id: int) -> Subscriber:
        sub = Subscriber(trip_id)
        self._subs[trip_id].add(sub)
        metrics.WS_CLIENTS.inc()
        return sub

    def unsubscribe(self, sub: Subscriber) -> None:
        subs = self._subs.get(sub.trip_id)
        if subs and sub in subs:
            subs.discard(sub)
            metrics.WS_CLIENTS.dec()
            if not subs:
                del self._subs[sub.trip_id]

    def _on_notify(self, _conn, _pid, _channel, payload: str) -> None:
        try:
            trip_id = json.loads(payload)["trip_id"]
        except (ValueError, KeyError):
            return
        for sub in list(self._subs.get(trip_id, ())):
            sub.offer(payload)

    def _resync_everyone(self) -> None:
        for subs in self._subs.values():
            for sub in subs:
                sub.offer(RESYNC)

    async def start(self) -> None:
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    async def _run(self) -> None:
        backoff, first = 0.5, True
        while True:
            conn = None
            try:
                conn = await asyncpg.connect(self.dsn)
                await conn.add_listener(SEAT_CHANNEL, self._on_notify)
                if not first:
                    # Notifications sent while we were disconnected are lost, so every
                    # open seat map reloads its state.
                    self._resync_everyone()
                first, backoff = False, 0.5
                self.ready.set()
                while True:
                    await asyncio.sleep(5)
                    await conn.fetchval("SELECT 1")  # Notice a dead connection quickly.
            except asyncio.CancelledError:
                raise
            except Exception:
                log.warning("seat listener lost its connection, reconnecting", exc_info=True)
                self.ready.clear()
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 10)
            finally:
                if conn is not None and not conn.is_closed():
                    await conn.close()


router = APIRouter()


async def _snapshot_message(pool, trip_id: int) -> str:
    return json.dumps({"type": "snapshot", "trip_id": trip_id,
                       "seats": await seat_snapshot(pool, trip_id)})


@router.websocket("/api/ws/trips/{trip_id}")
async def trip_socket(ws: WebSocket, trip_id: int):
    pool, hub = ws.app.state.pool, ws.app.state.hub
    exists = await pool.fetchval("SELECT EXISTS (SELECT 1 FROM trips WHERE id = $1)", trip_id)
    if not exists:
        await ws.close(code=4404)
        return
    await ws.accept()
    # Subscribe before taking the snapshot, so no change can slip between the two.
    # A change may arrive twice; applying a seat state twice is harmless.
    sub = hub.subscribe(trip_id)
    try:
        await ws.send_text(await _snapshot_message(pool, trip_id))

        async def pump():
            while True:
                item = await sub.queue.get()
                if item is RESYNC:
                    item = await _snapshot_message(pool, trip_id)
                await ws.send_text(item)

        async def drain():
            while True:
                await ws.receive_text()

        tasks = [asyncio.create_task(pump()), asyncio.create_task(drain())]
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        for task in done:
            if task.exception() and not isinstance(task.exception(), WebSocketDisconnect):
                log.debug("socket closed: %r", task.exception())
    except WebSocketDisconnect:
        pass
    finally:
        hub.unsubscribe(sub)
