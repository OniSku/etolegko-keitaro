import hashlib
import logging
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import uuid4

import orjson
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.domain import Offer, Problem, RemoteLink, distribute, fingerprint
from app.keitaro import Keitaro
from app.models import Campaign, Operation, Stream
from app.schemas import CampaignCreate, OfferChange

logger = logging.getLogger(__name__)
JSON = dict[str, Any]


def digest(value: object) -> str:
    return hashlib.sha256(orjson.dumps(value, option=orjson.OPT_SORT_KEYS)).hexdigest()


def present(stream: Stream) -> JSON:
    return {
        "id": stream.id,
        "campaign_id": stream.campaign_id,
        "name": stream.name,
        "schema": stream.schema_name,
        "revision": stream.revision,
        "dirty": stream.draft_snapshot is not None,
        "offers": stream.offers,
        "total": sum(
            x["share"] for x in stream.offers if not x["removed"] and x["state"] == "active"
        ),
    }


async def receipt(session: AsyncSession, key: str, signature: str) -> Operation | None:
    inserted = await session.scalar(
        insert(Operation)
        .values(
            key=key,
            request_hash=signature,
            state="pending",
        )
        .on_conflict_do_nothing()
        .returning(Operation.key)
    )
    if inserted:
        return None
    operation = await session.get(Operation, key)
    assert operation is not None
    if operation.request_hash != signature:
        raise Problem(409, "Idempotency-Key уже использован для другого запроса.")
    return operation


async def idempotent(
    session: AsyncSession,
    key: str,
    signature: str,
    handler: Callable[[], Awaitable[JSON]],
) -> JSON:
    previous = await receipt(session, key, signature)
    if previous:
        if previous.state == "complete" and previous.result is not None:
            return previous.result
        raise Problem(409, "Операция уже выполняется или требует проверки результата.")
    result = await handler()
    operation = await session.get(Operation, key)
    assert operation is not None
    operation.state, operation.result = "complete", result
    return result


def require_owned(name: str, settings: Settings) -> None:
    if not name.startswith(settings.campaign_prefix):
        raise Problem(403, f"Доступны только кампании с префиксом {settings.campaign_prefix}.")


async def sync_campaign(
    session: AsyncSession, kt: Keitaro, settings: Settings, campaign_id: int
) -> JSON:
    remote = await kt.request("GET", f"campaigns/{campaign_id}")
    require_owned(remote["name"], settings)
    # UPSERT serializes concurrent imports of the same campaign, including its first import.
    await session.execute(
        insert(Campaign)
        .values(id=campaign_id, name=remote["name"])
        .on_conflict_do_update(
            index_elements=[Campaign.id],
            set_={"name": remote["name"]},
        )
    )
    existing = list(
        (
            await session.scalars(
                select(Stream)
                .where(
                    Stream.campaign_id == campaign_id,
                )
                .with_for_update()
            )
        ).all()
    )
    if any(row.draft_snapshot is not None for row in existing):
        raise Problem(
            409, "Fetch не перезаписывает черновики. Сначала опубликуйте или отмените изменения."
        )
    streams = await kt.streams(campaign_id)
    catalog = {x["id"]: x["name"] for x in await kt.request("GET", "offers")}
    by_id = {row.id: row for row in existing}
    for item in streams:
        row = by_id.get(item.id)
        if row is None:
            row = Stream(id=item.id, campaign_id=campaign_id, revision=0)
            session.add(row)
        previous = {x["offer_id"]: Offer.model_validate(x) for x in (row.offers or [])}
        current: list[Offer] = []
        for link in item.offers:
            old = previous.pop(link.offer_id, None)
            current.append(
                Offer(
                    offer_id=link.offer_id,
                    name=catalog.get(link.offer_id, old.name if old else f"Offer #{link.offer_id}"),
                    share=link.share,
                    state=link.state,
                    pinned=old.pinned if old and not old.removed else False,
                )
            )
        for offer in previous.values():
            current.append(offer.model_copy(update={"removed": True, "share": 0, "pinned": False}))
        row.name, row.schema_name = item.name, item.schema_name
        row.offers = [x.model_dump() for x in current]
        row.remote_signature = fingerprint(item.offers)
        row.revision += 1
    for row in existing:
        if row.id not in {item.id for item in streams}:
            row.schema_name = "deleted"
            row.offers = [dict(x, removed=True, share=0, pinned=False) for x in row.offers]
            row.revision += 1
    await session.flush()
    rows = (
        await session.scalars(
            select(Stream).where(Stream.campaign_id == campaign_id).order_by(Stream.id)
        )
    ).all()
    return {"id": campaign_id, "name": remote["name"], "streams": [present(row) for row in rows]}


