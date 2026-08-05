# Copyright (c) 2025 zefronxd
# Licensed under the MIT License.
# This file is part of ZEFXMusic

import re
import aiohttp

from pyrogram import enums, filters, types

from zefron import zefx, app, db, lang, queue
from zefron.helpers import Media


# Verified working stations (audio/mpeg, 200 OK)
RADIO_STATIONS: dict[str, str] = {
    "lofi":        "http://ice1.somafm.com/groovesalad-128-mp3",
    "chill":       "http://ice1.somafm.com/groovesalad-128-mp3",
    "beats":       "http://ice1.somafm.com/beatblender-128-mp3",
    "jazz":        "http://ice1.somafm.com/folkfwd-128-mp3",
    "rock":        "http://ice1.somafm.com/metal-128-mp3",
    "metal":       "http://ice1.somafm.com/metal-128-mp3",
    "space":       "http://ice1.somafm.com/deepspaceone-128-mp3",
    "edm":         "http://ice1.somafm.com/beatblender-128-mp3",
    "indie":       "http://ice1.somafm.com/folkfwd-128-mp3",
    "ambient":     "http://ice1.somafm.com/deepspaceone-128-mp3",
}

_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)


def _resolve_station(query: str) -> tuple[str, str] | tuple[None, None]:
    """Return (display_name, url) for a known station or direct URL."""
    q = query.strip()
    if _URL_RE.match(q):
        return q, q
    key = q.lower()
    for name, url in RADIO_STATIONS.items():
        if name in key or key in name:
            return name.capitalize(), url
    return None, None


@app.on_message(
    filters.command(["radio", "vradio"])
    & filters.group
    & ~app.bl_users
)
@lang.language()
async def radio_hndlr(_, m: types.Message) -> None:
    try:
        await m.delete()
    except Exception:
        pass

    video = "v" in m.command[0].lower()

    if len(m.command) < 2:
        stations = "\n".join(f"  • <code>{k}</code>" for k in RADIO_STATIONS)
        return await m.reply_text(m.lang["radio_usage"].format(stations))

    query = " ".join(m.command[1:]).strip()
    station_name, stream_url = _resolve_station(query)

    if not station_name:
        return await m.reply_text(
            m.lang["radio_not_found"].format(", ".join(RADIO_STATIONS.keys()))
        )

    sent = await m.reply_text(m.lang["radio_connecting"].format(station_name))

    media = Media(
        id=f"radio_{station_name.lower()}",
        file_path=stream_url,
        message_id=sent.id,
        url=stream_url,
        title=f"📻 {station_name} Radio",
        duration="∞",
        video=video,
        user=m.from_user.mention,
    )

    if queue.get_queue(m.chat.id) or await db.get_call(m.chat.id):
        position = queue.add(m.chat.id, media)
        return await sent.edit_text(
            m.lang["radio_queued"].format(position, station_name)
        )

    queue.add(m.chat.id, media)
    await zefx.play_media(chat_id=m.chat.id, message=sent, media=media)
