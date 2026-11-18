import logging
from typing import Any

import httpx
from pydantic import TypeAdapter, ValidationError

from app.config import Settings
from app.domain import Problem, RemoteStream

logger = logging.getLogger(__name__)
streams_adapter = TypeAdapter(list[RemoteStream])


class Keitaro:
    def __init__(
        self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self.http = httpx.AsyncClient(
            base_url=f"{settings.keitaro_base_url}/admin_api/v1/",
            headers={"Api-Key": settings.keitaro_api_key.get_secret_value()},
            timeout=settings.request_timeout,
            follow_redirects=False,
            transport=transport,
        )

    async def close(self) -> None:
        await self.http.aclose()

    async def request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            response = await self.http.request(method, path, **kwargs)
        except httpx.TimeoutException as error:
            raise Problem(
                504,
                "Keitaro не ответил вовремя. Результат записи может быть неизвестен; сначала обновите состояние.",
            ) from error
        except httpx.RequestError as error:
            raise Problem(502, "Не удалось связаться с Keitaro.") from error
        if not response.is_success:
            logger.warning(
                "Keitaro request failed: method=%s resource=%s status=%s",
                method,
                path,
                response.status_code,
            )
            raise Problem(
                502, f"Keitaro вернул HTTP {response.status_code}. Локальный черновик сохранён."
            )
        try:
            return response.json()
        except ValueError as error:
            raise Problem(502, "Keitaro вернул некорректный JSON.") from error

    async def streams(self, campaign_id: int) -> list[RemoteStream]:
        try:
            return streams_adapter.validate_python(
                await self.request("GET", f"campaigns/{campaign_id}/streams")
            )
        except ValidationError as error:
            raise Problem(
                502, "Формат потоков Keitaro не соответствует ожидаемому контракту."
            ) from error

    async def stream(self, stream_id: int) -> RemoteStream:
        try:
            return RemoteStream.model_validate(await self.request("GET", f"streams/{stream_id}"))
        except ValidationError as error:
            raise Problem(
                502, "Формат потока Keitaro не соответствует ожидаемому контракту."
            ) from error
