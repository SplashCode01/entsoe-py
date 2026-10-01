"""Fetch one day of Dutch day-ahead prices and save the raw XML as a test fixture.

Run with:  uv run python scripts/fetch_sample.py
"""

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

from entsoe_py.client import EntsoeClient
from entsoe_py.config import get_settings

NL_BIDDING_ZONE = "10YNL----------L"
FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures"


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)  # httpx logs full URLs, token included

    today = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    start, end = today - timedelta(days=1), today

    params = {
        "documentType": "A44",  # day-ahead prices
        "in_Domain": NL_BIDDING_ZONE,
        "out_Domain": NL_BIDDING_ZONE,
        "contract_MarketAgreement.type": "A01",  # A01 = day-ahead
    }

    with EntsoeClient(get_settings()) as client:
        xml = client.fetch(params, start, end)

    FIXTURES.mkdir(parents=True, exist_ok=True)
    target = FIXTURES / f"day_ahead_prices_nl_{start:%Y%m%d}.xml"
    target.write_bytes(xml)
    print(f"Saved {len(xml):,} bytes to {target}")


if __name__ == "__main__":
    main()
