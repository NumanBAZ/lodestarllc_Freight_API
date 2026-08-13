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


class FakeBookingResponse:
    is_success = True
    status_code = 200
    headers: dict[str, str] = {}

    def json(self) -> dict[str, Any]:
        return {
            "booked": True,
            "shipment_id": "sandbox-shipment-123",
            "shipment_number": "S-SANDBOX-123",
            "tracking_number": "TRACK-SANDBOX-123",
            "tracking_dashboard": "https://example.invalid/sandbox-tracking",
            "sandbox": True,
            "charged": 0,
        }


class FakeBookingClient(FakeAsyncClient):
    call_count = 0

    async def post(
        self,
        url: str,
        *,
        headers: dict[str, str],
        json: dict[str, Any],
    ) -> FakeBookingResponse:
        type(self).last_url = url
        type(self).last_json = json
        type(self).last_headers = headers
        type(self).call_count += 1
        return FakeBookingResponse()


class FreightQuoteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.environment = patch.dict(
            os.environ,
            {
                "STAFF_USERNAME": "staff-user",
                "STAFF_PASSWORD": "correct-horse-battery-staple",
                "STAFF_SESSION_SECRET": "test-session-secret-that-is-longer-than-32-characters",
                "STAFF_COOKIE_SECURE": "false",
                "STAFF_BOOKING_ENABLED": "false",
            },
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)
        app_module.BOOKING_RESULTS.clear()
        app_module.BOOKINGS_IN_PROGRESS.clear()
        FakeBookingClient.call_count = 0
        self.client = TestClient(app_module.app)
        self.payload = {
            "_environment": "live",
            "origin_zip": "90012",
            "destination_zip": "94105",
            "pickup_date": "2026-07-27",
            "pallets": 2,
            "total_weight_lbs": 1001,
            "length_in": 48,
            "width_in": 40,
            "height_in": 48,
            "freight_class": "70",
            "pickup_services": [],
            "delivery_services": ["liftgate-delivery"],
            "must_not_be_forwarded": "ignored",
        }

    def login_staff(self) -> str:
        response = self.client.post(
            "/api/staff/login",
            json={
                "username": "staff-user",
                "password": "correct-horse-battery-staple",
            },
        )
        self.assertEqual(response.status_code, 200)
        return response.json()["csrf_token"]

    def booking_details(self, quote_token: str) -> dict[str, Any]:
        return {
            "quote_token": quote_token,
            "reference": "PO-12345",
            "pickup": {
                "company": "Shipper Co",
                "street": "123 Dock Street",
                "city": "Los Angeles",
                "state": "ca",
                "zipCode": "90012",
                "contactName": "Jane Shipper",
                "phone": "2135550100",
                "email": "shipper@example.com",
            },
            "delivery": {
                "company": "Receiver Co",
                "street": "500 Market Street",
                "city": "San Francisco",
                "state": "CA",
                "zipCode": "94105",
                "contactName": "Sam Receiver",
                "phone": "4155550100",
                "email": "receiver@example.com",
            },
            "pickup_window": {"from": "08:00", "to": "12:00"},
            "delivery_window": {"from": "13:00", "to": "17:00"},
            "notes": "Call the dock before arrival.",
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
        self.assertEqual((FakeAsyncClient.last_json or {})["weight_lbs_per_pallet"], 501)
        self.assertNotIn("total_weight_lbs", FakeAsyncClient.last_json or {})
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

    def test_each_required_quote_field_is_validated_by_the_backend(self) -> None:
        for field in (
            "origin_zip",
            "destination_zip",
            "pickup_date",
            "pallets",
            "total_weight_lbs",
            "length_in",
            "width_in",
            "height_in",
            "freight_class",
        ):
            with self.subTest(field=field):
                payload = {**self.payload, field: ""}
                response = self.client.post("/api/warp/quote-ltl-market", json=payload)
                self.assertEqual(response.status_code, 400)

    def test_zero_pallets_are_rejected_before_warp_is_called(self) -> None:
        FakeAsyncClient.last_url = None
        payload = {**self.payload, "pallets": 0}
        with patch.object(app_module.httpx, "AsyncClient", FakeAsyncClient):
            response = self.client.post("/api/warp/quote-ltl-market", json=payload)

        self.assertEqual(response.status_code, 400)
        self.assertIsNone(FakeAsyncClient.last_url)

    def test_ftl_route_is_preserved_and_uses_derived_weight(self) -> None:
        with patch.object(app_module.httpx, "AsyncClient", FakeAsyncClient):
            response = self.client.post("/api/warp/quote-ftl", json=self.payload)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(FakeAsyncClient.last_url, "https://www.wearewarp.com/api/v1/ftl/quote")
        self.assertEqual((FakeAsyncClient.last_json or {})["weight_lbs_per_pallet"], 501)
        self.assertNotIn("total_weight_lbs", FakeAsyncClient.last_json or {})

    def test_booking_and_payment_actions_are_not_exposed(self) -> None:
        for action in ("book", "rebook", "bookings", "payment", "checkout"):
            with self.subTest(action=action):
                response = self.client.post(f"/api/warp/{action}", json={})
                self.assertEqual(response.status_code, 404)

        exposed_actions = " ".join(app_module.ALLOWED_ACTIONS)
        self.assertNotIn("book", exposed_actions)
        self.assertNotIn("payment", exposed_actions)

    def test_staff_api_requires_backend_auth_and_csrf(self) -> None:
        for path in ("/api/staff/session", "/api/staff/quote/ltl", "/api/staff/book"):
            with self.subTest(path=path):
                response = self.client.get(path) if path.endswith("session") else self.client.post(path, json={})
                self.assertEqual(response.status_code, 401)

        csrf = self.login_staff()
        response = self.client.post("/api/staff/quote/ltl", json=self.payload)
        self.assertEqual(response.status_code, 403)
        self.assertTrue(csrf)

    def test_staff_login_cookie_session_and_logout(self) -> None:
        invalid = self.client.post(
            "/api/staff/login",
            json={"username": "staff-user", "password": "wrong-password"},
        )
        self.assertEqual(invalid.status_code, 401)

        login = self.client.post(
            "/api/staff/login",
            json={"username": "staff-user", "password": "correct-horse-battery-staple"},
        )
        self.assertEqual(login.status_code, 200)
        self.assertFalse(login.json()["booking_enabled"])
        csrf = login.json()["csrf_token"]
        cookie = self.client.cookies.get(app_module.STAFF_COOKIE_NAME)
        self.assertTrue(cookie)
        set_cookie = login.headers["set-cookie"].lower()
        self.assertIn("httponly", set_cookie)
        self.assertIn("samesite=strict", set_cookie)

        session = self.client.get("/api/staff/session")
        self.assertEqual(session.status_code, 200)
        self.assertFalse(session.json()["booking_enabled"])
        logout = self.client.post("/api/staff/logout", headers={"X-Staff-CSRF": csrf})
        self.assertEqual(logout.status_code, 200)
        self.assertEqual(self.client.get("/api/staff/session").status_code, 401)

    def test_staff_cookie_is_secure_behind_https_proxy(self) -> None:
        with patch.dict(os.environ, {"STAFF_COOKIE_SECURE": ""}):
            response = self.client.post(
                "/api/staff/login",
                json={"username": "staff-user", "password": "correct-horse-battery-staple"},
                headers={"x-forwarded-proto": "https"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertIn("secure", response.headers["set-cookie"].lower())

        with patch.dict(os.environ, {"STAFF_COOKIE_SECURE": "true"}):
            explicitly_secure = self.client.post(
                "/api/staff/login",
                json={"username": "staff-user", "password": "correct-horse-battery-staple"},
            )
        cookie = explicitly_secure.headers["set-cookie"].lower()
        self.assertIn("secure", cookie)
        self.assertIn("httponly", cookie)
        self.assertIn("samesite=strict", cookie)

    def test_staff_ltl_quote_preserves_booking_fields_and_disables_unbookable(self) -> None:
        csrf = self.login_staff()
        with (
            patch.object(app_module.httpx, "AsyncClient", FakeAsyncClient),
            patch.object(app_module, "WARP_ENV", "sandbox"),
            patch.dict(os.environ, {"WARP_SANDBOX_KEY": "wak_test_backend_only"}),
        ):
            response = self.client.post(
                "/api/staff/quote/ltl",
                json=self.payload,
                headers={"X-Staff-CSRF": csrf},
            )

        self.assertEqual(response.status_code, 200)
        options = response.json()["options"]
        self.assertEqual([option["carrier_name"] for option in options], ["Carrier A", "Carrier B", "Carrier C"])
        self.assertEqual(options[0]["quote_id"], "quote-a")
        self.assertEqual(options[0]["option_id"], "option-a")
        self.assertFalse(options[0]["bookable"])
        self.assertNotIn("quote_token", options[0])
        self.assertTrue(options[1]["bookable"])
        self.assertTrue(options[1]["quote_token"])
        self.assertEqual(FakeAsyncClient.last_url, "https://www.wearewarp.com/api/v1/ltl/market-options")
        self.assertEqual((FakeAsyncClient.last_headers or {}).get("Authorization"), "Bearer wak_test_backend_only")

    def test_staff_quote_modes_use_the_documented_warp_endpoints(self) -> None:
        csrf = self.login_staff()
        for mode, path in app_module.STAFF_QUOTE_PATHS.items():
            with self.subTest(mode=mode), patch.object(app_module.httpx, "AsyncClient", FakeAsyncClient):
                response = self.client.post(
                    f"/api/staff/quote/{mode}",
                    json=self.payload,
                    headers={"X-Staff-CSRF": csrf},
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(FakeAsyncClient.last_url, f"https://www.wearewarp.com/api/v1{path}")

    def test_public_user_cannot_reach_booking_and_staff_page_has_no_secrets(self) -> None:
        self.client.cookies.clear()
        self.assertEqual(self.client.post("/api/warp/book", json={}).status_code, 404)
        self.assertEqual(self.client.post("/api/staff/book", json={}).status_code, 401)
        public_javascript = Path("static/app.js").read_text(encoding="utf-8")
        staff_bundle = "".join(
            Path(path).read_text(encoding="utf-8")
            for path in ("static/staff.html", "static/staff.js")
        )
        self.assertNotIn("/book", public_javascript)
        for forbidden in (
            "correct-horse",
            "wak_test_",
            "wak_live_",
            "STAFF_USERNAME",
            "STAFF_PASSWORD",
            "STAFF_SESSION_SECRET",
        ):
            self.assertNotIn(forbidden, staff_bundle)

    def test_staff_frontend_requires_second_confirmation_and_hides_unbookable_actions(self) -> None:
        html = Path("static/staff.html").read_text(encoding="utf-8")
        javascript = Path("static/staff.js").read_text(encoding="utf-8")
        for mode in ("ltl", "ftl", "box-truck", "van"):
            self.assertIn(f'value="{mode}"', html)
        self.assertIn("Book Shipment", javascript)
        self.assertIn("Confirm Booking", html)
        self.assertIn("SECOND CONFIRMATION", html)
        self.assertIn("if (!quote.bookable || !quote.quote_token) return", javascript)
        self.assertIn("disabled>Not Bookable", javascript)
        self.assertIn('fetch("/api/staff/book"', javascript)
        self.assertIn("staffState.bookingPending", javascript)
        self.assertIn("Production booking is currently disabled.", html)
        self.assertIn("if (!staffState.bookingEnabled)", javascript)
        self.assertIn('headers: { "Content-Type": "application/json", "X-Staff-CSRF"', javascript)

    def test_staff_auth_ui_uses_mutually_exclusive_central_state(self) -> None:
        html = Path("static/staff.html").read_text(encoding="utf-8")
        javascript = Path("static/staff.js").read_text(encoding="utf-8")
        css = Path("static/staff.css").read_text(encoding="utf-8")

        self.assertIn('id="authLoading"', html)
        self.assertIn('id="loginView" class="login-shell" hidden', html)
        self.assertIn('id="panelView" class="workspace" hidden', html)
        self.assertIn("[hidden] { display:none !important; }", css)
        self.assertIn("function renderAuthState(authState, session = {})", javascript)
        self.assertIn('renderAuthState("checking")', javascript)
        self.assertIn('renderAuthState("anonymous")', javascript)
        self.assertIn('renderAuthState("authenticated", {', javascript)
        self.assertIn('$("#loginView").hidden = checking || authenticated', javascript)
        self.assertIn('$("#panelView").hidden = !authenticated', javascript)
        self.assertNotIn("function showAuthenticated", javascript)
        self.assertNotIn("function showLoggedOut", javascript)

    def test_booking_is_disabled_by_default_on_frontend_and_backend(self) -> None:
        csrf = self.login_staff()
        FakeBookingClient.call_count = 0
        FakeBookingClient.last_url = None
        with patch.object(app_module.httpx, "AsyncClient", FakeBookingClient):
            response = self.client.post(
                "/api/staff/book",
                json={"quote_token": "not-needed-while-disabled"},
                headers={"X-Staff-CSRF": csrf},
            )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["detail"], "Production booking is currently disabled.")
        self.assertEqual(FakeBookingClient.call_count, 0)
        self.assertIsNone(FakeBookingClient.last_url)

    def test_sandbox_booking_uses_documented_body_and_is_idempotent(self) -> None:
        csrf = self.login_staff()
        with (
            patch.object(app_module.httpx, "AsyncClient", FakeAsyncClient),
            patch.object(app_module, "WARP_ENV", "sandbox"),
            patch.dict(
                os.environ,
                {
                    "WARP_SANDBOX_KEY": "wak_test_backend_only",
                    "STAFF_BOOKING_ENABLED": "true",
                },
            ),
        ):
            quote_response = self.client.post(
                "/api/staff/quote/ltl",
                json=self.payload,
                headers={"X-Staff-CSRF": csrf},
            )
        quote_token_value = quote_response.json()["options"][1]["quote_token"]

        with (
            patch.object(app_module.httpx, "AsyncClient", FakeBookingClient),
            patch.object(app_module, "WARP_ENV", "sandbox"),
            patch.dict(
                os.environ,
                {
                    "WARP_SANDBOX_KEY": "wak_test_backend_only",
                    "STAFF_BOOKING_ENABLED": "true",
                },
            ),
        ):
            first = self.client.post(
                "/api/staff/book",
                json=self.booking_details(quote_token_value),
                headers={"X-Staff-CSRF": csrf},
            )
            second = self.client.post(
                "/api/staff/book",
                json=self.booking_details(quote_token_value),
                headers={"X-Staff-CSRF": csrf},
            )

        self.assertEqual(first.status_code, 200)
        self.assertTrue(first.json()["ok"])
        self.assertEqual(first.json()["shipment_id"], "sandbox-shipment-123")
        self.assertEqual(first.json()["carrier"], "Carrier B")
        self.assertEqual(first.json()["booked_price"], "425.80")
        self.assertTrue(second.json()["idempotent_replay"])
        self.assertEqual(FakeBookingClient.call_count, 1)
        self.assertEqual(FakeBookingClient.last_url, "https://www.wearewarp.com/api/v1/book")
        self.assertEqual((FakeBookingClient.last_headers or {}).get("Authorization"), "Bearer wak_test_backend_only")
        sent = FakeBookingClient.last_json or {}
        self.assertEqual(sent["quote_id"], "quote-b")
        self.assertEqual(sent["reference"], "PO-12345")
        self.assertEqual(sent["patch"]["pickup"]["state"], "CA")
        self.assertEqual(sent["pickup_window"], {"from": "08:00", "to": "12:00"})
        self.assertNotIn("quote_token", sent)

    def test_tampered_quote_token_is_rejected_before_warp(self) -> None:
        csrf = self.login_staff()
        token = app_module.quote_token(
            {"username": "staff-user"},
            {
                "quote_id": "sandbox-quote",
                "carrier_name": "Sandbox Carrier",
                "price_usd": 100,
                "bookable": True,
            },
            "ltl",
            self.payload,
        )
        encoded, signature = token.split(".", 1)
        tampered_signature = ("A" if signature[0] != "A" else "B") + signature[1:]
        FakeBookingClient.last_url = None
        with patch.dict(os.environ, {"STAFF_BOOKING_ENABLED": "true"}):
            response = self.client.post(
                "/api/staff/book",
                json=self.booking_details(f"{encoded}.{tampered_signature}"),
                headers={"X-Staff-CSRF": csrf},
            )
        self.assertEqual(response.status_code, 401)
        self.assertIsNone(FakeBookingClient.last_url)

    def test_non_ltl_quote_routes_are_preserved(self) -> None:
        self.assertEqual(app_module.ALLOWED_ACTIONS["quote-ftl"][1], "/ftl/quote")
        self.assertEqual(app_module.ALLOWED_ACTIONS["quote-box"][1], "/box-truck/quote")
        self.assertEqual(app_module.ALLOWED_ACTIONS["quote-van"][1], "/van/quote")

        javascript = Path("static/app.js").read_text(encoding="utf-8")
        self.assertIn('carrier_name: "Lodestar Logistics"', javascript)
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
            "Origin ZIP",
            "Destination ZIP",
            "Pickup Date",
            "Number of Pallets",
            "Total Weight",
            "Commodity",
            "Freight Class",
            "Hazmat",
            "Shipment contains regulated hazardous materials",
            "Stackable",
            "Pallets can be safely stacked",
            "Liftgate Pickup",
            "Residential Delivery",
            "Two-Man Delivery",
            'value="driver-assist-pickup"',
            'value="driver-assist-delivery"',
        ):
            self.assertIn(expected, html)
        self.assertIn("Commodity <b>Optional</b>", html)
        self.assertNotIn("Weight per Pallet", html)
        self.assertGreaterEqual(html.count('class="required-mark"'), 9)

    def test_freight_type_requires_an_explicit_selection(self) -> None:
        html = Path("static/index.html").read_text(encoding="utf-8")
        javascript = Path("static/app.js").read_text(encoding="utf-8")
        for value in ("quote-ltl-market", "quote-ftl"):
            self.assertIn(f'name="quoteType" value="{value}" required', html)
        self.assertNotIn('value="quote-ltl-market" required checked', html)
        self.assertIn("Select LTL or FTL before requesting a quote.", html)
        self.assertIn("function validateFreightType()", javascript)
        self.assertIn('return $(\'input[name="quoteType"]:checked\')?.value || "";', javascript)
        self.assertNotIn('?.value || "quote-ltl-market"', javascript)

    def test_mobile_offer_cards_can_shrink_and_wrap_long_text(self) -> None:
        css = Path("static/style.css").read_text(encoding="utf-8")
        self.assertIn(".offer-card { position: relative; min-width: 0;", css)
        self.assertIn(".offer-grid { grid-template-columns: minmax(0, 1fr); }", css)
        self.assertIn(".carrier-heading h3", css)
        self.assertIn("overflow-wrap: anywhere", css)
        self.assertIn("white-space: normal", css)

    def test_competitive_rate_estimate_uses_lowest_dynamic_sample(self) -> None:
        javascript = Path("static/app.js").read_text(encoding="utf-8")
        self.assertIn("Competitive Rate Estimate", javascript)
        self.assertIn("Math.min(10, Math.max(3, Math.ceil(validQuoteCount / 3)))", javascript)
        self.assertIn("validPrices.slice(0, sampleCount)", javascript)
        self.assertIn("Calculated from the lowest ${estimate.sampleCount} of ${estimate.validQuoteCount}", javascript)
        self.assertIn("not an actual carrier quote and cannot be selected", javascript)

    def test_selected_quote_modal_contains_only_requested_summary_fields(self) -> None:
        javascript = Path("static/app.js").read_text(encoding="utf-8")
        start = javascript.index("function summaryMarkup")
        end = javascript.index("function openQuoteDialog", start)
        summary = javascript[start:end]
        for label in ("Carrier", "Price", "Transit Time", "Service Level", "Quote ID"):
            self.assertIn(f"<span>{label}</span>", summary)
        self.assertNotIn("Freight Type", summary)

    def test_customer_copy_uses_lodestar_logistics(self) -> None:
        customer_copy = "".join(
            Path(path).read_text(encoding="utf-8")
            for path in ("static/index.html", "static/app.js")
        )
        self.assertNotIn("Lodestar Freight", customer_copy)
        self.assertIn("Lodestar Logistics", customer_copy)

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
