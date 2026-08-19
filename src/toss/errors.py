"""Exception hierarchy for the Toss Securities Open API.

All Toss error responses share one envelope::

    {"error": {"requestId": ..., "code": ..., "message": ..., "data": {...}}}

``TossApiError`` carries that envelope so callers can branch on ``code``
rather than parsing messages.
"""


class TossError(Exception):
    """Base class for every error raised by the toss package."""


class TossConfigError(TossError):
    """Missing or invalid client configuration (credentials, account, ...)."""


class TossApiError(TossError):
    """Non-2xx response from the API."""

    def __init__(self, status, code, message, request_id=None, data=None):
        self.status = status
        self.code = code
        self.message = message
        self.request_id = request_id
        self.data = data or {}
        detail = f"[{status} {code}] {message}"
        if request_id:
            detail += f" (requestId={request_id})"
        super().__init__(detail)


class TossAuthError(TossApiError):
    """401 - invalid, expired or missing token."""


class TossForbiddenError(TossApiError):
    """403 - most often an IP that is not on the allowlist.

    The Toss console requires every calling IP to be registered under
    Settings > Open API > allowed IPs. Calls from anywhere else are rejected
    with ``edge-blocked``, which looks identical to a credential problem.
    """


class TossNotFoundError(TossApiError):
    """404 - unknown symbol, account or order."""


class TossRateLimitError(TossApiError):
    """429 - rate limit exceeded. ``retry_after`` is in seconds."""

    def __init__(self, status, code, message, retry_after=None, **kwargs):
        super().__init__(status, code, message, **kwargs)
        self.retry_after = retry_after


class TossServerError(TossApiError):
    """5xx - transient server fault or scheduled maintenance."""


class TossWriteBlockedError(TossError):
    """A write call was attempted on a client created in read-only mode.

    Phase 1 of this project is report-only. Order endpoints are reached
    through ``src/toss/trading.py``, which must be given a client that was
    explicitly constructed with ``allow_write=True``.
    """


#: Errors that are pointless to retry - the request will fail the same way
#: until something about the account or the market changes.
TERMINAL_CODES = frozenset({
    "insufficient-buying-power",
    "insufficient-sellable-quantity",
    "order-hours-closed",
    "amount-order-outside-regular-hours",
    "fractional-quantity-outside-regular-hours",
    "stock-restricted",
    "account-restricted",
    "prerequisite-required",
    "idempotency-key-conflict",
    "condition-already-met",
    "duplicate-conditional-order",
})


def error_from_response(status, payload, headers=None):
    """Build the most specific TossApiError subclass for a response."""
    headers = headers or {}
    envelope = {}
    if isinstance(payload, dict):
        envelope = payload.get("error") or {}

    code = envelope.get("code") or f"http-{status}"
    message = envelope.get("message") or "요청이 실패했습니다."
    request_id = envelope.get("requestId") or headers.get("X-Request-Id")
    data = envelope.get("data")

    common = {"request_id": request_id, "data": data}

    if status == 401:
        return TossAuthError(status, code, message, **common)
    if status == 403:
        return TossForbiddenError(status, code, message, **common)
    if status == 404:
        return TossNotFoundError(status, code, message, **common)
    if status == 429:
        retry_after = headers.get("Retry-After")
        try:
            retry_after = float(retry_after) if retry_after is not None else None
        except (TypeError, ValueError):
            retry_after = None
        return TossRateLimitError(status, code, message, retry_after=retry_after, **common)
    if status >= 500:
        return TossServerError(status, code, message, **common)
    return TossApiError(status, code, message, **common)
