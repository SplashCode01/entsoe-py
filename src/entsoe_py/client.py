"""Thin HTTP client for the ENTSO-E Transparency Platform REST API.

The client's only job is to turn a query into raw XML bytes, or a clear error.
Parsing that XML into records is a separate concern (see ``entsoe_py.parsers``).
"""

import logging
import xml.etree.ElementTree as ET
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime, timedelta
from types import TracebackType
from typing import Self

import httpx
from tenacity import (
    Retrying,
    before_sleep_log,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)
from tenacity.wait import wait_base

from entsoe_py.config import Settings

logger = logging.getLogger(__name__)

ENTSOE_TIME_FORMAT = "%Y%m%d%H%M"
MAX_REQUEST_SPAN = timedelta(days=365)


# --- Errors -----------------------------------------------------------------


class EntsoeError(Exception):
    """Base class for every error this client raises."""


class EntsoeServerError(EntsoeError):
    """HTTP 5xx: a problem on ENTSO-E's side. Worth retrying."""


class EntsoeRateLimitError(EntsoeError):
    """HTTP 429: we hit the rate limit. ENTSO-E then bans us for ~10 minutes."""


class EntsoeRequestError(EntsoeError):
    """The request itself was wrong (bad parameters, invalid token...). Never retried."""


class NoMatchingDataError(EntsoeError):
    """The query was valid, but ENTSO-E has no data for it."""


# --- Pure helpers (no I/O, trivially testable) ------------------------------


def format_entsoe_time(moment: datetime) -> str:
    if moment.tzinfo is None:
        raise ValueError(f"Naive datetime {moment!r}: pass a timezone-aware datetime.")
    return moment.astimezone(UTC).strftime(ENTSOE_TIME_FORMAT)


def split_range(
    start: datetime, end: datetime, max_span: timedelta = MAX_REQUEST_SPAN
) -> Iterator[tuple[datetime, datetime]]:
    """Split [start, end) into consecutive windows no longer than ``max_span``."""
    if start.tzinfo is None or end.tzinfo is None:
        raise ValueError("start and end must be timezone-aware")
    if start >= end:
        raise ValueError(f"start ({start}) must be before end ({end}).")
    if max_span > MAX_REQUEST_SPAN:
        raise ValueError(f"max_span cannot exceed teh API limit of {MAX_REQUEST_SPAN}.")

    window_start = start
    while window_start < end:
        window_end = min(window_start + max_span, end)
        yield window_start, window_end
        window_start = window_end


def _acknowledgement_reason(body: bytes) -> str | None:
    """Return the reason text if the body is an ENTSO-E acknowledgement (error) document."""
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        return None
    if not root.tag.endswith("Acknowledgement_MarketDocument"):
        return None
    return root.findtext(".//{*}Reason/{*}text") or "no reason given"


def _check_response(response: httpx.Response) -> bytes:
    """Map an HTTP response to raw bytes or one of our error types.

    We deliberately avoid ``response.raise_for_status()``: its message contains the
    full URL, including our security token, which would then end up in logs.
    """
    status = response.status_code
    if status == 429:
        raise EntsoeRateLimitError("HTTP 429: rate limit reached (ENTSO-E bans for ~10 minutes).")
    if status >= 500:
        raise EntsoeServerError(f"HTTP {status}: ENTSO-E server error.")

    reason = _acknowledgement_reason(response.content)
    if reason is not None:
        if "No matching data" in reason:
            raise NoMatchingDataError(reason)
        raise EntsoeRequestError(f"HTTP {status}: {reason}")
    if status >= 400:
        raise EntsoeRequestError(f"HTTP {status}: request rejected.")
    return response.content


# --- The client -------------------------------------------------------------


class EntsoeClient:
    """Fetches raw XML documents from the ENTSO-E API.

    Use it as a context manager so the underlying connection pool is closed:

        with EntsoeClient(get_settings()) as client:
            xml = client.fetch({"documentType": "A44", ...}, start, end)
    """

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.BaseTransport | None = None,
        max_attempts: int = 5,
        wait: wait_base | None = None,
    ) -> None:
        self._token = settings.api_token
        self._url = settings.base_url
        self._http = httpx.Client(timeout=settings.timeout_seconds, transport=transport)
        self._retrying = Retrying(
            retry=retry_if_exception_type((httpx.TransportError, EntsoeServerError)),
            stop=stop_after_attempt(max_attempts),
            wait=wait or wait_exponential(multiplier=1, min=1, max=30),
            before_sleep=before_sleep_log(logger, logging.WARNING),
            reraise=True,
        )

    def fetch(self, params: Mapping[str, str], start: datetime, end: datetime) -> bytes:
        """Fetch one document. The caller is responsible for keeping start..end within limits."""
        query = {
            **params,
            "periodStart": format_entsoe_time(start),
            "periodEnd": format_entsoe_time(end),
            "securityToken": self._token.get_secret_value(),
        }
        logger.info(
            "Fetching %s from %s to %s",
            params.get("documentType", "?"),
            query["periodStart"],
            query["periodEnd"],
        )
        return self._retrying(self._get, query)

    def _get(self, query: Mapping[str, str]) -> bytes:
        response = self._http.get(self._url, params=query)
        return _check_response(response)

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()
