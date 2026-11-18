import asyncio
from typing import Any
from uuid import uuid4

import httpx
import pytest

from tests.conftest import Tracker

JSON = dict[str, Any]


async def write(
    client: httpx.AsyncClient,
    path: str,
    body: JSON | None = None,
    method: str = "POST",
    key: str | None = None,
) -> httpx.Response:
    return await client.request(
        method, "/api/v1" + path, json=body, headers={"Idempotency-Key": key or str(uuid4())}
    )


async def imported(client: httpx.AsyncClient) -> JSON:
    response = await write(client, "/campaigns/101/fetch")
    assert response.status_code == 200, response.text
    return response.json()["streams"][1]  # type: ignore[no-any-return]


async def change(
    client: httpx.AsyncClient, stream: JSON, action: str, offer: int, share: int | None = None
) -> JSON:
    response = await write(
        client,
        "/streams/502/offers",
        {
            "revision": stream["revision"],
            "action": action,
            "offer_id": offer,
            "share": share,
        },
        "PATCH",
    )
    assert response.status_code == 200, response.text
    return response.json()  # type: ignore[no-any-return]


async def test_add_is_local_persistent_and_cancel_restores_everything(
    client: httpx.AsyncClient, tracker: Tracker
) -> None:
    stream = await imported(client)
    draft = await change(client, stream, "add", 14)
    assert draft["dirty"] and [x["share"] for x in draft["offers"]] == [25] * 4
    assert len(tracker.streams[502]["offers"]) == 3
    refreshed = (await client.get("/api/v1/campaigns/101")).json()
    assert refreshed["streams"][1] == draft
    response = await write(client, "/streams/502/cancel", {"revision": draft["revision"]})
    assert response.status_code == 200
    assert response.json()["offers"] == stream["offers"]
    assert not response.json()["dirty"]


async def test_remove_push_archive_restore_and_pin(
    client: httpx.AsyncClient, tracker: Tracker
) -> None:
    stream = await imported(client)
    original_filters = tracker.streams[502]["filters"]
    original_landings = tracker.streams[502]["landings"]
    draft = await change(client, stream, "remove", 12)
    assert next(x for x in draft["offers"] if x["offer_id"] == 12)["share"] == 0
    pushed = await write(client, "/streams/502/push", {"revision": draft["revision"]})
    assert pushed.status_code == 200
    assert len(tracker.streams[502]["offers"]) == 2
    assert len(pushed.json()["offers"]) == 3
    assert tracker.streams[502]["filters"] == original_filters
    assert tracker.streams[502]["landings"] == original_landings
    pinned = await change(client, pushed.json(), "pin", 11, 25)
    restored = await change(client, pinned, "restore", 12)
    assert [x["share"] for x in restored["offers"]] == [25, 38, 37]
    published = await write(client, "/streams/502/push", {"revision": restored["revision"]})
    assert published.status_code == 200
    assert [x["share"] for x in tracker.streams[502]["offers"]] == [25, 38, 37]


async def test_fetch_archives_external_deletion(
    client: httpx.AsyncClient, tracker: Tracker
) -> None:
    await imported(client)
    tracker.streams[502]["offers"] = [{"offer_id": 11, "share": 100, "state": "active"}]
    stream = await imported(client)
    assert stream["total"] == 100
    assert {x["offer_id"] for x in stream["offers"] if x["removed"]} == {12, 13}


async def test_external_change_blocks_push_without_losing_draft(
    client: httpx.AsyncClient, tracker: Tracker
) -> None:
    stream = await imported(client)
    draft = await change(client, stream, "add", 14)
    tracker.streams[502]["offers"][0]["share"] = 60
    response = await write(client, "/streams/502/push", {"revision": draft["revision"]})
    assert response.status_code == 409
    assert not any(method == "PUT" for method, _, _ in tracker.calls)
    local = (await client.get("/api/v1/campaigns/101")).json()["streams"][1]
    assert local == draft


