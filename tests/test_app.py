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
        self.assertIn('carrier_name: "Lodestar Freight"', javascript)
        self.assertIn('safeCustomerNote(data?.note)', javascript)

    def test_health_does_not_disclose_configuration(self) -> None:
        body = self.client.get("/api/health").json()
        self.assertEqual(body, {"ok": True})

    def test_customer_page_is_english_and_exposes_only_ltl_and_ftl(self) -> None:
        html = Path("static/index.html").read_text(encoding="utf-8")
        self.assertIn('<html lang="en">', html)
        self.assertIn("Get an Instant Freight Quote", html)
        self.assertNotIn(
            "Enter your shipment details and compare available carrier rates.",
            html,
        )
        self.assertIn("Get My Quote", html)
        self.assertIn("Less Than Truckload", html)
        self.assertIn("Full Truckload", html)
        self.assertIn('value="quote-ltl-market"', html)
        self.assertIn('value="quote-ftl"', html)
        for forbidden in (
            "Sandbox",
            "Production / Live",
            "Ham API cevabı",
            "Test Paneli",
            "Reefer",
            "Temperature Controlled",
            "Box Truck",
            "Cargo Van",
        ):
            self.assertNotIn(forbidden, html)

    def test_required_customer_controls_and_services_are_present(self) -> None:
        html = Path("static/index.html").read_text(encoding="utf-8")
        for expected in (
            "Origin ZIP Code",
            "Destination ZIP Code",
            "Pickup Date",
            "Number of Pallets",
            "Weight per Pallet",
            "Commodity",
            "Freight Class",
            "Hazmat",
            "Shipment contains regulated hazardous materials",
            "Stackable",
            "Pallets can be safely stacked",
            "Liftgate Pickup",
            "Residential Delivery",
            "Two-Man Delivery",
        ):
            self.assertIn(expected, html)

    def test_quote_form_uses_simplified_premium_layout(self) -> None:
        html = Path("static/index.html").read_text(encoding="utf-8")
        css = Path("static/style.css").read_text(encoding="utf-8").lower()
        self.assertIn("family=Manrope:wght@400;500;600;700;800", html)
        self.assertIn("Shipment Details", html)
        self.assertIn("Enter your route and load information.", html)
        self.assertIn("Route <span>→</span> Load Details <span>→</span> Quote", html)
        self.assertNotIn("section-number", html)
        self.assertNotIn("progress-strip", html)
        self.assertNotIn("Live carrier pricing through the secure WARP network", html)
        self.assertIn("Secure and transparent freight pricing", html)
        self.assertIn("background: #f0eee8", css)
        self.assertIn("border: 1px solid #cac6bc", css)
        self.assertIn("background: #111721", css)
        self.assertIn("background: #151c27", css)

    def test_customer_ui_does_not_expose_warp_branding(self) -> None:
        html = Path("static/index.html").read_text(encoding="utf-8")
        javascript = Path("static/app.js").read_text(encoding="utf-8")
        self.assertNotIn("WARP Network", html + javascript)
        self.assertNotIn('offer-tag-warp">WARP', javascript)

    def test_quote_modal_has_fixed_visible_contact_actions(self) -> None:
        html = Path("static/index.html").read_text(encoding="utf-8")
        css = Path("static/style.css").read_text(encoding="utf-8").lower()
        javascript = Path("static/app.js").read_text(encoding="utf-8")
        self.assertIn('class="dialog-info" role="note"', html)
        self.assertIn(
            "Contact Lodestar Logistics to confirm and finalize this quote.",
            html,
        )
        self.assertIn("(908) 224-3764", html)
        self.assertIn("info@lodestarllc.com", html)
        self.assertEqual(html.count('href="tel:+19082243764"'), 2)
        self.assertEqual(html.count('href="mailto:info@lodestarllc.com"'), 2)
        self.assertIn('id="phoneLink"', html)
        self.assertIn(">Call Now</a>", html)
        self.assertIn('id="emailLink"', html)
        self.assertIn(">Send Email</a>", html)
        self.assertNotIn("dialog-contact-copy", html + css)
        self.assertNotIn("requestLink", html + javascript)
        self.assertNotIn("Request Confirmation", html)
        self.assertIn("background: #2563eb", css)
        self.assertIn("grid-template-columns: 1fr 1fr", css)

    def test_raw_response_is_gated_by_debug_query_parameter(self) -> None:
        html = Path("static/index.html").read_text(encoding="utf-8")
        javascript = Path("static/app.js").read_text(encoding="utf-8")
        self.assertIn('id="debugPanel" class="debug-panel" hidden', html)
        self.assertIn('new URLSearchParams(window.location.search).get("debug") === "true"', javascript)
        self.assertIn("if (!DEBUG_MODE) return", javascript)
        self.assertIn("textContent = JSON.stringify", javascript)
        self.assertNotIn("innerHTML = JSON.stringify", javascript)

    def test_frontend_uses_live_api_fields_without_fake_carriers_or_booking(self) -> None:
        html = Path("static/index.html").read_text(encoding="utf-8")
        javascript = Path("static/app.js").read_text(encoding="utf-8")
        self.assertIn("market_options", javascript)
        for field in (
            "carrier_name",
            "service_level",
            "price_usd",
            "transit_days",
            "bookable",
            "quote_id",
            "option_id",
            "is_warp",
        ):
            self.assertIn(field, javascript)
        for fake_carrier in ("FedEx", "XPO", "Estes"):
            self.assertNotIn(fake_carrier, html + javascript)
        self.assertNotIn("/book", javascript)
        self.assertNotIn("checkout", javascript.lower())

    def test_lodestar_theme_and_static_assets_are_served(self) -> None:
        css = Path("static/style.css").read_text(encoding="utf-8").lower()
        for color in ("#0b0d12", "#07142b", "#11151d", "#161b24", "#f4b800", "#e01e37", "#f7f7f5", "#a9b0bc"):
            self.assertIn(color, css)
        self.assertEqual(self.client.get("/").status_code, 200)
        self.assertEqual(self.client.get("/static/style.css").status_code, 200)
        self.assertEqual(self.client.get("/static/app.js").status_code, 200)


if __name__ == "__main__":
    unittest.main()
