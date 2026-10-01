from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
import pytest
from pydantic import SecretStr
from tenacity import wait_none

from entsoe_py.client import (
    EntsoeClient,
    EntsoeRateLimitError,
    EntsoeRequestError,
    EntsoeServerError,
    NoMatchingDataError,
    format_entsoe_time,
    split_range,
)
from entsoe_py.config import Settings

TOKEN = "test-token-123"

NO_DATA_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<Acknowledgement_MarketDocument
    xmlns="urn:iec62325.351:tc57wg16:451-1:acknowledgementdocument:7:0">
  <mRID>abc</mRID>
  <Reason>
    <code>999</code>
    <text>No matching data found for Data item Day-ahead Prices.</text>
  </Reason>
</Acknowledgement_MarketDocument>"""

BAD_PARAMS_XML = NO_DATA_XML.replace(
    b"No matching data found for Data item Day-ahead Prices.",
    b"Mandatory parameter in_Domain is missing.",
)

START = datetime(2026, 1, 1, tzinfo=UTC)
END = datetime(2026, 1, 2, tzinfo=UTC)


def make_client(handler: Callable[[httpx.Request], httpx.Response]) -> EntsoeClient:
    """A client whose HTTP calls go to ``handler`` instead of the internet."""
    settings = Settings(api_token=SecretStr(TOKEN))
    return EntsoeClient(settings, transport=httpx.MockTransport(handler), wait=wait_none())


# --- Pure helpers -------------------------------------------------------------


def test_format_entsoe_time_converts_to_utc() -> None:
    midnight_amsterdam = datetime(2026, 1, 1, tzinfo=ZoneInfo("Europe/Amsterdam"))

    assert format_entsoe_time(midnight_amsterdam) == "202512312300"


def test_format_entsoe_time_rejects_naive_datetimes() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        format_entsoe_time(datetime(2026, 1, 1))


def test_split_range_covers_the_whole_range_without_gaps() -> None:
    start = datetime(2024, 1, 1, tzinfo=UTC)
    end = datetime(2026, 3, 15, tzinfo=UTC)

    windows = list(split_range(start, end))

    assert windows[0][0] == start
    assert windows[-1][1] == end
    for (_, previous_end), (next_start, _) in zip(windows, windows[1:], strict=False):
        assert previous_end == next_start  # contiguous: no gaps, no overlaps
    assert all(
        window_end - window_start <= timedelta(days=365) for window_start, window_end in windows
    )


def test_split_range_short_range_is_a_single_window() -> None:
    assert list(split_range(START, END)) == [(START, END)]


def test_split_range_rejects_reversed_range() -> None:
    with pytest.raises(ValueError, match="before"):
        list(split_range(END, START))


# --- HTTP behaviour -----------------------------------------------------------


def test_fetch_sends_expected_query() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=b"<Publication_MarketDocument/>")

    with make_client(handler) as client:
        body = client.fetch({"documentType": "A44"}, START, END)

    assert body == b"<Publication_MarketDocument/>"
    params = seen[0].url.params
    assert params["documentType"] == "A44"
    assert params["periodStart"] == "202601010000"
    assert params["periodEnd"] == "202601020000"
    assert params["securityToken"] == TOKEN


def test_retries_server_errors_then_succeeds() -> None:
    responses = iter([httpx.Response(503), httpx.Response(502), httpx.Response(200, content=b"ok")])
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return next(responses)

    with make_client(handler) as client:
        assert client.fetch({}, START, END) == b"ok"
    assert calls == 3


def test_gives_up_after_max_attempts() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(500)

    with make_client(handler) as client, pytest.raises(EntsoeServerError):
        client.fetch({}, START, END)
    assert calls == 5


def test_does_not_retry_rate_limit() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(429)

    with make_client(handler) as client, pytest.raises(EntsoeRateLimitError):
        client.fetch({}, START, END)
    assert calls == 1


def test_no_matching_data_raises_specific_error() -> None:
    with (
        make_client(lambda request: httpx.Response(200, content=NO_DATA_XML)) as client,
        pytest.raises(NoMatchingDataError),
    ):
        client.fetch({}, START, END)


def test_bad_request_surfaces_entsoe_reason_without_leaking_token() -> None:
    with (
        make_client(lambda request: httpx.Response(400, content=BAD_PARAMS_XML)) as client,
        pytest.raises(EntsoeRequestError) as excinfo,
    ):
        client.fetch({}, START, END)

    assert "in_Domain is missing" in str(excinfo.value)
    assert TOKEN not in str(excinfo.value)
