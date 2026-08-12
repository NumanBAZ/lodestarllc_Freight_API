from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import hmac
import json
import math
import os
import re
import secrets
import time
from datetime import date
from typing import Any

import httpx
from dotenv import load_dotenv
from fastapi import Body, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

load_dotenv()

BASE_URL = os.getenv("WARP_BASE_URL", "https://www.wearewarp.com/api/v1").rstrip("/")
WARP_ENV = os.getenv("WARP_ENV", "sandbox").strip().lower()
STAFF_COOKIE_NAME = "lodestar_staff_session"
STAFF_SESSION_SECONDS = 8 * 60 * 60
STAFF_QUOTE_TOKEN_SECONDS = 2 * 60 * 60

STAFF_QUOTE_PATHS = {
    "ltl": "/ltl/market-options",
    "ftl": "/ftl/quote",
    "box-truck": "/box-truck/quote",
    "van": "/van/quote",
}

BOOKING_RESULTS: dict[str, dict[str, Any]] = {}
BOOKINGS_IN_PROGRESS: set[str] = set()
BOOKING_LOCK = asyncio.Lock()

LTL_MARKET_FIELDS = (
    "origin_zip",
    "destination_zip",
    "pickup_date",
    "pallets",
    "weight_lbs_per_pallet",
    "length_in",
    "width_in",
    "height_in",
    "pickup_services",
    "delivery_services",
)

app = FastAPI(
    title="WARP Freight Quotes",
    description="Customer freight quotes with a protected staff booking workflow.",
    version="2.1.0",
)

# Booking-related endpoints are intentionally NOT exposed.
ALLOWED_ACTIONS: dict[str, tuple[str, str, bool, float]] = {
    # Quotes
    "quote-all": ("POST", "/quote", False, 60.0),
    "quote-ltl": ("POST", "/ltl/quote", False, 60.0),
    "quote-ltl-market": ("POST", "/ltl/market-options", False, 60.0),
    "quote-ftl": ("POST", "/ftl/quote", False, 60.0),
    "quote-van": ("POST", "/van/quote", False, 60.0),
    "quote-box": ("POST", "/box-truck/quote", False, 60.0),

    # Public tools
    "tool-freight-class": ("POST", "/tools/freight-class", False, 30.0),
    "tool-density": ("POST", "/tools/density", False, 30.0),
    "tool-dim-weight": ("POST", "/tools/dim-weight", False, 30.0),
    "tool-cbm": ("POST", "/tools/cbm", False, 30.0),
    "tool-rate-per-mile": ("POST", "/tools/rate-per-mile", False, 30.0),
    "tool-pallet-weight": ("POST", "/tools/pallet-weight", False, 30.0),
    "tool-truck-payload": ("POST", "/tools/truck-payload", False, 30.0),
    "tool-freight-insurance": ("POST", "/tools/freight-insurance", False, 30.0),
    "tool-fuel-surcharge": ("POST", "/tools/fuel-surcharge", False, 30.0),
    "tool-accessorial": ("POST", "/tools/accessorial", False, 30.0),
    "tool-container-load": ("POST", "/tools/container-load", False, 30.0),

    # Read-only diagnostics
    "version": ("GET", "/version", False, 30.0),
    "rate-card": ("GET", "/rate-card", True, 30.0),
    "quote-history": ("GET", "/quote-history", True, 30.0),
    "balance": ("GET", "/balance", True, 30.0),
    "lanes": ("GET", "/lanes", True, 30.0),
    "locations": ("GET", "/locations", True, 30.0),
}


def api_key_for_configured_environment() -> str | None:
    """Read the selected WARP credential only from backend configuration."""
    if WARP_ENV == "sandbox":
        return os.getenv("WARP_SANDBOX_KEY")
    if WARP_ENV == "live":
        return os.getenv("WARP_LIVE_KEY")
    raise HTTPException(status_code=500, detail="Server environment is not configured correctly")


