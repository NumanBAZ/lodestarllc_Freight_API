from __future__ import annotations

import math
import os
from datetime import date
from typing import Any

import httpx
from dotenv import load_dotenv
from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

load_dotenv()

BASE_URL = os.getenv("WARP_BASE_URL", "https://www.wearewarp.com/api/v1").rstrip("/")
WARP_ENV = os.getenv("WARP_ENV", "sandbox").strip().lower()

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
    description="Customer-facing freight quote comparison.",
    version="2.0.0",
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
        body = {
            **{field: body.get(field) for field in LTL_MARKET_FIELDS[:8]},
            "pickup_services": body.get("pickup_services") or [],
            "delivery_services": body.get("delivery_services") or [],
        }

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


app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
async def index() -> FileResponse:
    return FileResponse("static/index.html")