async def locked_stream(session: AsyncSession, stream_id: int, revision: int) -> Stream:
    row = await session.scalar(select(Stream).where(Stream.id == stream_id).with_for_update())
    if row is None:
        raise Problem(404, "Поток ещё не импортирован.")
    if row.revision != revision:
        raise Problem(409, "Поток изменён в другой вкладке. Обновите локальное состояние.")
    if row.schema_name != "landings":
        raise Problem(422, "У этого потока нет редактируемой схемы офферов.")
    return row


async def change_offer(
    session: AsyncSession, kt: Keitaro, stream_id: int, change: OfferChange
) -> JSON:
    row = await locked_stream(session, stream_id, change.revision)
    offers = [Offer.model_validate(x) for x in row.offers]
    target = next((x for x in offers if x.offer_id == change.offer_id), None)
    if change.action == "add":
        if target:
            raise Problem(
                409, "Оффер уже известен потоку. Для архивного оффера используйте восстановление."
            )
        remote = await kt.request("GET", f"offers/{change.offer_id}")
        if remote.get("state") != "active":
            raise Problem(422, "Оффер недоступен для добавления.")
        offers.append(Offer(offer_id=change.offer_id, name=remote["name"], share=0))
    else:
        if target is None:
            raise Problem(404, "Оффер не найден в потоке.")
        if change.action == "remove":
            target.removed, target.pinned = True, False
        elif change.action == "restore":
            remote = await kt.request("GET", f"offers/{change.offer_id}")
            if remote.get("state") != "active":
                raise Problem(422, "Оффер удалён или отключён в Keitaro.")
            target.removed, target.state = False, "active"
        elif change.action == "pin":
            if target.removed or target.state != "active" or change.share is None:
                raise Problem(422, "Для активного оффера укажите закрепляемую долю.")
            target.pinned, target.share = True, change.share
        elif change.action == "unpin":
            target.pinned = False
    updated = distribute(offers)
    if row.draft_snapshot is None:
        row.draft_snapshot = row.offers
    row.offers = [x.model_dump() for x in updated]
    row.revision += 1
    return present(row)


async def cancel(session: AsyncSession, stream_id: int, revision: int) -> JSON:
    row = await locked_stream(session, stream_id, revision)
    if row.draft_snapshot is not None:
        row.offers, row.draft_snapshot = row.draft_snapshot, None
        row.revision += 1
    return present(row)


async def publish(
    session: AsyncSession, kt: Keitaro, settings: Settings, stream_id: int, revision: int
) -> JSON:
    row = await locked_stream(session, stream_id, revision)
    if row.draft_snapshot is None:
        return present(row)
    offers = distribute([Offer.model_validate(x) for x in row.offers])
    links = [
        RemoteLink(offer_id=x.offer_id, share=x.share, state=x.state)
        for x in offers
        if not x.removed
    ]
    current = await kt.stream(stream_id)
    if current.campaign_id != row.campaign_id or current.schema_name != "landings":
        raise Problem(409, "Кампания или схема потока изменена в Keitaro. Черновик сохранён.")
    remote_campaign = await kt.request("GET", f"campaigns/{row.campaign_id}")
    require_owned(remote_campaign["name"], settings)
    desired = fingerprint(links)
    # A previous request may have reached Keitaro but lost its response. Reconcile it safely.
    if fingerprint(current.offers) != desired:
        if fingerprint(current.offers) != row.remote_signature:
            raise Problem(
                409,
                "Офферы или веса изменены в Keitaro. Черновик сохранён; отмените его и выполните Fetch для сверки.",
            )
        await kt.request(
            "PUT", f"streams/{stream_id}", json={"offers": [x.model_dump() for x in links]}
        )
        current = await kt.stream(stream_id)
        if fingerprint(current.offers) != desired:
            raise Problem(
                409, "Keitaro не подтвердил ожидаемые веса. Черновик сохранён для проверки."
            )
    row.offers = [x.model_dump() for x in offers]
    row.remote_signature, row.draft_snapshot = desired, None
    row.revision += 1
    return present(row)