def staff_configuration() -> tuple[str, str, str]:
    username = os.getenv("STAFF_USERNAME", "").strip()
    password = os.getenv("STAFF_PASSWORD", "")
    session_secret = os.getenv("STAFF_SESSION_SECRET", "")
    if not username or not password or len(session_secret) < 32:
        raise HTTPException(status_code=503, detail="Staff access is not configured")
    return username, password, session_secret


def staff_booking_enabled() -> bool:
    """Keep production booking off unless the backend explicitly enables it."""
    return os.getenv("STAFF_BOOKING_ENABLED", "false").strip().lower() == "true"


def encode_signed_payload(payload: dict[str, Any]) -> str:
    _, _, secret = staff_configuration()
    encoded = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).decode("ascii").rstrip("=")
    signature = hmac.new(secret.encode("utf-8"), encoded.encode("ascii"), hashlib.sha256).digest()
    encoded_signature = base64.urlsafe_b64encode(signature).decode("ascii").rstrip("=")
    return f"{encoded}.{encoded_signature}"


def decode_signed_payload(token: str, purpose: str) -> dict[str, Any]:
    try:
        encoded, encoded_signature = token.split(".", 1)
        _, _, secret = staff_configuration()
        expected = hmac.new(
            secret.encode("utf-8"), encoded.encode("ascii"), hashlib.sha256
        ).digest()
        supplied = base64.urlsafe_b64decode(encoded_signature + "=" * (-len(encoded_signature) % 4))
        if not hmac.compare_digest(expected, supplied):
            raise ValueError("Invalid signature")
        payload = json.loads(
            base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)).decode("utf-8")
        )
        if payload.get("purpose") != purpose or int(payload.get("expires_at", 0)) < int(time.time()):
            raise ValueError("Expired or invalid token")
        return payload
    except (TypeError, ValueError, KeyError, UnicodeDecodeError, binascii.Error) as exc:
        raise HTTPException(status_code=401, detail="Staff session is invalid or expired") from exc


def require_staff(request: Request) -> dict[str, Any]:
    token = request.cookies.get(STAFF_COOKIE_NAME, "")
    if not token:
        raise HTTPException(status_code=401, detail="Staff authentication required")
    return decode_signed_payload(token, "staff-session")


def require_csrf(request: Request, session: dict[str, Any]) -> None:
    supplied = request.headers.get("X-Staff-CSRF", "")
    expected = str(session.get("csrf") or "")
    if not expected or not secrets.compare_digest(supplied, expected):
        raise HTTPException(status_code=403, detail="Invalid staff request token")


def cookie_is_secure(request: Request) -> bool:
    configured = os.getenv("STAFF_COOKIE_SECURE", "").strip().lower()
    if configured:
        return configured in {"1", "true", "yes", "on"}
    forwarded_proto = request.headers.get("x-forwarded-proto", "").split(",", 1)[0].strip()
    return request.url.scheme == "https" or forwarded_proto == "https"


def staff_session_payload(username: str) -> dict[str, Any]:
    return {
        "purpose": "staff-session",
        "username": username,
        "csrf": secrets.token_urlsafe(24),
        "expires_at": int(time.time()) + STAFF_SESSION_SECONDS,
    }


def market_price(option: Any) -> float:
    """Return a sortable numeric price while keeping missing prices last."""
    if not isinstance(option, dict):
        return float("inf")

    value = option.get("price_usd")
    if isinstance(value, str):
        value = value.replace("$", "").replace(",", "").strip()

    try:
        return float(value)
    except (TypeError, ValueError):
        return float("inf")


