import asyncio
import json

from app.realtime import RESYNC, SeatHub, Subscriber
from app.services import holds
from tests.conftest import TEST_DB


async def next_message(sub, timeout=3):
    return json.loads(await asyncio.wait_for(sub.queue.get(), timeout))


async def test_viewers_of_a_trip_receive_seat_changes(pool, trip):
    hub = SeatHub(TEST_DB)
    await hub.start()
    try:
        await asyncio.wait_for(hub.ready.wait(), 5)
        viewer = hub.subscribe(trip["id"])
        other_trip_viewer = hub.subscribe(trip["id"] + 1000)

        hold = await holds.set_hold(pool, trip["id"], "viewer-test", ["A1", "A2"])
        msg = await next_message(viewer)
        assert msg["type"] == "seats" and msg["trip_id"] == trip["id"]
        assert [(s["seat_no"], s["status"], s["hold_id"]) for s in msg["seats"]] == [
            ("A1", "held", hold["id"]), ("A2", "held", hold["id"])]

        await holds.set_hold(pool, trip["id"], "viewer-test", [])
        msg = await next_message(viewer)
        assert {s["status"] for s in msg["seats"]} == {"available"}
        assert other_trip_viewer.queue.empty()
    finally:
        await hub.stop()


async def test_rolled_back_change_is_never_broadcast(pool, trip):
    hub = SeatHub(TEST_DB)
    await hub.start()
    try:
        await asyncio.wait_for(hub.ready.wait(), 5)
        viewer = hub.subscribe(trip["id"])
        await holds.set_hold(pool, trip["id"], "first", ["A1"])
        await next_message(viewer)
        try:
            await holds.set_hold(pool, trip["id"], "second", ["A1"])  # Conflict, rolled back.
        except Exception:
            pass
        await asyncio.sleep(0.3)
        assert viewer.queue.empty()
    finally:
        await hub.stop()


def test_slow_viewer_gets_a_resync_instead_of_unbounded_backlog():
    sub = Subscriber(trip_id=1, maxsize=3)
    for i in range(10):
        sub.offer(f"msg-{i}")
    items = [sub.queue.get_nowait() for _ in range(sub.queue.qsize())]
    assert RESYNC in items and len(items) <= 3
