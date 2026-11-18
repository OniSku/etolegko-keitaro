import logging
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from http import HTTPStatus
from pathlib import Path
from typing import Annotated, Any
from uuid import UUID

import orjson
from fastapi import Depends, FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import RequestResponseEndpoint
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import Response

from app import service
from app.config import Settings
from app.domain import Problem
from app.keitaro import Keitaro
from app.models import Campaign, Operation, Stream
from app.schemas import COUNTRIES, CampaignCreate, OfferChange, Revision

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None, gateway: Keitaro | None = None) -> FastAPI:
    config = settings or Settings()
    engine = create_async_engine(
        config.database_url.get_secret_value(), pool_pre_ping=True, hide_parameters=True
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    kt = gateway or Keitaro(config)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        yield
        await kt.close()
        await engine.dispose()

    app = FastAPI(
        title="Keitaro Workbench",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.factory = factory
    app.add_middleware(
        TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"]
    )

    def problem_response(request: Request, status: int, detail: str) -> Response:
        return Response(
            status_code=status,
            media_type="application/problem+json",
            content=orjson.dumps(
                {
                    "type": "about:blank",
                    "title": HTTPStatus(status).phrase,
                    "status": status,
                    "detail": detail,
                    "instance": request.url.path,
                }
            ),
        )

    @app.exception_handler(Problem)
    async def domain_error(request: Request, error: Problem) -> Response:
        return problem_response(request, error.status, error.detail)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, error: StarletteHTTPException) -> Response:
        return problem_response(request, error.status_code, str(error.detail))

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, error: RequestValidationError) -> Response:
        messages = {
            "share": "Доля оффера должна быть целым числом от 0 до 100%.",
            "country": "Укажите существующий код страны из двух латинских букв.",
            "name": "Проверьте название кампании: оно не должно быть пустым или слишком длинным.",
            "offer_id": "Укажите положительный целочисленный ID оффера.",
            "domain_id": "Выберите домен.",
            "group_id": "Выберите группу.",
            "traffic_source_id": "Выберите источник.",
            "revision": "Обновите состояние потока и повторите действие.",
            "action": "Выберите допустимое действие с оффером.",
            "Idempotency-Key": "Для операции нужен корректный Idempotency-Key в формате UUID.",
        }
        details = []
        for issue in error.errors():
            if issue["type"] == "json_invalid":
                details.append("Некорректный JSON запроса.")
            else:
                details.append(
                    messages.get(str(issue["loc"][-1]), "Некорректные параметры запроса.")
                )
        return problem_response(request, 422, " ".join(dict.fromkeys(details)))

    @app.exception_handler(SQLAlchemyError)
    async def database_error(request: Request, _error: SQLAlchemyError) -> Response:
        logger.exception("Database operation failed")
        return problem_response(
            request, 503, "База данных недоступна. Повторите чтение состояния перед новой записью."
        )

    @app.middleware("http")
    async def security_headers(request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    async def authenticate(authorization: Annotated[str | None, Header()] = None) -> None:
        supplied = (
            authorization[7:] if authorization and authorization.startswith("Bearer ") else ""
        )
        if not supplied:
            raise Problem(401, "Введите ключ доступа.")
        if not secrets.compare_digest(
            supplied.encode(), config.app_token.get_secret_value().encode()
        ):
            raise Problem(401, "Неверный ключ доступа.")

    async def database() -> AsyncIterator[AsyncSession]:
        async with factory() as session, session.begin():
            yield session

    DB = Annotated[AsyncSession, Depends(database)]
    Key = Annotated[UUID, Header(alias="Idempotency-Key")]
    auth = [Depends(authenticate)]

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/v1/meta", dependencies=auth)
    async def metadata() -> dict[str, Any]:
        return {
            "prefix": config.campaign_prefix,
            "countries": [{"id": code, "name": name} for code, name in sorted(COUNTRIES.items())],
        }

    @app.get("/api/v1/resources", dependencies=auth)
    async def resources() -> dict[str, Any]:
        result: dict[str, Any] = {}
        for name, path in [
            ("domains", "domains"),
            ("groups", "groups?type=campaigns"),
            ("sources", "traffic_sources"),
            ("offers", "offers"),
            ("campaigns", "campaigns"),
        ]:
            rows = await kt.request("GET", path)
            result[name] = [
                {"id": x["id"], "name": x["name"]}
                for x in rows
                if x.get("state", "active") not in {"deleted", "disabled"}
                and (name != "campaigns" or x["name"].startswith(config.campaign_prefix))
            ]
        return result

    @app.post("/api/v1/campaigns", status_code=201, dependencies=auth)
    async def create(payload: CampaignCreate, idempotency_key: Key) -> dict[str, Any]:
        return await service.create_campaign(factory, kt, config, payload, str(idempotency_key))

    @app.get("/api/v1/campaigns/{campaign_id}", dependencies=auth)
    async def read_campaign(campaign_id: int, session: DB) -> dict[str, Any]:
        campaign = await session.get(Campaign, campaign_id)
        if campaign is None:
            raise Problem(404, "Сначала выполните Fetch для кампании.")
        rows = (
            await session.scalars(
                select(Stream).where(Stream.campaign_id == campaign_id).order_by(Stream.id)
            )
        ).all()
        return {
            "id": campaign.id,
            "name": campaign.name,
            "streams": [service.present(x) for x in rows],
        }

    @app.post("/api/v1/campaigns/{campaign_id}/fetch", dependencies=auth)
    async def fetch(campaign_id: int, session: DB, idempotency_key: Key) -> dict[str, Any]:
        return await service.idempotent(
            session,
            str(idempotency_key),
            service.digest({"fetch": campaign_id}),
            lambda: service.sync_campaign(session, kt, config, campaign_id),
        )

    @app.patch("/api/v1/streams/{stream_id}/offers", dependencies=auth)
    async def edit(
        stream_id: int, payload: OfferChange, session: DB, idempotency_key: Key
    ) -> dict[str, Any]:
        return await service.idempotent(
            session,
            str(idempotency_key),
            service.digest({"edit": stream_id, "body": payload.model_dump()}),
            lambda: service.change_offer(session, kt, stream_id, payload),
        )

    @app.post("/api/v1/streams/{stream_id}/cancel", dependencies=auth)
    async def discard(
        stream_id: int, payload: Revision, session: DB, idempotency_key: Key
    ) -> dict[str, Any]:
        return await service.idempotent(
            session,
            str(idempotency_key),
            service.digest({"cancel": stream_id, "body": payload.model_dump()}),
            lambda: service.cancel(session, stream_id, payload.revision),
        )

    @app.post("/api/v1/streams/{stream_id}/push", dependencies=auth)
    async def push(
        stream_id: int, payload: Revision, session: DB, idempotency_key: Key
    ) -> dict[str, Any]:
        return await service.idempotent(
            session,
            str(idempotency_key),
            service.digest({"push": stream_id, "body": payload.model_dump()}),
            lambda: service.publish(session, kt, config, stream_id, payload.revision),
        )

    @app.get("/api/v1/operations/{key}", dependencies=auth)
    async def operation_state(key: UUID, session: DB) -> dict[str, Any]:
        operation = await session.get(Operation, str(key))
        if operation is None:
            raise Problem(404, "Операция не найдена.")
        return {
            "state": operation.state,
            "campaign_id": operation.remote_id,
            "result": operation.result,
        }

    dist = Path(__file__).resolve().parent.parent / "frontend" / "dist"
    if dist.is_dir():
        app.mount("/", StaticFiles(directory=dist, html=True), name="frontend")
    return app