def normalize_quote_body(body: dict[str, Any]) -> dict[str, Any]:
    """Validate customer quote fields and derive WARP's per-pallet weight."""
    errors: list[str] = []

    origin_zip = str(body.get("origin_zip") or "").strip()
    destination_zip = str(body.get("destination_zip") or "").strip()
    if len(origin_zip) != 5 or not origin_zip.isdigit():
        errors.append("Origin ZIP must be a valid 5-digit ZIP code.")
    if len(destination_zip) != 5 or not destination_zip.isdigit():
        errors.append("Destination ZIP must be a valid 5-digit ZIP code.")

    pickup_date = str(body.get("pickup_date") or "").strip()
    try:
        date.fromisoformat(pickup_date)
    except ValueError:
        errors.append("Pickup Date must be a valid date.")

    pallets = body.get("pallets")
    pallets_invalid = (
        isinstance(pallets, bool)
        or not isinstance(pallets, (int, float))
        or pallets <= 0
        or not float(pallets).is_integer()
    )
    if pallets_invalid:
        errors.append("Number of Pallets must be a whole number greater than zero.")

    positive_fields = {
        "total_weight_lbs": "Total Weight",
        "length_in": "Length",
        "width_in": "Width",
        "height_in": "Height",
    }
    for field, label in positive_fields.items():
        value = body.get(field)
        value_invalid = (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            or value <= 0
        )
        if value_invalid:
            errors.append(f"{label} must be greater than zero.")

    if not str(body.get("freight_class") or "").strip():
        errors.append("Freight Class is required.")

    if errors:
        raise HTTPException(status_code=400, detail=errors)

    normalized = dict(body)
    normalized["pallets"] = int(pallets)
    normalized["weight_lbs_per_pallet"] = math.ceil(
        float(normalized.pop("total_weight_lbs")) / normalized["pallets"]
    )
    return normalized


def filtered_ltl_body(body: dict[str, Any]) -> dict[str, Any]:
    return {
        **{field: body.get(field) for field in LTL_MARKET_FIELDS[:8]},
        "pickup_services": body.get("pickup_services") or [],
        "delivery_services": body.get("delivery_services") or [],
    }


def warp_headers(*, require_key: bool = False) -> dict[str, str]:
    key = api_key_for_configured_environment()
    if require_key and not key:
        raise HTTPException(status_code=503, detail="The WARP credential is not configured")
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "lodestar-logistics/2.1",
    }
    if key:
        headers["Authorization"] = f"Bearer {key}"
    return headers


async def post_to_warp(
    path: str,
    body: dict[str, Any],
    *,
    timeout_seconds: float = 60.0,
    require_key: bool = False,
) -> tuple[httpx.Response, Any]:
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_seconds, connect=10.0),
            follow_redirects=True,
        ) as client:
            response = await client.post(
                f"{BASE_URL}{path}",
                headers=warp_headers(require_key=require_key),
                json=body,
            )
    except httpx.TimeoutException as exc:
        raise HTTPException(status_code=504, detail="WARP request timed out") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="WARP network request failed") from exc

    try:
        data: Any = response.json()
    except ValueError:
        data = {"error": "WARP returned an unreadable response"}
    return response, data


def quote_token(
    session: dict[str, Any],
    option: dict[str, Any],
    mode: str,
    quote_body: dict[str, Any],
) -> str:
    protected_quote = {
        "mode": mode,
        "quote_id": option.get("quote_id"),
        "option_id": option.get("option_id"),
        "carrier_name": option.get("carrier_name") or "Lodestar Logistics",
        "price_usd": option.get("price_usd"),
        "transit_days": option.get("transit_days"),
        "service_level": option.get("service_level") or option.get("mode"),
        "bookable": option.get("bookable") is True,
        "accessorials": {
            "pickup": quote_body.get("pickup_services")
            or (quote_body.get("accessorials") or {}).get("pickup")
            or [],
            "delivery": quote_body.get("delivery_services")
            or (quote_body.get("accessorials") or {}).get("delivery")
            or [],
        },
        "shipment": {
            field: quote_body.get(field)
            for field in (
                "origin_zip",
                "destination_zip",
                "pickup_date",
                "pallets",
                "weight_lbs_per_pallet",
                "length_in",
                "width_in",
                "height_in",
                "freight_class",
                "commodity",
            )
            if quote_body.get(field) not in (None, "")
        },
    }
    return encode_signed_payload(
        {
            "purpose": "staff-quote",
            "username": session["username"],
            "quote": protected_quote,
            "expires_at": int(time.time()) + STAFF_QUOTE_TOKEN_SECONDS,
        }
    )


