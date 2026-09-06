"""Shared outbound HTTP session for provider integrations.

Keeping one connector alive lets aiohttp reuse DNS results and TCP/TLS
connections across YouTube, Spotify, lyrics, recommendation, and other
provider requests.  The session is created lazily because the bot constructs several
modules before its asyncio event loop starts.
"""

from __future__ import annotations

import asyncio

import aiohttp


_session: aiohttp.ClientSession | None = None
_session_lock = asyncio.Lock()


async def get_http_session() -> aiohttp.ClientSession:
    """Return the shared aiohttp session for the current application loop."""
    global _session
    if _session is None or _session.closed:
        async with _session_lock:
            if _session is None or _session.closed:
                _session = aiohttp.ClientSession(
                    connector=aiohttp.TCPConnector(
                        limit=200,
                        limit_per_host=50,
                        ttl_dns_cache=300,
                    ),
                )
    return _session


async def close_http_session() -> None:
    """Close the shared provider session during application shutdown."""
    global _session
    session = _session
    _session = None
    if session and not session.closed:
        await session.close()