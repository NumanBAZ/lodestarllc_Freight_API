from __future__ import annotations

import copy
from typing import Any

import app as production


records: dict[str, dict[str, Any]] = {}


class MockWarpResponse:
    is_success = True
    status_code = 200
    headers: dict[str, str] = {}

    def json(self) -> dict[str, Any]:
        return {
            "market_options": [
                {
                    "carrier_name": "QA Carrier One",
                    "service_level": "Standard",
                    "price_usd": 315.25,
                    "transit_days": 2,
                    "quote_id": "qa-quote-public-hidden",
                    "option_id": "qa-option-staff-visible",
                    "bookable": True,
                },
                {
                    "carrier_name": "QA Carrier Two",
                    "service_level": "Economy",
                    "price_usd": 348.90,
                    "transit_days": 3,
                    "quote_id": "qa-quote-two",
                    "option_id": "qa-option-two",
                    "bookable": False,
                },
                {
                    "carrier_name": "QA Carrier Express",
                    "service_level": "Priority",
                    "price_usd": 390.00,
                    "transit_days": 1,
                    "quote_id": "qa-quote-three",
                    "option_id": "qa-option-three",
                    "bookable": True,
                },
            ]
        }


class MockWarpClient:
    def __init__(self, **_: Any) -> None:
        pass

    async def __aenter__(self) -> "MockWarpClient":
        return self

    async def __aexit__(self, *_: Any) -> None:
        return None

    async def post(self, *_: Any, **__: Any) -> MockWarpResponse:
        return MockWarpResponse()


async def save_quote_request(record: dict[str, Any]) -> None:
    records[str(record["id"])] = copy.deepcopy(record)


async def get_quote_request(request_id: str) -> dict[str, Any] | None:
    record = records.get(request_id)
    return copy.deepcopy(record) if record else None


async def list_quote_requests() -> list[dict[str, Any]]:
    return [copy.deepcopy(record) for record in records.values()]


async def update_quote_request(request_id: str, changes: dict[str, Any]) -> dict[str, Any]:
    records[request_id].update(copy.deepcopy(changes))
    return copy.deepcopy(records[request_id])


production.httpx.AsyncClient = MockWarpClient
production.save_quote_request = save_quote_request
production.get_quote_request = get_quote_request
production.list_quote_requests = list_quote_requests
production.update_quote_request = update_quote_request

app = production.app
