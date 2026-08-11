# Copyright (c) 2025 zefronxd
# Licensed under the MIT License.
# This file is part of ZEFXMusic

from html import escape
import asyncio

from pyrogram import filters, types

from zefron import app, db


_COMMANDS = ["sametaste", "taste", "musicmatch"]
_MAX_COMMON_SONGS = 5


def _song_ids(songs: list[dict]) -> set[str]:
    return {str(song.get("id")) for song in songs if song.get("id")}


def _vibe_label(score: int) -> str:
    if score >= 80:
        return "Music soulmates 💫"
    if score >= 55:
        return "Same wavelength 🎧"
    if score >= 30:
        return "A promising musical connection 🎶"
    return "Different taste, but room for discoveries 🌈"


def _display_name(user) -> str:
    return escape(user.first_name or user.username or "this listener")


@app.on_message(
    filters.command(_COMMANDS)
    & filters.group
    & ~app.bl_users
)
async def same_taste(_, m: types.Message):
    """Compare two listeners' saved favourites."""
    reply = m.reply_to_message
    if not reply or not reply.from_user:
        return await m.reply_text(
            "🎧 <b>Same Taste Finder</b>\n\n"
            "Reply to a member's message with <code>/sametaste</code> "
            "to compare your saved favourite songs.",
            quote=True,
        )

    other = reply.from_user
    if other.id == m.from_user.id:
        return await m.reply_text(
            "🎧 Reply to another member's message to find your shared music taste.",
            quote=True,
        )

    mine, theirs = await asyncio.gather(
        db.get_favourites(m.from_user.id),
        db.get_favourites(other.id),
    )
    mine_by_id = {
        str(song.get("id")): song
        for song in mine
        if song.get("id")
    }
    their_ids = _song_ids(theirs)

    if not mine:
        return await m.reply_text(
            "🎧 You have not saved any favourite songs yet.\n"
            "Save songs with the ❤️ button, then try <code>/sametaste</code> again.",
            quote=True,
        )
    if not theirs:
        return await m.reply_text(
            f"🎧 <b>{_display_name(other)}</b> has not saved any favourite songs yet.\n"
            "Ask them to save a few songs with the ❤️ button first.",
            quote=True,
        )

    common_ids = set(mine_by_id) & their_ids
    union_size = len(set(mine_by_id) | their_ids)
    score = round(len(common_ids) / union_size * 100) if union_size else 0

    common_lines = []
    their_by_id = {
        str(song.get("id")): song
        for song in theirs
        if song.get("id")
    }
    for video_id in sorted(common_ids)[:_MAX_COMMON_SONGS]:
        song = mine_by_id.get(video_id) or their_by_id[video_id]
        title = escape(str(song.get("title") or "Unknown song"))
        url = escape(str(song.get("url") or ""), quote=True)
        common_lines.append(f"• <a href=\"{url}\">{title}</a>")

    common_block = "\n".join(common_lines) or "• No exact songs yet"
    extra = (
        f"\n\n<b>Shared songs:</b> {len(common_ids)}"
        f"\n{common_block}"
    )
    if len(common_ids) > _MAX_COMMON_SONGS:
        extra += f"\n…and {len(common_ids) - _MAX_COMMON_SONGS} more"

    await m.reply_text(
        f"🎧 <b>Same Taste Finder</b>\n\n"
        f"{m.from_user.mention} × {_display_name(other)}\n"
        f"💯 <b>Music match:</b> {score}%\n"
        f"✨ <b>{_vibe_label(score)}</b>"
        f"{extra}\n\n"
        "Save more favourites to make this match even more accurate!",
        quote=True,
    )