def staff_quote_option(
    session: dict[str, Any],
    option: dict[str, Any],
    mode: str,
    quote_body: dict[str, Any],
) -> dict[str, Any]:
    bookable = option.get("bookable") is True and bool(option.get("quote_id"))
    safe_option = {
        "carrier_name": option.get("carrier_name") or "Lodestar Logistics",
        "price_usd": option.get("price_usd"),
        "transit_days": option.get("transit_days"),
        "service_level": option.get("service_level") or option.get("mode"),
        "quote_id": option.get("quote_id"),
        "option_id": option.get("option_id"),
        "bookable": bookable,
    }
    if bookable:
        safe_option["quote_token"] = quote_token(
            session, {**option, "bookable": True}, mode, quote_body
        )
    return safe_option


def clean_text(value: Any, *, maximum: int = 200) -> str:
    text = str(value or "").strip()
    if len(text) > maximum:
        raise HTTPException(status_code=400, detail="A booking field is too long")
    return text


def validate_booking_stop(value: Any, label: str) -> dict[str, str]:
    if not isinstance(value, dict):
        raise HTTPException(status_code=400, detail=f"{label} details are required")
    required = ("street", "city", "state", "zipCode", "contactName", "phone", "email")
    stop = {field: clean_text(value.get(field)) for field in required}
    missing = [field for field, content in stop.items() if not content]
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"{label} is missing required fields: {', '.join(missing)}",
        )
    if not re.fullmatch(r"\d{5}(?:-\d{4})?", stop["zipCode"]):
        raise HTTPException(status_code=400, detail=f"{label} ZIP code is invalid")
    if not re.fullmatch(r"[A-Za-z]{2}", stop["state"]):
        raise HTTPException(status_code=400, detail=f"{label} state must be a 2-letter code")
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", stop["email"]):
        raise HTTPException(status_code=400, detail=f"{label} email is invalid")
    stop["state"] = stop["state"].upper()
    for optional in ("company", "street2", "specialInstruction", "refNum"):
        content = clean_text(value.get(optional), maximum=500 if optional == "specialInstruction" else 200)
        if content:
            stop[optional] = content
    return stop


def validate_time_window(value: Any, label: str) -> dict[str, str] | None:
    if value in (None, {}):
        return None
    if not isinstance(value, dict):
        raise HTTPException(status_code=400, detail=f"{label} is invalid")
    window = {"from": clean_text(value.get("from")), "to": clean_text(value.get("to"))}
    if not all(re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", item) for item in window.values()):
        raise HTTPException(status_code=400, detail=f"{label} must use HH:MM time values")
    if window["from"] >= window["to"]:
        raise HTTPException(status_code=400, detail=f"{label} end time must be later")
    return window


@app.get("/api/health")
async def health() -> dict[str, Any]:
    return {"ok": True}


@app.get("/api/public-config")
async def public_config() -> dict[str, str]:
    """Expose optional contact details, never credentials or environment data."""
    return {
        "whatsapp": os.getenv("CONTACT_WHATSAPP", "").strip(),
        "phone": os.getenv("CONTACT_PHONE", "").strip(),
        "email": os.getenv("CONTACT_EMAIL", "").strip(),
    }


@app.post("/api/warp/{action}")
async def call_warp(
    action: str,
    body: dict[str, Any] = Body(default_factory=dict),
) -> dict[str, Any]:
    config = ALLOWED_ACTIONS.get(action)
    if not config:
        raise HTTPException(status_code=404, detail="Unknown or blocked action")

    method, path, auth_required, timeout_seconds = config
    # Environment selection is intentionally ignored at the public boundary.
    # It can only be changed through the backend WARP_ENV setting.
    body.pop("_environment", None)
    key = api_key_for_configured_environment()

    if action in {"quote-ltl-market", "quote-ftl"}:
        body = normalize_quote_body(body)

    if action == "quote-ltl-market":
        # Only the documented market-options fields are sent to WARP. Service
        # arrays remain present even when the user has not selected a service.
        body = filtered_ltl_body(body)

    if auth_required and not key:
        raise HTTPException(
            status_code=400,
            detail="The service credential is not configured",
        )

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "warp-api-tester/1.0",
    }
    if key:
        headers["Authorization"] = f"Bearer {key}"

    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_seconds, connect=10.0),
            follow_redirects=True,
        ) as client:
            if method == "GET":
                response = await client.get(f"{BASE_URL}{path}", headers=headers)
            else:
                response = await client.post(
                    f"{BASE_URL}{path}",
                    headers=headers,
                    json=body,
                )
    except httpx.TimeoutException as exc:
        raise HTTPException(
            status_code=504,
            detail="WARP request timed out. LTL market comparison can take 20–45 seconds.",
        ) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Network error: {exc}") from exc

    try:
        data: Any = response.json()
    except ValueError:
        data = {"raw_text": response.text}

    if action == "quote-ltl-market" and isinstance(data, dict):
        market_options = data.get("market_options")
        if isinstance(market_options, list):
            data["market_options"] = sorted(market_options, key=market_price)

    return {
        "ok": response.is_success,
        "status_code": response.status_code,
        "data": data,
    }


