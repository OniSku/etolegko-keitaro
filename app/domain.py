import hashlib
from typing import Literal

import orjson
from pydantic import BaseModel, ConfigDict, Field


class Problem(Exception):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


class Offer(BaseModel):
    offer_id: int = Field(gt=0)
    name: str
    share: int = Field(ge=0, le=100)
    pinned: bool = False
    removed: bool = False
    state: Literal["active", "disabled"] = "active"


class RemoteLink(BaseModel):
    offer_id: int
    share: int
    state: Literal["active", "disabled"] = "active"


class RemoteStream(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    id: int
    campaign_id: int
    name: str
    schema_name: str = Field(alias="schema")
    offers: list[RemoteLink] = Field(default_factory=list)


def fingerprint(links: list[RemoteLink]) -> str:
    values = sorted((x.offer_id, x.share, x.state) for x in links)
    return hashlib.sha256(orjson.dumps(values)).hexdigest()


def distribute(offers: list[Offer]) -> list[Offer]:
    """Allocate integer percentages; deterministic remainder, no floating-point drift."""
    result = [offer.model_copy(deep=True) for offer in offers]
    active = [x for x in result if not x.removed and x.state == "active"]
    if not active:
        raise Problem(422, "В потоке должен остаться хотя бы один активный оффер.")
    pinned = [x for x in active if x.pinned]
    free = sorted((x for x in active if not x.pinned), key=lambda x: x.offer_id)
    remaining = 100 - sum(x.share for x in pinned)
    if remaining < 0 or (not free and remaining != 0):
        raise Problem(422, "Закреплённые доли не позволяют получить сумму 100%.")
    if free:
        share, remainder = divmod(remaining, len(free))
        for index, offer in enumerate(free):
            offer.share = share + (index < remainder)
    for offer in result:
        if offer.removed or offer.state != "active":
            offer.share = 0
    return result
