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
from datetime import date, datetime, timezone
from functools import lru_cache
from pathlib import Path
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
PUBLIC_QUOTE_TOKEN_SECONDS = 2 * 60 * 60
QUOTE_REQUEST_INDEX_KEY = "lodestar:quote-requests"
QUOTE_REQUEST_KEY_PREFIX = "lodestar:quote-request:"
LOCATION_DATA_PATH = Path(__file__).resolve().parent / "data" / "us_zip_codes.json"
POPULAR_LOCATION_ZIPS = ("90012", "94105", "10001", "33101", "60601", "85001")
LOCATION_RESULT_LIMIT = 25
LOCATION_METADATA_FIELDS = (
    "origin_city",
    "origin_state",
    "destination_city",
    "destination_state",
)
QUOTE_REQUEST_STATUSES = {"new", "approved", "rejected", "booked"}
STAFF_STATUS_TRANSITIONS = {
    "new": {"approved", "rejected"},
    "rejected": {"approved"},
}

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


def encode_payload_with_secret(payload: dict[str, Any], secret: str) -> str:
    encoded = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).decode("ascii").rstrip("=")
    signature = hmac.new(secret.encode("utf-8"), encoded.encode("ascii"), hashlib.sha256).digest()
    encoded_signature = base64.urlsafe_b64encode(signature).decode("ascii").rstrip("=")
    return f"{encoded}.{encoded_signature}"


def decode_payload_with_secret(token: str, purpose: str, secret: str) -> dict[str, Any]:
    try:
        encoded, encoded_signature = token.split(".", 1)
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


def encode_signed_payload(payload: dict[str, Any]) -> str:
    _, _, secret = staff_configuration()
    return encode_payload_with_secret(payload, secret)


def decode_signed_payload(token: str, purpose: str) -> dict[str, Any]:
    _, _, secret = staff_configuration()
    return decode_payload_with_secret(token, purpose, secret)


def quote_request_signing_secret() -> str:
    secret = os.getenv("QUOTE_REQUEST_SIGNING_SECRET", "")
    if len(secret) < 32:
        raise HTTPException(status_code=503, detail="Quote request signing is not configured")
    return secret


def encode_public_quote_payload(payload: dict[str, Any]) -> str:
    return encode_payload_with_secret(payload, quote_request_signing_secret())


def decode_public_quote_payload(token: str) -> dict[str, Any]:
    try:
        return decode_payload_with_secret(token, "public-quote", quote_request_signing_secret())
    except HTTPException as exc:
        if exc.status_code == 503:
            raise
        raise HTTPException(status_code=400, detail="The selected quote is invalid or expired") from exc


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
        parsed_pickup_date = date.fromisoformat(pickup_date)
        if parsed_pickup_date < date.today():
            errors.append("Pickup Date cannot be in the past.")
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


def display_city(value: str) -> str:
    return " ".join(part.capitalize() for part in value.strip().split())


@lru_cache(maxsize=1)
def us_location_records() -> tuple[dict[str, dict[str, str]], tuple[dict[str, str], ...]]:
    try:
        raw = json.loads(LOCATION_DATA_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=503, detail="The local US location dataset is unavailable.") from exc

    by_zip: dict[str, dict[str, str]] = {}
    records: list[dict[str, str]] = []
    for zip_code, location in raw.items() if isinstance(raw, dict) else ():
        city = str(location.get("city") or "").strip() if isinstance(location, dict) else ""
        state = str(location.get("state") or "").strip().upper() if isinstance(location, dict) else ""
        if not re.fullmatch(r"\d{5}", str(zip_code)) or not city or not re.fullmatch(r"[A-Z]{2}", state):
            continue
        record = {"zip": str(zip_code), "city": display_city(city), "state": state}
        by_zip[record["zip"]] = record
        records.append(record)
    records.sort(key=lambda record: record["zip"])
    return by_zip, tuple(records)


async def resolve_us_locations(query: str) -> list[dict[str, str]]:
    value = str(query or "").strip()
    if not re.fullmatch(r"\d{5}", value):
        return []
    record = us_location_records()[0].get(value)
    return [dict(record)] if record else []


