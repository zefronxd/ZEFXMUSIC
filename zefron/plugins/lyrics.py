# Copyright (c) 2025 zefronxd
# Licensed under the MIT License.
# This file is part of ZEFXMusic

import aiohttp
import urllib.parse

from pyrogram import filters, types

from zefron import app, lang


LRCLIB_SEARCH = "https://lrclib.net/api/search"
OVHSUGGEST    = "https://api.lyrics.ovh/suggest/{}"
OVHLYRICS     = "https://api.lyrics.ovh/v1/{}/{}"


async def _fetch_lrclib(session: aiohttp.ClientSession, query: str) -> tuple[str, str, str] | None:
    """Search lrclib. Returns (title, artist, lyrics) or None."""
    try:
        async with session.get(
            LRCLIB_SEARCH,
            params={"q": query},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status != 200:
                return None
            data = await resp.json()
            result = next(
                (r for r in data if not r.get("instrumental") and r.get("plainLyrics")),
                None,
            )
            if result:
                return (
                    result.get("trackName") or result.get("name") or query,
                    result.get("artistName", "Unknown Artist"),
                    result.get("plainLyrics", "").strip(),
                )
    except Exception:
        pass
    return None


async def _fetch_ovh(session: aiohttp.ClientSession, query: str) -> tuple[str, str, str] | None:
    """Search lyrics.ovh suggest then fetch lyrics. Tries multiple candidates."""
    try:
        url = OVHSUGGEST.format(urllib.parse.quote(query))
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status != 200:
                return None
            data = await resp.json()
            candidates = data.get("data", [])
    except Exception:
        return None

    # Try up to 6 candidates to find one with actual lyrics
    for hit in candidates[:6]:
        artist = hit.get("artist", {}).get("name", "")
        title  = hit.get("title", "")
        if not artist or not title:
            continue
        try:
            lurl = OVHLYRICS.format(
                urllib.parse.quote(artist), urllib.parse.quote(title)
            )
            async with session.get(lurl, timeout=aiohttp.ClientTimeout(total=8)) as r:
                if r.status != 200:
                    continue
                d = await r.json()
                lyrics = d.get("lyrics", "").strip()
                if lyrics:
                    return title, artist, lyrics
        except Exception:
            continue
    return None


@app.on_message(
    filters.command(["lyrics", "lyric", "ly"])
    & ~app.bl_users
)
@lang.language()
async def lyrics_hndlr(_, m: types.Message) -> None:
    if len(m.command) < 2:
        return await m.reply_text(m.lang["lyrics_usage"])

    query = " ".join(m.command[1:]).strip()
    sent  = await m.reply_text(m.lang["lyrics_searching"].format(query))

    async with aiohttp.ClientSession() as session:
        result = await _fetch_lrclib(session, query)
        if not result:
            result = await _fetch_ovh(session, query)

    if not result:
        return await sent.edit_text(m.lang["lyrics_not_found"].format(query))

    title, artist, lyrics = result
    header = m.lang["lyrics_header"].format(title, artist) + "\n\n"

    max_lyrics = 4096 - len(header) - 10
    if len(lyrics) > max_lyrics:
        lyrics = lyrics[:max_lyrics].rsplit("\n", 1)[0] + "\n…"

    await sent.edit_text(header + lyrics)
