# Copyright (c) 2025 zefronxd
# Licensed under the MIT License.
# This file is part of ZEFXMusic

import asyncio
from html import escape

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
    """Show an overall score, or compare directly when replying to a user."""
    reply = m.reply_to_message
    mine, other_lists = await asyncio.gather(
        db.get_favourites(m.from_user.id),
        db.get_all_favourite_lists(exclude_id=m.from_user.id),
    )

    if not mine:
        return await m.reply_text(
            "🎧 You have not saved any favourite songs yet.\n"
            "Save songs with the ❤️ button, then try <code>/sametaste</code>.",
            quote=True,
        )

    # Reply mode keeps the original direct user-to-user comparison.
    if reply and reply.from_user:
        other = reply.from_user
        if other.id == m.from_user.id:
            return await m.reply_text(
                "🎧 Reply to another member's message to find your shared music taste.",
                quote=True,
            )

        theirs = await db.get_favourites(other.id)
        if not theirs:
            return await m.reply_text(
                f"🎧 <b>{_display_name(other)}</b> has not saved any favourite songs yet.\n"
                "Ask them to save a few songs with the ❤️ button first.",
                quote=True,
            )

        mine_by_id = {
            str(song.get("id")): song
            for song in mine
            if song.get("id")
        }
        their_by_id = {
            str(song.get("id")): song
            for song in theirs
            if song.get("id")
        }
        common_ids = set(mine_by_id) & set(their_by_id)
        union_size = len(set(mine_by_id) | set(their_by_id))
        score = round(len(common_ids) / union_size * 100) if union_size else 0

        common_lines = []
        for video_id in sorted(common_ids)[:_MAX_COMMON_SONGS]:
            song = mine_by_id[video_id]
            title = escape(str(song.get("title") or "Unknown song"))
            url = escape(str(song.get("url") or ""), quote=True)
            common_lines.append(f"• <a href=\"{url}\">{title}</a>")

        common_block = "\n".join(common_lines) or "• No exact songs yet"
        if len(common_ids) > _MAX_COMMON_SONGS:
            common_block += f"\n…and {len(common_ids) - _MAX_COMMON_SONGS} more"

        return await m.reply_text(
            f"🎧 <b>Same Taste Finder</b>\n\n"
            f"{m.from_user.mention} × {_display_name(other)}\n"
            f"💯 <b>Music match:</b> {score}%\n"
            f"✨ <b>{_vibe_label(score)}</b>\n\n"
            f"<b>Shared songs:</b> {len(common_ids)}\n{common_block}\n\n"
            "Save more favourites to make this match even more accurate!",
            quote=True,
        )

    # No-reply mode intentionally exposes only the aggregate percentage.
    mine_ids = _song_ids(mine)
    matching_users = sum(
        bool(mine_ids & _song_ids(song_list))
        for song_list in other_lists
    )
    total_listeners = len(other_lists)
    score = round(matching_users / total_listeners * 100) if total_listeners else 0

    await m.reply_text(
        f"🎧 <b>Same Taste Finder</b>\n\n"
        f"💯 <b>Your same-taste score:</b> {score}%",
        quote=True,
    )