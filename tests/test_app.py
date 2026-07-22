from __future__ import annotations

import os
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from fastapi.testclient import TestClient

import app as app_module


class FakeWarpResponse:
    is_success = True
    status_code = 200
    headers: dict[str, str] = {}

    def json(self) -> dict[str, Any]:
        return {
            "market_options": [
                {
                    "carrier_name": "Carrier B",
                    "service_level": "Standard",
                    "price_usd": "425.80",
                    "transit_days": 2,
                    "bookable": True,
                    "quote_id": "quote-b",
                    "option_id": "option-b",
                    "is_warp": False,
                },
                {
                    "carrier_name": "Carrier A",
                    "service_level": "Priority",
                    "price_usd": 389.23,
                    "transit_days": 3,
                    "bookable": False,
                    "quote_id": "quote-a",
                    "option_id": "option-a",
                    "is_warp": True,
                },
                {"carrier_name": "Carrier C", "price_usd": None},
            ],
            "note": "Test response",
        }


class FakeAsyncClient:
    last_url: str | None = None
    last_json: dict[str, Any] | None = None
    last_headers: dict[str, str] | None = None
    last_timeout: Any = None

    def __init__(self, *, timeout: Any, **_: Any) -> None:
        type(self).last_timeout = timeout

    async def __aenter__(self) -> FakeAsyncClient:
        return self

    async def __aexit__(self, *_: Any) -> None:
        return None

    async def post(
        self,
        url: str,
        *,
        headers: dict[str, str],
        json: dict[str, Any],
    ) -> FakeWarpResponse:
        type(self).last_url = url
        type(self).last_json = json
        type(self).last_headers = headers
        return FakeWarpResponse()


class FreightQuoteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app_module.app)
        self.payload = {
            "_environment": "live",
            "origin_zip": "90012",
            "destination_zip": "94105",
            "pickup_date": "2026-07-27",
            "pallets": 2,
            "weight_lbs_per_pallet": 500,
            "length_in": 48,
            "width_in": 40,
            "height_in": 48,
            "pickup_services": [],
            "delivery_services": ["liftgate-delivery"],
            "must_not_be_forwarded": "ignored",
        }

    def test_ltl_market_request_is_filtered_timed_and_sorted(self) -> None:
        with (
            patch.object(app_module.httpx, "AsyncClient", FakeAsyncClient),
            patch.object(app_module, "WARP_ENV", "sandbox"),
            patch.dict(os.environ, {"WARP_SANDBOX_KEY": "backend-only-key"}),
        ):
            response = self.client.post("/api/warp/quote-ltl-market", json=self.payload)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            FakeAsyncClient.last_url,
            "https://www.wearewarp.com/api/v1/ltl/market-options",
        )
        self.assertEqual(set(FakeAsyncClient.last_json or {}), set(app_module.LTL_MARKET_FIELDS))
        self.assertEqual((FakeAsyncClient.last_json or {})["pickup_services"], [])
        self.assertEqual(FakeAsyncClient.last_timeout.read, 60.0)
        self.assertEqual(
            (FakeAsyncClient.last_headers or {}).get("Authorization"),
            "Bearer backend-only-key",
        )

        body = response.json()
        self.assertNotIn("environment", body)
        self.assertNotIn("rate_limit", body)
        carriers = [option["carrier_name"] for option in body["data"]["market_options"]]
        self.assertEqual(carriers, ["Carrier A", "Carrier B", "Carrier C"])
        self.assertEqual(body["data"]["market_options"][0]["option_id"], "option-a")

    def test_browser_cannot_select_the_backend_environment(self) -> None:
        with (
            patch.object(app_module.httpx, "AsyncClient", FakeAsyncClient),
            patch.object(app_module, "WARP_ENV", "sandbox"),
            patch.dict(
                os.environ,
                {"WARP_SANDBOX_KEY": "sandbox-secret", "WARP_LIVE_KEY": "live-secret"},
            ),
        ):
            self.client.post("/api/warp/quote-ltl-market", json=self.payload)

        self.assertEqual(
            (FakeAsyncClient.last_headers or {}).get("Authorization"),
            "Bearer sandbox-secret",
        )
        self.assertNotIn("_environment", FakeAsyncClient.last_json or {})

    def test_missing_ltl_market_fields_are_rejected(self) -> None:
        response = self.client.post("/api/warp/quote-ltl-market", json={})
        self.assertEqual(response.status_code, 400)

    def test_booking_and_payment_actions_are_not_exposed(self) -> None:
        for action in ("book", "rebook", "bookings", "payment", "checkout"):
            with self.subTest(action=action):
                response = self.client.post(f"/api/warp/{action}", json={})
                self.assertEqual(response.status_code, 404)

        exposed_actions = " ".join(app_module.ALLOWED_ACTIONS)
        self.assertNotIn("book", exposed_actions)
        self.assertNotIn("payment", exposed_actions)

    def test_non_ltl_quote_routes_are_preserved(self) -> None:
        self.assertEqual(app_module.ALLOWED_ACTIONS["quote-ftl"][1], "/ftl/quote")
        self.assertEqual(app_module.ALLOWED_ACTIONS["quote-box"][1], "/box-truck/quote")
        self.assertEqual(app_module.ALLOWED_ACTIONS["quote-van"][1], "/van/quote")

        javascript = Path("static/app.js").read_text(encoding="utf-8")
        self.assertIn('carrier_name: "WARP Network"', javascript)
        self.assertIn('safeCustomerNote(data?.note)', javascript)

    def test_health_does_not_disclose_configuration(self) -> None:
        body = self.client.get("/api/health").json()
        self.assertEqual(body, {"ok": True})

    def test_customer_page_hides_technical_panel_language(self) -> None:
        html = Path("static/index.html").read_text(encoding="utf-8")
        for forbidden in ("Sandbox", "Production / Live", "Ham API cevabı", "Test Paneli"):
            self.assertNotIn(forbidden, html)
        self.assertIn("Teklifleri Karşılaştır", html)
        self.assertIn("quote-ltl-market", html)

    def test_customer_javascript_has_no_raw_response_panel(self) -> None:
        javascript = Path("static/app.js").read_text(encoding="utf-8")
        self.assertNotIn("debugOutput", javascript)
        self.assertNotIn("URLSearchParams", javascript)
        self.assertNotIn("innerHTML = JSON.stringify", javascript)


if __name__ == "__main__":
    unittest.main()
