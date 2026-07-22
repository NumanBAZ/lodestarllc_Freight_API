from __future__ import annotations

import os
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

    if action == "quote-ltl-market":
        required_fields = LTL_MARKET_FIELDS[:8]
        missing_fields = [
            field for field in required_fields if body.get(field) in (None, "")
        ]
        if missing_fields:
            raise HTTPException(
                status_code=400,
                detail=f"Missing LTL market fields: {', '.join(missing_fields)}",
            )

        # Only the documented market-options fields are sent to WARP. Service
        # arrays remain present even when the user has not selected a service.
        body = {
            **{field: body.get(field) for field in required_fields},
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
