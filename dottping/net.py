"""Client HTTP condiviso.

Due accortezze rispetto a un requests.get() secco:
  1. header da browser reale: acn.gov.it risponde 403 agli user-agent "da script";
  2. fallback su curl_cffi (impersonazione TLS di Chrome) se il 403 arriva comunque,
     tipico quando la richiesta parte da un IP datacenter come quello di un VPS.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

log = logging.getLogger(__name__)

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,"
              "image/webp,*/*;q=0.8",
    "Accept-Language": "it-IT,it;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
}

BLOCKED_STATUS = {401, 403, 406, 429, 503}


class FetchError(RuntimeError):
    """Il contenuto non è stato recuperato (rete, status, o blocco WAF)."""


def _fetch_with_curl_cffi(url: str, timeout: float) -> str:
    """Ultima spiaggia: TLS fingerprint di Chrome. Richiede `pip install curl-cffi`."""
    from curl_cffi import requests as cffi_requests  # import locale: dipendenza opzionale

    resp = cffi_requests.get(
        url, headers=BROWSER_HEADERS, impersonate="chrome", timeout=timeout
    )
    if resp.status_code >= 400:
        raise FetchError(f"curl_cffi: HTTP {resp.status_code} su {url}")
    return resp.text


async def fetch_text(url: str, *, timeout: float = 30.0, allow_fallback: bool = True) -> str:
    """Scarica una pagina come testo, con fallback anti-WAF."""
    try:
        async with httpx.AsyncClient(
            headers=BROWSER_HEADERS, timeout=timeout, follow_redirects=True
        ) as client:
            resp = await client.get(url)
        if resp.status_code < 400:
            return resp.text
        status = resp.status_code
        log.warning("HTTP %s su %s", status, url)
    except httpx.HTTPError as exc:
        status = None
        log.warning("Errore rete su %s: %s", url, exc)

    if not allow_fallback or (status is not None and status not in BLOCKED_STATUS):
        raise FetchError(f"HTTP {status} su {url}")

    try:
        return await asyncio.to_thread(_fetch_with_curl_cffi, url, timeout)
    except ImportError:
        raise FetchError(
            f"HTTP {status} su {url}. Il sito blocca le richieste da questo IP/user-agent. "
            "Installa il fallback con:  pip install curl-cffi"
        ) from None
    except Exception as exc:  # noqa: BLE001 - qualsiasi errore del fallback è "non recuperato"
        raise FetchError(f"Fallback curl_cffi fallito su {url}: {exc}") from exc


async def fetch_json(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 20.0,
) -> Any:
    """GET che restituisce JSON. Nessun fallback: le API pubbliche non hanno WAF aggressivi."""
    merged = {"User-Agent": BROWSER_HEADERS["User-Agent"], "Accept": "application/json"}
    merged.update(headers or {})
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            resp = await client.get(url, params=params, headers=merged)
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPStatusError as exc:
        raise FetchError(f"HTTP {exc.response.status_code} su {url}") from exc
    except httpx.HTTPError as exc:
        raise FetchError(f"Errore rete su {url}: {exc}") from exc
    except ValueError as exc:
        raise FetchError(f"Risposta non JSON da {url}") from exc
