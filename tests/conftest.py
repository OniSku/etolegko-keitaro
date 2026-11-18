import os
import secrets
from collections.abc import AsyncIterator
from copy import deepcopy
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx
import orjson
import pytest
from alembic import command
from alembic.config import Config
from dotenv import dotenv_values
from pydantic import SecretStr
from sqlalchemy import delete
from sqlalchemy.engine import make_url

from app.config import Settings
from app.keitaro import Keitaro
from app.main import create_app
from app.models import Campaign, Operation, Stream

JSON = dict[str, Any]


class Tracker:
    def __init__(self) -> None:
        self.campaigns: dict[int, JSON] = {
            101: {"id": 101, "name": "test_skudarnov_existing", "state": "active"}
        }
        self.offers: dict[int, JSON] = {
            number: {"id": number, "name": f"Offer {number}", "state": "active"}
            for number in [11, 12, 13, 14]
        }
        self.streams: dict[int, JSON] = {
            501: {
                "id": 501,
                "campaign_id": 101,
                "name": "Geo",
                "schema": "redirect",
                "offers": [],
                "filters": [{"name": "country", "payload": ["AU"]}],
            },
            502: {
                "id": 502,
                "campaign_id": 101,
                "name": "Offers",
                "schema": "landings",
                "offers": [
                    {"offer_id": 11, "share": 34, "state": "active"},
                    {"offer_id": 12, "share": 33, "state": "active"},
                    {"offer_id": 13, "share": 33, "state": "active"},
                ],
                "filters": [{"name": "language", "payload": ["en"]}],
                "landings": [{"landing_id": 99, "share": 100}],
            },
        }
        self.calls: list[tuple[str, str, JSON | None]] = []
        self.timeout_put = False
        self.fail_stream_create = False

    async def handle(self, request: httpx.Request) -> httpx.Response:
        method = request.method
        path = request.url.path.removeprefix("/admin_api/v1/")
        body: JSON = orjson.loads(request.content) if request.content else {}
        self.calls.append((method, path, body))
        result: Any = None
        if method == "GET":
            if path == "campaigns":
                result = list(self.campaigns.values())
            elif path == "offers":
                result = list(self.offers.values())
            elif path in {"domains", "groups", "traffic_sources"}:
                result = [{"id": 1, "name": "Test reference", "state": "active"}]
            elif path.startswith("campaigns/") and path.endswith("/streams"):
                result = [
                    x for x in self.streams.values() if x["campaign_id"] == int(path.split("/")[1])
                ]
            elif path.startswith("campaigns/"):
                result = self.campaigns.get(int(path.split("/")[1]))
            elif path.startswith("streams/"):
                result = self.streams.get(int(path.split("/")[1]))
            elif path.startswith("offers/"):
                result = self.offers.get(int(path.split("/")[1]))
        elif method == "POST" and path == "campaigns":
            number = max(self.campaigns) + 1
            result = {"id": number, **body}
            self.campaigns[number] = result
        elif method == "POST" and path == "streams":
            if self.fail_stream_create:
                return httpx.Response(500, json={"error": "fixture failure"})
            number = max(self.streams) + 1
            result = {"id": number, "offers": [], **body}
            self.streams[number] = result
        elif method == "PUT" and path.startswith("streams/"):
            number = int(path.split("/")[1])
            self.streams[number].update(body)
            if self.timeout_put:
                self.timeout_put = False
                raise httpx.ReadTimeout("Response lost after write", request=request)
            result = self.streams[number]
        elif method == "DELETE" and path.startswith("campaigns/"):
            number = int(path.split("/")[1])
            self.campaigns[number]["state"] = "deleted"
            result = self.campaigns[number]
        return (
            httpx.Response(200, json=deepcopy(result))
            if result is not None
            else httpx.Response(404, json={"error": "missing"})
        )


@pytest.fixture(scope="session")
def test_database() -> str:
    value = os.environ.get("TEST_DATABASE_URL") or dotenv_values(Path(".env")).get(
        "TEST_DATABASE_URL"
    )
    if not value or not (make_url(value).database or "").endswith("_test"):
        pytest.fail("Set TEST_DATABASE_URL to a dedicated PostgreSQL database ending in _test")
    with patch.dict(os.environ, {"DATABASE_URL": value}):
        config = Config("alembic.ini")
        command.downgrade(config, "base")
        command.upgrade(config, "head")
        command.check(config)
    return value


@pytest.fixture
def tracker() -> Tracker:
    return Tracker()


@pytest.fixture
async def client(test_database: str, tracker: Tracker) -> AsyncIterator[httpx.AsyncClient]:
    settings = Settings().model_copy(
        update={
            "database_url": SecretStr(test_database),
            "keitaro_base_url": "https://keitaro.example",
            "keitaro_api_key": SecretStr(secrets.token_urlsafe(32)),
            "app_token": SecretStr(secrets.token_urlsafe(32)),
            "campaign_prefix": "test_skudarnov_",
        }
    )
    gateway = Keitaro(settings, transport=httpx.MockTransport(tracker.handle))
    app = create_app(settings, gateway)
    async with app.router.lifespan_context(app):
        async with app.state.factory.begin() as session:
            for model in [Operation, Stream, Campaign]:
                await session.execute(delete(model))
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app),
            base_url="http://testserver",
            headers={
                "Authorization": f"Bearer {settings.app_token.get_secret_value()}",
            },
        ) as test_client:
            yield test_client