@lru_cache(maxsize=256)
def search_us_locations(query: str) -> tuple[dict[str, str], ...]:
    value = " ".join(str(query or "").strip().split())
    by_zip, records = us_location_records()
    if not value:
        return tuple(dict(by_zip[zip_code]) for zip_code in POPULAR_LOCATION_ZIPS if zip_code in by_zip)
    if len(value) > 80 or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 .,'-]*", value):
        raise HTTPException(status_code=400, detail="Enter a US city, state, or ZIP prefix.")

    if value.isdigit():
        if len(value) > 5:
            return ()
        ranked_matches = [(0, record) for record in records if record["zip"].startswith(value)]
    else:
        normalized = value.upper().replace(".", "")
        city_query, separator, state_query = normalized.partition(",")
        city_query = city_query.strip()
        state_query = state_query.strip()
        if separator and (len(state_query) > 2 or not state_query.isalpha()):
            return ()

        def text_score(record: dict[str, str]) -> int | None:
            city = record["city"].upper()
            if separator:
                if not city.startswith(city_query) or not record["state"].startswith(state_query):
                    return None
                return 0 if city == city_query else (1 if city.startswith(f"{city_query} ") else 2)
            if city == normalized:
                return 0
            if city.startswith(f"{normalized} "):
                return 1
            if f"{city} {record['state']}".startswith(normalized):
                return 2
            if city.startswith(normalized):
                return 3
            return None

        ranked_matches = [
            (score, record)
            for record in records
            if (score := text_score(record)) is not None
        ]
        city_counts: dict[tuple[str, str], int] = {}
        for _, record in ranked_matches:
            identity = (record["city"], record["state"])
            city_counts[identity] = city_counts.get(identity, 0) + 1
        ranked_matches.sort(
            key=lambda match: (
                match[0],
                -city_counts[(match[1]["city"], match[1]["state"])],
                match[1]["city"],
                match[1]["state"],
                match[1]["zip"],
            )
        )

    return tuple(dict(record) for _, record in ranked_matches[:LOCATION_RESULT_LIMIT])


async def resolve_quote_locations(body: dict[str, Any]) -> dict[str, Any]:
    origin_options, destination_options = await asyncio.gather(
        resolve_us_locations(str(body.get("origin_zip") or "")),
        resolve_us_locations(str(body.get("destination_zip") or "")),
    )
    if not origin_options:
        raise HTTPException(status_code=400, detail="Origin ZIP is not a valid US ZIP code.")
    if not destination_options:
        raise HTTPException(status_code=400, detail="Destination ZIP is not a valid US ZIP code.")
    origin = origin_options[0]
    destination = destination_options[0]
    return {
        **body,
        "origin_zip": origin["zip"],
        "origin_city": origin["city"],
        "origin_state": origin["state"],
        "destination_zip": destination["zip"],
        "destination_city": destination["city"],
        "destination_state": destination["state"],
    }


def without_location_metadata(body: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in body.items() if key not in LOCATION_METADATA_FIELDS}


@app.get("/api/locations/search")
async def search_locations(q: str = "") -> dict[str, Any]:
    return {"options": [dict(option) for option in search_us_locations(q)]}


@app.get("/api/locations/resolve")
async def resolve_location(query: str = "") -> dict[str, Any]:
    """Backward-compatible local lookup alias for older clients."""
    return {"options": [dict(option) for option in search_us_locations(query)]}


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


def upstash_configuration() -> tuple[str, str]:
    url = os.getenv("UPSTASH_REDIS_REST_URL", "").rstrip("/")
    token = os.getenv("UPSTASH_REDIS_REST_TOKEN", "")
    if not url or not token:
        raise HTTPException(status_code=503, detail="Quote request storage is not configured")
    return url, token


async def upstash_command(command: list[Any]) -> Any:
    url, token = upstash_configuration()
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=5.0)) as client:
            response = await client.post(
                url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                json=command,
            )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=503, detail="Quote request storage is unavailable") from exc
    if not response.is_success:
        raise HTTPException(status_code=503, detail="Quote request storage is unavailable")
    payload = response.json()
    if not isinstance(payload, dict) or payload.get("error"):
        raise HTTPException(status_code=503, detail="Quote request storage is unavailable")
    return payload.get("result")


async def upstash_pipeline(commands: list[list[Any]]) -> list[Any]:
    url, token = upstash_configuration()
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=5.0)) as client:
            response = await client.post(
                f"{url}/pipeline",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                json=commands,
            )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=503, detail="Quote request storage is unavailable") from exc
    if not response.is_success:
        raise HTTPException(status_code=503, detail="Quote request storage is unavailable")
    payload = response.json()
    if not isinstance(payload, list) or any(
        not isinstance(item, dict) or item.get("error") for item in payload
    ):
        raise HTTPException(status_code=503, detail="Quote request storage is unavailable")
    return [item.get("result") for item in payload]


