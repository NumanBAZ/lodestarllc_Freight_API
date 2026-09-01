from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone

import qa_public_flow_app as qa


statuses = ("new", "approved", "rejected", "booked")
for index in range(100):
    request_id = f"qr_browser{index:03d}abcdefghijklmnop"
    qa.records[request_id] = {
        "id": request_id,
        "customer": {
            "full_name": f"Browser Customer {index:03d}",
            "email": f"browser{index:03d}@example.com",
            "phone": "+1 213 555 0199",
        },
        "freight_type": "FTL" if index % 2 else "LTL",
        "shipment": {
            "origin_zip": "90012",
            "origin_city": "Los Angeles",
            "origin_state": "CA",
            "destination_zip": "94105",
            "destination_city": "San Francisco",
            "destination_state": "CA",
            "pickup_date": "2026-09-03",
            "pallets": 2,
            "total_weight_lbs": 1000,
            "weight_lbs_per_pallet": 500,
            "length_in": 48,
            "width_in": 40,
            "height_in": 48,
            "freight_class": "70",
            "commodity": "QA freight",
            "accessorials": {"pickup": [], "delivery": []},
        },
        "selected_quote": {
            "carrier_name": f"QA Carrier {index % 5}",
            "price_usd": 300 + index,
            "service_level": "Standard",
            "transit_days": 2,
            "quote_id": f"qa-quote-{index:03d}",
            "option_id": f"qa-option-{index:03d}",
            "bookable": True,
        },
        "created_at": (
            datetime(2026, 8, 1, tzinfo=timezone.utc) + timedelta(hours=index)
        ).isoformat(),
        "status": statuses[index % len(statuses)],
        "shipment_id": None,
    }


async def list_quote_requests(limit: int = 100) -> list[dict[str, object]]:
    return list(qa.records.values())[:limit]


async def update_quote_request(record: dict[str, object]) -> None:
    qa.records[str(record["id"])] = copy.deepcopy(record)


qa.production.list_quote_requests = list_quote_requests
qa.production.update_quote_request = update_quote_request
app = qa.app
