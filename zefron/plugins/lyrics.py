# Copyright (c) 2025 zefronxd
# Licensed under the MIT License.
# This file is part of ZEFXMusic

import aiohttp

from pyrogram import filters, types

from zefron import app, lang


LRCLIB_API = "https://lrclib.net/api/search"


@app.on_message(
    filters.command(["lyrics", "lyric", "ly"])
    & ~app.bl_users
)
@lang.language()
async def lyrics_hndlr(_, m: types.Message) -> None:
    if len(m.command) < 2:
        return await m.reply_text(m.lang["lyrics_usage"])

    query = " ".join(m.command[1:]).strip()
    sent = await m.reply_text(m.lang["lyrics_searching"].format(query))

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                LRCLIB_API,
                params={"q": query},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    return await sent.edit_text(m.lang["lyrics_not_found"].format(query))
                data = await resp.json()
    except Exception:
        return await sent.edit_text(m.lang["lyrics_error"])

    # Filter out instrumental tracks and pick the first with lyrics
    result = next(
        (r for r in data if not r.get("instrumental") and r.get("plainLyrics")),
        None,
    )

    if not result:
        return await sent.edit_text(m.lang["lyrics_not_found"].format(query))

    title    = result.get("trackName") or result.get("name") or query
    artist   = result.get("artistName", "Unknown Artist")
    lyrics   = result.get("plainLyrics", "").strip()
    duration = result.get("duration")

    dur_str = ""
    if duration:
        m_val, s_val = divmod(int(duration), 60)
        dur_str = f"\n⏱ <b>Duration:</b> {m_val}:{s_val:02d}"

    header = m.lang["lyrics_header"].format(title, artist) + dur_str + "\n\n"

    # Telegram message limit is 4096 chars
    max_lyrics = 4096 - len(header) - 20
    if len(lyrics) > max_lyrics:
        lyrics = lyrics[:max_lyrics].rsplit("\n", 1)[0] + "\n…"

    await sent.edit_text(header + lyrics)
