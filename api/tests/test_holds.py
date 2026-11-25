from datetime import timedelta

from app.services import holds
from tests.conftest import expire_hold, make_trip, session


async def seat_states(client, trip_id):
    trip = (await client.get(f"/api/trips/{trip_id}")).json()
    return {s["seat_no"]: s["status"] for s in trip["seats"]}


async def test_hold_seats_and_see_them_held(client, trip):
    r = await client.put(f"/api/trips/{trip['id']}/hold", json={"seats": ["A2", "A1"]},
                         headers=session("a"))
    assert r.status_code == 200
    body = r.json()
    assert body["seats"] == ["A1", "A2"] and body["status"] == "active"
    states = await seat_states(client, trip["id"])
    assert states["A1"] == states["A2"] == "held" and states["A3"] == "available"


async def test_second_session_gets_conflict_with_seat_list(client, trip):
    await client.put(f"/api/trips/{trip['id']}/hold", json={"seats": ["B1"]}, headers=session("a"))
    r = await client.put(f"/api/trips/{trip['id']}/hold", json={"seats": ["B1", "B2"]},
                         headers=session("b"))
    assert r.status_code == 409
    assert r.json()["error"] == {"code": "seats_unavailable",
                                 "message": "Some seats were just taken by someone else",
                                 "unavailable": ["B1"]}
    # All or nothing: B2 must not have been taken.
    assert (await seat_states(client, trip["id"]))["B2"] == "available"


async def test_changing_selection_releases_dropped_seats(client, trip):
    url, h = f"/api/trips/{trip['id']}/hold", session("a")
    first = (await client.put(url, json={"seats": ["C1", "C2"]}, headers=h)).json()
    second = (await client.put(url, json={"seats": ["C2", "C3"]}, headers=h)).json()
    assert second["id"] == first["id"]
    assert second["expires_at"] == first["expires_at"], "changing seats must not reset the timer"
    states = await seat_states(client, trip["id"])
    assert (states["C1"], states["C2"], states["C3"]) == ("available", "held", "held")


async def test_empty_selection_releases_hold(client, trip):
    url, h = f"/api/trips/{trip['id']}/hold", session("a")
    await client.put(url, json={"seats": ["D1"]}, headers=h)
    r = await client.put(url, json={"seats": []}, headers=h)
    assert r.status_code == 204
    assert (await seat_states(client, trip["id"]))["D1"] == "available"
    assert (await client.get(url, headers=h)).status_code == 204


async def test_same_request_twice_is_harmless(client, trip):
    url, h = f"/api/trips/{trip['id']}/hold", session("a")
    a = (await client.put(url, json={"seats": ["E1"]}, headers=h)).json()
    b = (await client.put(url, json={"seats": ["E1"]}, headers=h)).json()
    assert a == b


async def test_limits_and_validation(client, trip):
    url = f"/api/trips/{trip['id']}/hold"
    r = await client.put(url, json={"seats": ["A1", "A2", "A3", "A4", "B1"]}, headers=session("a"))
    assert r.status_code == 422 and r.json()["error"]["code"] == "too_many_seats"
    r = await client.put(url, json={"seats": ["Z9"]}, headers=session("a"))
    assert r.status_code == 422 and r.json()["error"]["seats"] == ["Z9"]
    r = await client.put(url, json={"seats": ["A1"]})
    assert r.status_code == 400 and r.json()["error"]["code"] == "session_required"
    r = await client.put("/api/trips/999999/hold", json={"seats": ["A1"]}, headers=session("a"))
    assert r.status_code == 404


async def test_cannot_hold_on_departed_trip(client, pool):
    gone = await make_trip(pool, departs_in=timedelta(minutes=-5))
    r = await client.put(f"/api/trips/{gone['id']}/hold", json={"seats": ["A1"]},
                         headers=session("a"))
    assert r.status_code == 409 and r.json()["error"]["code"] == "trip_departed"


async def test_sweeper_releases_lapsed_hold(client, pool, trip):
    hold = (await client.put(f"/api/trips/{trip['id']}/hold", json={"seats": ["F1", "F2"]},
                             headers=session("a"))).json()
    await expire_hold(pool, hold["id"])
    assert await holds.expire_lapsed_holds(pool) == 1
    states = await seat_states(client, trip["id"])
    assert states["F1"] == states["F2"] == "available"
    status = await pool.fetchval("SELECT status FROM holds WHERE id = $1::uuid", hold["id"])
    assert status == "expired"


async def test_lapsed_seat_can_be_taken_before_sweeper_runs(client, pool, trip):
    url = f"/api/trips/{trip['id']}/hold"
    old = (await client.put(url, json={"seats": ["G1", "G2"]}, headers=session("a"))).json()
    await expire_hold(pool, old["id"])
    # Snapshot already shows the lapsed seats as available.
    assert (await seat_states(client, trip["id"]))["G1"] == "available"
    r = await client.put(url, json={"seats": ["G1"]}, headers=session("b"))
    assert r.status_code == 200
    # When the sweeper finally runs, it frees only G2; G1 now belongs to session b.
    await holds.expire_lapsed_holds(pool)
    states = await seat_states(client, trip["id"])
    assert states["G1"] == "held" and states["G2"] == "available"


async def test_own_lapsed_hold_is_replaced_by_a_fresh_one(client, pool, trip):
    url, h = f"/api/trips/{trip['id']}/hold", session("a")
    old = (await client.put(url, json={"seats": ["H1"]}, headers=h)).json()
    await expire_hold(pool, old["id"])
    new = (await client.put(url, json={"seats": ["H2"]}, headers=h)).json()
    assert new["id"] != old["id"] and new["seats"] == ["H2"]
    assert (await seat_states(client, trip["id"]))["H1"] == "available"


async def test_release_endpoint(client, trip):
    hold = (await client.put(f"/api/trips/{trip['id']}/hold", json={"seats": ["I1"]},
                             headers=session("a"))).json()
    assert (await client.delete(f"/api/holds/{hold['id']}", headers=session("b"))).status_code == 404
    assert (await client.delete(f"/api/holds/{hold['id']}", headers=session("a"))).status_code == 204
    assert (await seat_states(client, trip["id"]))["I1"] == "available"


async def test_seat_names_are_length_limited(client, trip):
    r = await client.put(f"/api/trips/{trip['id']}/hold", json={"seats": ["A" * 500]},
                         headers=session("a"))
    assert r.status_code == 422


async def test_seat_clicks_are_rate_limited_per_session(client, trip, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "rate_limits_enabled", True)
    url, h = f"/api/trips/{trip['id']}/hold", session("spammer")
    codes = [(await client.put(url, json={"seats": ["A1"]}, headers=h)).status_code
             for _ in range(25)]
    assert codes[:20] == [200] * 20
    assert 429 in codes[20:]
    # Another session is unaffected.
    assert (await client.put(url, json={"seats": ["A2"]}, headers=session("other"))).status_code == 200