def quote_request_key(request_id: str) -> str:
    return f"{QUOTE_REQUEST_KEY_PREFIX}{request_id}"


async def save_quote_request(record: dict[str, Any]) -> None:
    serialized = json.dumps(record, separators=(",", ":"), sort_keys=True)
    created_score = int(datetime.fromisoformat(record["created_at"]).timestamp() * 1000)
    results = await upstash_pipeline(
        [
            ["SET", quote_request_key(record["id"]), serialized],
            ["ZADD", QUOTE_REQUEST_INDEX_KEY, created_score, record["id"]],
        ]
    )
    if results != ["OK", 1]:
        existing = await get_quote_request(record["id"])
        if existing is None:
            raise HTTPException(status_code=503, detail="Quote request could not be saved")


async def get_quote_request(request_id: str) -> dict[str, Any] | None:
    if not re.fullmatch(r"qr_[A-Za-z0-9_-]{16,64}", request_id):
        return None
    serialized = await upstash_command(["GET", quote_request_key(request_id)])
    if serialized is None:
        return None
    try:
        value = json.loads(serialized)
    except (TypeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=503, detail="Quote request storage returned invalid data") from exc
    return value if isinstance(value, dict) else None


async def list_quote_requests(limit: int = 100) -> list[dict[str, Any]]:
    request_ids = await upstash_command(
        ["ZREVRANGE", QUOTE_REQUEST_INDEX_KEY, 0, max(0, min(limit, 100) - 1)]
    )
    if not isinstance(request_ids, list) or not request_ids:
        return []
    serialized_records = await upstash_command(
        ["MGET", *[quote_request_key(str(request_id)) for request_id in request_ids]]
    )
    if not isinstance(serialized_records, list):
        raise HTTPException(status_code=503, detail="Quote request storage returned invalid data")
    records: list[dict[str, Any]] = []
    for serialized in serialized_records:
        if not serialized:
            continue
        try:
            record = json.loads(serialized)
        except (TypeError, json.JSONDecodeError):
            continue
        if isinstance(record, dict):
            records.append(record)
    return records


async def update_quote_request(record: dict[str, Any]) -> None:
    serialized = json.dumps(record, separators=(",", ":"), sort_keys=True)
    result = await upstash_command(["SET", quote_request_key(record["id"]), serialized])
    if result != "OK":
        raise HTTPException(status_code=503, detail="Quote request could not be updated")


def public_quote_snapshot(
    action: str,
    option: dict[str, Any],
    shipment: dict[str, Any],
) -> dict[str, Any]:
    pickup_services = shipment.get("pickup_services") or (
        shipment.get("accessorials") or {}
    ).get("pickup") or []
    delivery_services = shipment.get("delivery_services") or (
        shipment.get("accessorials") or {}
    ).get("delivery") or []
    return {
        "freight_type": "LTL" if action == "quote-ltl-market" else "FTL",
        "shipment": {
            "origin_zip": shipment.get("origin_zip"),
            "origin_city": shipment.get("origin_city"),
            "origin_state": shipment.get("origin_state"),
            "destination_zip": shipment.get("destination_zip"),
            "destination_city": shipment.get("destination_city"),
            "destination_state": shipment.get("destination_state"),
            "pickup_date": shipment.get("pickup_date"),
            "pallets": shipment.get("pallets"),
            "total_weight_lbs": shipment.get("total_weight_lbs"),
            "weight_lbs_per_pallet": shipment.get("weight_lbs_per_pallet"),
            "length_in": shipment.get("length_in"),
            "width_in": shipment.get("width_in"),
            "height_in": shipment.get("height_in"),
            "freight_class": shipment.get("freight_class"),
            "commodity": shipment.get("commodity") or "",
            "accessorials": {
                "pickup": pickup_services,
                "delivery": delivery_services,
            },
        },
        "selected_quote": {
            "carrier_name": option.get("carrier_name") or "Lodestar Logistics",
            "price_usd": option.get("price_usd"),
            "service_level": option.get("service_level") or option.get("mode"),
            "transit_days": option.get("transit_days"),
            "quote_id": option.get("quote_id"),
            "option_id": option.get("option_id"),
            "mode": option.get("mode"),
            "vehicle_type": option.get("vehicle_type") or option.get("equipment_type"),
            "pickup_date": option.get("pickup_date") or option.get("pickup_at"),
            "delivery_date": option.get("delivery_date")
            or option.get("estimated_delivery_date")
            or option.get("delivery_at"),
            "expires_at": option.get("expires_at")
            or option.get("quote_expiration")
            or option.get("expiration"),
            "bookable": bool(option.get("quote_id"))
            and (action != "quote-ltl-market" or option.get("bookable") is True),
        },
    }