async def create_campaign(
    factory: async_sessionmaker[AsyncSession],
    kt: Keitaro,
    settings: Settings,
    payload: CampaignCreate,
    key: str,
) -> JSON:
    signature = digest({"create": payload.model_dump()})
    async with factory.begin() as session:
        previous = await receipt(session, key, signature)
        if previous:
            if previous.state == "complete" and previous.result:
                return previous.result
            raise Problem(
                409, "Создание с этим ключом уже начато. Проверьте результат перед повтором."
            )
    remote_id: int | None = None
    try:
        # Validate references before any remote mutation.
        for resource, selected in [
            ("domains", payload.domain_id),
            ("groups?type=campaigns", payload.group_id),
            ("traffic_sources", payload.traffic_source_id),
        ]:
            choices = await kt.request("GET", resource)
            if not any(
                x["id"] == selected and x.get("state", "active") == "active" for x in choices
            ):
                raise Problem(422, f"Недоступный объект Keitaro: {resource}.")
        offer = await kt.request("GET", f"offers/{payload.offer_id}")
        if offer.get("state") != "active":
            raise Problem(422, "Выбранный оффер неактивен.")
        name = (
            payload.name
            if payload.name.startswith(settings.campaign_prefix)
            else settings.campaign_prefix + payload.name
        )
        created = await kt.request(
            "POST",
            "campaigns",
            json={
                "name": name,
                "alias": uuid4().hex[:12],
                "type": "position",
                "domain_id": payload.domain_id,
                "group_id": str(payload.group_id),
                "traffic_source_id": payload.traffic_source_id,
            },
        )
        remote_id = int(created["id"])
        async with factory.begin() as session:
            operation = await session.get(Operation, key)
            assert operation is not None
            operation.remote_id = remote_id
        await kt.request(
            "POST",
            "streams",
            json={
                "campaign_id": remote_id,
                "name": f"Geo {payload.country}",
                "type": "regular",
                "position": 1,
                "schema": "redirect",
                "action_type": "http",
                "action_payload": settings.google_url,
                "filters": [{"name": "country", "mode": "accept", "payload": [payload.country]}],
            },
        )
        await kt.request(
            "POST",
            "streams",
            json={
                "campaign_id": remote_id,
                "name": "Offers",
                "type": "default",
                "position": 2,
                "schema": "landings",
                "action_type": "http",
                "offers": [{"offer_id": payload.offer_id, "share": 100, "state": "active"}],
            },
        )
        async with factory.begin() as session:
            result = await sync_campaign(session, kt, settings, remote_id)
            operation = await session.get(Operation, key)
            assert operation is not None
            operation.state, operation.result = "complete", result
        return result
    except Problem:
        state = "uncertain" if remote_id is None else "cleanup_pending"
        if remote_id is not None:
            try:
                await kt.request("DELETE", f"campaigns/{remote_id}")
                state = "failed_archived"
            except Problem:
                logger.exception("Could not archive incomplete test campaign id=%s", remote_id)
        async with factory.begin() as session:
            operation = await session.get(Operation, key)
            assert operation is not None
            operation.state = state
        logger.warning(
            "Campaign creation incomplete: operation=%s campaign=%s state=%s", key, remote_id, state
        )
        raise