@app.post("/api/staff/login")
async def staff_login(request: Request, body: dict[str, Any] = Body(default_factory=dict)) -> Response:
    configured_username, configured_password, _ = staff_configuration()
    username = clean_text(body.get("username"), maximum=100)
    password = str(body.get("password") or "")
    if not (
        secrets.compare_digest(username, configured_username)
        and secrets.compare_digest(password, configured_password)
    ):
        raise HTTPException(status_code=401, detail="Invalid username or password")

    session = staff_session_payload(configured_username)
    response = JSONResponse(
        {
            "ok": True,
            "username": configured_username,
            "csrf_token": session["csrf"],
            "booking_enabled": staff_booking_enabled(),
        }
    )
    response.set_cookie(
        STAFF_COOKIE_NAME,
        encode_signed_payload(session),
        max_age=STAFF_SESSION_SECONDS,
        httponly=True,
        secure=cookie_is_secure(request),
        samesite="strict",
        path="/",
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/api/staff/session")
async def staff_session(request: Request, response: Response) -> dict[str, Any]:
    session = require_staff(request)
    response.headers["Cache-Control"] = "no-store"
    return {
        "authenticated": True,
        "username": session["username"],
        "csrf_token": session["csrf"],
        "booking_enabled": staff_booking_enabled(),
    }


@app.post("/api/staff/logout")
async def staff_logout(request: Request) -> Response:
    session = require_staff(request)
    require_csrf(request, session)
    response = JSONResponse({"ok": True})
    response.delete_cookie(STAFF_COOKIE_NAME, path="/", samesite="strict")
    response.headers["Cache-Control"] = "no-store"
    return response


@app.post("/api/staff/quote/{mode}")
async def staff_quote(
    mode: str,
    request: Request,
    body: dict[str, Any] = Body(default_factory=dict),
) -> dict[str, Any]:
    session = require_staff(request)
    require_csrf(request, session)
    path = STAFF_QUOTE_PATHS.get(mode)
    if not path:
        raise HTTPException(status_code=404, detail="Unknown freight mode")

    normalized = normalize_quote_body(dict(body))
    if mode == "ltl":
        outgoing = filtered_ltl_body(normalized)
    else:
        outgoing = normalized
        pickup_services = outgoing.pop("pickup_services", []) or []
        delivery_services = outgoing.pop("delivery_services", []) or []
        if pickup_services or delivery_services:
            outgoing["accessorials"] = {
                "pickup": pickup_services,
                "delivery": delivery_services,
            }

    response, data = await post_to_warp(path, outgoing)
    if not response.is_success:
        return {"ok": False, "status_code": response.status_code, "data": data}

    if mode == "ltl":
        raw_options = data.get("market_options", []) if isinstance(data, dict) else []
        options = [
            staff_quote_option(session, option, mode, outgoing)
            for option in raw_options
            if isinstance(option, dict)
        ]
        options.sort(key=market_price)
    else:
        raw_option = data if isinstance(data, dict) else {}
        if "bookable" not in raw_option:
            raw_option = {**raw_option, "bookable": bool(raw_option.get("quote_id"))}
        options = [staff_quote_option(session, raw_option, mode, outgoing)]

    return {"ok": True, "status_code": response.status_code, "options": options}


@app.post("/api/staff/book")
async def staff_book(
    request: Request,
    body: dict[str, Any] = Body(default_factory=dict),
) -> dict[str, Any]:
    session = require_staff(request)
    require_csrf(request, session)
    if not staff_booking_enabled():
        raise HTTPException(status_code=403, detail="Production booking is currently disabled.")
    signed_quote = decode_signed_payload(
        clean_text(body.get("quote_token"), maximum=12000), "staff-quote"
    )
    if signed_quote.get("username") != session.get("username"):
        raise HTTPException(status_code=403, detail="This quote belongs to another staff session")
    quote = signed_quote.get("quote")
    if not isinstance(quote, dict) or quote.get("bookable") is not True or not quote.get("quote_id"):
        raise HTTPException(status_code=400, detail="The selected quote is not bookable")

    booking_body: dict[str, Any] = {
        "quote_id": quote["quote_id"],
        "patch": {
            "pickup": validate_booking_stop(body.get("pickup"), "Pickup"),
            "delivery": validate_booking_stop(body.get("delivery"), "Delivery"),
        },
    }
    notes = clean_text(body.get("notes"), maximum=1000)
    if notes:
        booking_body["patch"]["notes"] = notes
    reference = clean_text(body.get("reference"))
    if reference:
        booking_body["reference"] = reference
    accessorials = quote.get("accessorials")
    if isinstance(accessorials, dict) and (
        accessorials.get("pickup") or accessorials.get("delivery")
    ):
        booking_body["accessorials"] = accessorials
    for field, label in (
        ("pickup_window", "Pickup window"),
        ("delivery_window", "Delivery window"),
    ):
        window = validate_time_window(body.get(field), label)
        if window:
            booking_body[field] = window

    quote_id = str(quote["quote_id"])
    async with BOOKING_LOCK:
        if quote_id in BOOKING_RESULTS:
            return {**BOOKING_RESULTS[quote_id], "idempotent_replay": True}
        if quote_id in BOOKINGS_IN_PROGRESS:
            raise HTTPException(status_code=409, detail="Booking is already in progress")
        BOOKINGS_IN_PROGRESS.add(quote_id)

    try:
        response, data = await post_to_warp("/book", booking_body, require_key=True)
        if not response.is_success:
            return {"ok": False, "status_code": response.status_code, "data": data}
        if response.status_code == 202:
            return {
                "ok": False,
                "status_code": 202,
                "booking_status": "pending_owner_confirmation",
                "data": data,
            }
        if not isinstance(data, dict) or (
            data.get("booked") is not True
            and not data.get("shipment_id")
            and not data.get("shipment_number")
        ):
            return {
                "ok": False,
                "status_code": response.status_code,
                "data": data,
            }

        result = {
            "ok": True,
            "status_code": response.status_code,
            "shipment_id": data.get("shipment_id") or data.get("shipment_number"),
            "booking_status": data.get("status") or ("booked" if data.get("booked") else "confirmed"),
            "tracking_number": data.get("tracking_number"),
            "tracking_dashboard": data.get("tracking_dashboard"),
            "carrier": quote.get("carrier_name"),
            "booked_price": data.get("price_usd") or data.get("booked_price") or quote.get("price_usd"),
        }
        async with BOOKING_LOCK:
            BOOKING_RESULTS[quote_id] = result
        return result
    finally:
        async with BOOKING_LOCK:
            BOOKINGS_IN_PROGRESS.discard(quote_id)


app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
async def index() -> FileResponse:
    return FileResponse("static/index.html")


@app.get("/staff")
async def staff_index() -> FileResponse:
    return FileResponse("static/staff.html")