async def test_lost_push_response_is_reconciled_on_retry(
    client: httpx.AsyncClient, tracker: Tracker
) -> None:
    stream = await imported(client)
    draft = await change(client, stream, "add", 14)
    tracker.timeout_put = True
    first = await write(client, "/streams/502/push", {"revision": draft["revision"]})
    assert first.status_code == 504
    assert len(tracker.streams[502]["offers"]) == 4
    retry = await write(client, "/streams/502/push", {"revision": draft["revision"]})
    assert retry.status_code == 200 and not retry.json()["dirty"]
    assert sum(method == "PUT" for method, _, _ in tracker.calls) == 1


async def test_dirty_fetch_and_stale_revision_are_rejected(client: httpx.AsyncClient) -> None:
    stream = await imported(client)
    await change(client, stream, "remove", 11)
    fetch = await write(client, "/campaigns/101/fetch")
    assert fetch.status_code == 409
    stale = await write(
        client,
        "/streams/502/offers",
        {"revision": stream["revision"], "action": "add", "offer_id": 14},
        "PATCH",
    )
    assert stale.status_code == 409


async def test_idempotency_replays_same_operation_and_rejects_other_payload(
    client: httpx.AsyncClient,
) -> None:
    stream = await imported(client)
    body = {"revision": stream["revision"], "action": "add", "offer_id": 14}
    key = str(uuid4())
    first = await write(client, "/streams/502/offers", body, "PATCH", key)
    second = await write(client, "/streams/502/offers", body, "PATCH", key)
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    body["offer_id"] = 11
    conflict = await write(client, "/streams/502/offers", body, "PATCH", key)
    assert conflict.status_code == 409


async def test_empty_and_impossible_pinned_states_do_not_replace_draft(
    client: httpx.AsyncClient,
) -> None:
    stream = await imported(client)
    stream = await change(client, stream, "remove", 12)
    stream = await change(client, stream, "remove", 13)
    response = await write(
        client,
        "/streams/502/offers",
        {"revision": stream["revision"], "action": "remove", "offer_id": 11},
        "PATCH",
    )
    assert response.status_code == 422
    local = (await client.get("/api/v1/campaigns/101")).json()["streams"][1]
    assert local == stream


async def test_no_offer_stream_is_visible_and_unchanged(client: httpx.AsyncClient) -> None:
    await imported(client)
    data = (await client.get("/api/v1/campaigns/101")).json()
    assert data["streams"][0]["offers"] == []
    response = await write(
        client,
        "/streams/501/offers",
        {"revision": data["streams"][0]["revision"], "action": "add", "offer_id": 14},
        "PATCH",
    )
    assert response.status_code == 422


@pytest.mark.parametrize(
    "payload",
    [
        {
            "name": "sample",
            "country": "ZZ",
            "offer_id": 11,
            "domain_id": 1,
            "group_id": 1,
            "traffic_source_id": 1,
        },
        {
            "name": "sample",
            "country": "AU",
            "offer_id": -1,
            "domain_id": 1,
            "group_id": 1,
            "traffic_source_id": 1,
        },
        {"name": "sample"},
    ],
)
async def test_invalid_creation_is_rejected(
    client: httpx.AsyncClient, tracker: Tracker, payload: JSON
) -> None:
    response = await write(client, "/campaigns", payload)
    assert response.status_code == 422
    assert response.headers["content-type"] == "application/problem+json"
    assert not tracker.calls


async def test_creation_has_correct_references_filters_and_two_flows(
    client: httpx.AsyncClient, tracker: Tracker
) -> None:
    payload = {
        "name": "sample",
        "country": "au",
        "offer_id": 11,
        "domain_id": 1,
        "group_id": 1,
        "traffic_source_id": 1,
    }
    key = str(uuid4())
    response = await write(client, "/campaigns", payload, key=key)
    assert response.status_code == 201, response.text
    data = response.json()
    assert data["name"] == "test_skudarnov_sample"
    created = tracker.campaigns[data["id"]]
    assert created["domain_id"] == created["traffic_source_id"] == 1
    assert created["group_id"] == "1"
    streams = [x for x in tracker.streams.values() if x["campaign_id"] == data["id"]]
    assert len(streams) == 2
    assert streams[0]["filters"] == [{"name": "country", "mode": "accept", "payload": ["AU"]}]
    assert streams[0]["action_payload"] == "https://www.google.com"
    assert streams[1]["type"] == "default" and streams[1]["offers"][0]["share"] == 100
    repeated = await write(client, "/campaigns", payload, key=key)
    assert repeated.status_code == 201 and repeated.json() == data
    assert sum(method == "POST" and path == "campaigns" for method, path, _ in tracker.calls) == 1