def public_quote_request_token(
    action: str,
    option: dict[str, Any],
    shipment: dict[str, Any],
) -> str:
    return encode_public_quote_payload(
        {
            "purpose": "public-quote",
            "quote": public_quote_snapshot(action, option, shipment),
            "expires_at": int(time.time()) + PUBLIC_QUOTE_TOKEN_SECONDS,
        }
    )


def quote_token(
    session: dict[str, Any],
    option: dict[str, Any],
    mode: str,
    quote_body: dict[str, Any],
    request_id: str | None = None,
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
    if request_id:
        protected_quote["customer_request_id"] = request_id
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


@app.post("/api/quote-requests", status_code=201)
async def create_quote_request(
    body: dict[str, Any] = Body(default_factory=dict),
) -> dict[str, Any]:
    full_name = clean_text(body.get("full_name"), maximum=120)
    email = clean_text(body.get("email"), maximum=200).lower()
    phone = clean_text(body.get("phone"), maximum=40)
    if not full_name:
        raise HTTPException(status_code=400, detail="Full Name is required")
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
        raise HTTPException(status_code=400, detail="Enter a valid email address")
    phone_digits = re.sub(r"\D", "", phone)
    if len(phone_digits) < 7 or len(phone_digits) > 15:
        raise HTTPException(status_code=400, detail="Enter a valid phone number")

    public_quote = decode_public_quote_payload(
        clean_text(body.get("request_token"), maximum=16000)
    ).get("quote")
    if not isinstance(public_quote, dict):
        raise HTTPException(status_code=400, detail="The selected quote is invalid or expired")
    shipment = public_quote.get("shipment")
    selected_quote = public_quote.get("selected_quote")
    if not isinstance(shipment, dict) or not isinstance(selected_quote, dict):
        raise HTTPException(status_code=400, detail="The selected quote is invalid or expired")

    request_id = f"qr_{secrets.token_urlsafe(18)}"
    record = {
        "id": request_id,
        "customer": {
            "full_name": full_name,
            "email": email,
            "phone": phone,
        },
        "freight_type": public_quote.get("freight_type"),
        "shipment": shipment,
        "selected_quote": selected_quote,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "new",
        "shipment_id": None,
    }
    await save_quote_request(record)
    return {"ok": True, "request_id": request_id, "status": "new"}


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
    quote_context: dict[str, Any] | None = None

    if action in {"quote-ltl-market", "quote-ftl"}:
        total_weight_lbs = body.get("total_weight_lbs")
        resolved_body = await resolve_quote_locations(normalize_quote_body(body))
        quote_context = {**resolved_body, "total_weight_lbs": total_weight_lbs}
        body = without_location_metadata(resolved_body)

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

    request_signing_ready = len(os.getenv("QUOTE_REQUEST_SIGNING_SECRET", "")) >= 32
    if (
        response.is_success
        and quote_context is not None
        and isinstance(data, dict)
        and request_signing_ready
    ):
        if action == "quote-ltl-market":
            market_options = data.get("market_options")
            if isinstance(market_options, list):
                for option in market_options:
                    if isinstance(option, dict):
                        option["request_token"] = public_quote_request_token(
                            action, option, quote_context
                        )
        elif action == "quote-ftl":
            data["request_token"] = public_quote_request_token(action, data, quote_context)

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


@app.get("/api/staff/quote-requests")
async def staff_quote_requests(request: Request) -> dict[str, Any]:
    session = require_staff(request)
    require_csrf(request, session)
    records = await list_quote_requests()
    records.sort(key=lambda record: record.get("status") != "new")
    return {
        "requests": [
            {
                "id": record.get("id"),
                "customer": (record.get("customer") or {}).get("full_name"),
                "route": {
                    "origin": (record.get("shipment") or {}).get("origin_zip"),
                    "origin_city": (record.get("shipment") or {}).get("origin_city"),
                    "origin_state": (record.get("shipment") or {}).get("origin_state"),
                    "destination": (record.get("shipment") or {}).get("destination_zip"),
                    "destination_city": (record.get("shipment") or {}).get("destination_city"),
                    "destination_state": (record.get("shipment") or {}).get("destination_state"),
                },
                "carrier": (record.get("selected_quote") or {}).get("carrier_name"),
                "price_usd": (record.get("selected_quote") or {}).get("price_usd"),
                "freight_type": record.get("freight_type"),
                "created_at": record.get("created_at"),
                "status": record.get("status"),
            }
            for record in records
        ]
    }


def staff_quote_request_detail_payload(
    record: dict[str, Any], session: dict[str, Any]
) -> dict[str, Any]:
    selected_quote = record.get("selected_quote") or {}
    shipment = record.get("shipment") or {}
    detail = dict(record)
    if (
        record.get("status") == "approved"
        and isinstance(selected_quote, dict)
        and selected_quote.get("bookable") is True
        and selected_quote.get("quote_id")
    ):
        accessorials = shipment.get("accessorials") or {}
        detail["booking_quote_token"] = quote_token(
            session,
            selected_quote,
            str(record.get("freight_type") or "ltl").lower(),
            {
                **shipment,
                "pickup_services": accessorials.get("pickup") or [],
                "delivery_services": accessorials.get("delivery") or [],
            },
            request_id=str(record.get("id")),
        )
    return detail


@app.get("/api/staff/quote-requests/{request_id}")
async def staff_quote_request_detail(request_id: str, request: Request) -> dict[str, Any]:
    session = require_staff(request)
    require_csrf(request, session)
    record = await get_quote_request(request_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Quote request not found")
    return staff_quote_request_detail_payload(record, session)


@app.patch("/api/staff/quote-requests/{request_id}/status")
async def staff_quote_request_status(
    request_id: str,
    request: Request,
    body: dict[str, Any] = Body(default_factory=dict),
) -> dict[str, Any]:
    session = require_staff(request)
    require_csrf(request, session)
    record = await get_quote_request(request_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Quote request not found")

    current_status = clean_text(record.get("status"), maximum=20).lower() or "new"
    next_status = clean_text(body.get("status"), maximum=20).lower()
    if next_status not in QUOTE_REQUEST_STATUSES or next_status == "booked":
        raise HTTPException(status_code=400, detail="Invalid quote request status")
    if next_status not in STAFF_STATUS_TRANSITIONS.get(current_status, set()):
        raise HTTPException(
            status_code=409,
            detail=f"Quote request cannot change from {current_status} to {next_status}",
        )

    if next_status == "rejected":
        record["reject_reason"] = clean_text(body.get("reject_reason"), maximum=1000) or None
    record["status"] = next_status
    record["status_updated_at"] = datetime.now(timezone.utc).isoformat()
    await update_quote_request(record)
    return staff_quote_request_detail_payload(record, session)


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

    normalized = await resolve_quote_locations(normalize_quote_body(dict(body)))
    outgoing_body = without_location_metadata(normalized)
    if mode == "ltl":
        outgoing = filtered_ltl_body(outgoing_body)
    else:
        outgoing = outgoing_body
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

    customer_request: dict[str, Any] | None = None
    customer_request_id = quote.get("customer_request_id")
    if customer_request_id:
        customer_request = await get_quote_request(str(customer_request_id))
        if customer_request is None:
            raise HTTPException(status_code=404, detail="Quote request not found")
        stored_quote = customer_request.get("selected_quote") or {}
        if stored_quote.get("quote_id") != quote.get("quote_id"):
            raise HTTPException(status_code=400, detail="Quote request does not match the selected quote")
        if customer_request.get("status") == "booked":
            return {
                "ok": True,
                "status_code": 200,
                "shipment_id": customer_request.get("shipment_id"),
                "booking_status": "booked",
                "tracking_number": customer_request.get("tracking_number"),
                "carrier": stored_quote.get("carrier_name"),
                "booked_price": customer_request.get("booked_price")
                or stored_quote.get("price_usd"),
                "idempotent_replay": True,
                "customer_request_id": customer_request_id,
            }
        if customer_request.get("status") != "approved":
            raise HTTPException(status_code=409, detail="Quote request cannot be booked")

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
            "customer_request_id": customer_request_id,
        }
        if customer_request is not None:
            customer_request["status"] = "booked"
            customer_request["shipment_id"] = result["shipment_id"]
            customer_request["tracking_number"] = result["tracking_number"]
            customer_request["booked_price"] = result["booked_price"]
            customer_request["booked_at"] = datetime.now(timezone.utc).isoformat()
            await update_quote_request(customer_request)
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
