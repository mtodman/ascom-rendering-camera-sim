"""Shared HTTP plumbing for the outbound Alpaca clients (telescope, focuser):
transaction-ID bookkeeping and uniform error handling for a single GET of an
Alpaca device property.
"""
from __future__ import annotations

import itertools
import logging

import requests

logger = logging.getLogger("camera_sim.alpaca_client")

_client_id = 1234
_transaction_counter = itertools.count(1)


def get_alpaca_property(base_url: str, prop: str, timeout_s: float) -> float | bool | str | None:
    """GETs a single Alpaca device property (e.g. "rightascension",
    "position"). Returns None (logging a warning) on any transport error,
    non-2xx response, or a non-zero Alpaca ErrorNumber - callers treat that
    uniformly as "value unavailable" and fall back to configured defaults.
    """
    params = {
        "ClientID": _client_id,
        "ClientTransactionID": next(_transaction_counter),
    }
    try:
        resp = requests.get(f"{base_url}/{prop}", params=params, timeout=timeout_s)
        resp.raise_for_status()
        body = resp.json()
    except Exception as exc:  # noqa: BLE001
        logger.warning("query %s/%s failed: %s", base_url, prop, exc)
        return None
    if body.get("ErrorNumber", 0) != 0:
        logger.warning("query %s/%s returned error: %s", base_url, prop, body.get("ErrorMessage"))
        return None
    return body.get("Value")