async def test_partial_creation_is_archived_and_not_retried_blindly(
    client: httpx.AsyncClient, tracker: Tracker
) -> None:
    tracker.fail_stream_create = True
    payload = {
        "name": "partial",
        "country": "AU",
        "offer_id": 11,
        "domain_id": 1,
        "group_id": 1,
        "traffic_source_id": 1,
    }
    key = str(uuid4())
    failed = await write(client, "/campaigns", payload, key=key)
    assert failed.status_code == 502
    assert tracker.campaigns[102]["state"] == "deleted"
    operation = (await client.get(f"/api/v1/operations/{key}")).json()
    assert operation["state"] == "failed_archived" and operation["campaign_id"] == 102
    retry = await write(client, "/campaigns", payload, key=key)
    assert retry.status_code == 409


async def test_auth_and_campaign_ownership(client: httpx.AsyncClient, tracker: Tracker) -> None:
    unauthorized = await client.get(
        "/api/v1/resources", headers={"Authorization": "Bearer invalid"}
    )
    assert unauthorized.status_code == 401 and not tracker.calls
    tracker.campaigns[101]["name"] = "another_candidate"
    blocked = await write(client, "/campaigns/101/fetch")
    assert blocked.status_code == 403


async def test_two_tabs_cannot_overwrite_one_revision(client: httpx.AsyncClient) -> None:
    stream = await imported(client)
    one, two = await asyncio.gather(
        *[
            write(
                client,
                "/streams/502/offers",
                {"revision": stream["revision"], "action": "remove", "offer_id": number},
                "PATCH",
            )
            for number in [11, 12]
        ]
    )
    assert sorted([one.status_code, two.status_code]) == [200, 409]
    local = (await client.get("/api/v1/campaigns/101")).json()["streams"][1]
    assert sum(x["removed"] for x in local["offers"]) == 1
    assert local["total"] == 100


async def test_concurrent_replay_executes_edit_once(
    client: httpx.AsyncClient, tracker: Tracker
) -> None:
    stream = await imported(client)
    key = str(uuid4())
    body = {"revision": stream["revision"], "action": "add", "offer_id": 14}
    one, two = await asyncio.gather(
        *[write(client, "/streams/502/offers", body, "PATCH", key) for _ in range(2)]
    )
    assert one.status_code == two.status_code == 200
    assert one.json() == two.json()
    assert sum(method == "GET" and path == "offers/14" for method, path, _ in tracker.calls) == 1


async def test_changed_stream_schema_blocks_remote_write(
    client: httpx.AsyncClient, tracker: Tracker
) -> None:
    stream = await imported(client)
    draft = await change(client, stream, "add", 14)
    tracker.streams[502]["schema"] = "redirect"
    response = await write(client, "/streams/502/push", {"revision": draft["revision"]})
    assert response.status_code == 409
    assert not any(method == "PUT" for method, _, _ in tracker.calls)


async def test_pin_overflow_does_not_corrupt_existing_draft(client: httpx.AsyncClient) -> None:
    stream = await imported(client)
    pinned = await change(client, stream, "pin", 11, 80)
    invalid = await write(
        client,
        "/streams/502/offers",
        {
            "revision": pinned["revision"],
            "action": "pin",
            "offer_id": 12,
            "share": 30,
        },
        "PATCH",
    )
    assert invalid.status_code == 422
    local = (await client.get("/api/v1/campaigns/101")).json()["streams"][1]
    assert local == pinned
