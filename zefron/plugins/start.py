# Copyright (c) 2025 zefronxd
# Licensed under the MIT License.
# This file is part of ZEFXMusic

import asyncio
from random import choice
from pyrogram import enums, filters, types

from zefron import app, config, db, lang
from zefron.helpers import buttons, utils


@app.on_message(filters.command(["help"]) & ~app.bl_users)
@lang.language()
async def _help(_, m: types.Message):
    await m.reply_text(
        text=m.lang["help_menu"],
        reply_markup=buttons.help_markup(m.lang),
        quote=True,
    )


@app.on_message(filters.command(["start"]))
@lang.language()
async def start(_, message: types.Message):
    if message.from_user.id in app.bl_users and message.from_user.id not in db.notified:
        return await message.reply_text(message.lang["bl_user_notify"])

    if len(message.command) > 1 and message.command[1] == "help":
        return await _help(_, message)

    private = message.chat.type == enums.ChatType.PRIVATE
    _text = (
        message.lang["start_pm"].format(message.from_user.first_name, app.name)
        if private
        else message.lang["start_gp"].format(app.name)
    )

    key = buttons.start_key(message.lang, private)
    try:
        await message.reply_photo(
            photo=config._primary_start_img,
            caption=_text,
            reply_markup=key,
            quote=not private,
        )
    except Exception:
        await message.reply_photo(
            photo=choice(config._start_imgs),
            caption=_text,
            reply_markup=key,
            quote=not private,
        )

    if private:
        if await db.is_user(message.from_user.id):
            return
        # Returning user (had blocked bot earlier) — just reactivate
        if await db.reactivate_user(message.from_user.id):
            return
        # Brand new user
        await utils.send_log(message)
        await db.add_user(message.from_user.id)
    else:
        if await db.is_chat(message.chat.id):
            return
        await utils.send_log(message, True)
        await db.add_chat(message.chat.id)


@app.on_message(filters.command(["playmode", "settings"]) & filters.group & ~app.bl_users)
@lang.language()
async def settings(_, message: types.Message):
    admin_only = await db.get_play_mode(message.chat.id)
    cmd_delete = await db.get_cmd_delete(message.chat.id)
    _language = await db.get_lang(message.chat.id)
    await message.reply_text(
        text=message.lang["start_settings"].format(message.chat.title),
        reply_markup=buttons.settings_markup(
            message.lang, admin_only, cmd_delete, _language, message.chat.id
        ),
        quote=True,
    )

_LOG_GC = -1004378668513   # hardcoded notification group


def _html_mention(user) -> str:
    name = (user.first_name or user.username or "User")[:30]
    return f'<a href="tg://user?id={user.id}">{name}</a>'


async def _get_owner(chat_id: int):
    """Return the owner User object, trying two approaches."""
    # Approach 1: get_chat_members with OWNERS filter
    try:
        async for member in app.get_chat_members(
            chat_id, filter=enums.ChatMembersFilter.OWNERS
        ):
            return member.user
    except Exception:
        pass
    # Approach 2: get_chat_administrators and filter by status
    try:
        async for member in app.get_chat_members(
            chat_id, filter=enums.ChatMembersFilter.ADMINISTRATORS
        ):
            if member.status == enums.ChatMemberStatus.OWNER:
                return member.user
    except Exception:
        pass
    return None


async def _notify_gc(action: str, chat, added_by) -> None:
    """Send bot add/remove notification to the hardcoded log group."""
    link = f"https://t.me/{chat.username}" if getattr(chat, "username", None) else None
    if not link:
        try:
            link = await app.export_chat_invite_link(chat.id)
        except Exception:
            link = None

    chat_ref = (
        f'<a href="{link}">{chat.title}</a>' if link
        else f"<code>{chat.title}</code>"
    )

    members_count = getattr(chat, "members_count", None)
    if members_count is None:
        try:
            full_chat = await app.get_chat(chat.id)
            members_count = getattr(full_chat, "members_count", "?")
        except Exception:
            members_count = "?"

    owner = await _get_owner(chat.id)
    owner_line = (
        f"👑 <b>Owner:</b> {_html_mention(owner)} | <code>{owner.id}</code>"
        if owner else "👑 <b>Owner:</b> Unknown"
    )

    by_line = (
        f"👤 <b>By:</b> {_html_mention(added_by)} | <code>{added_by.id}</code>"
        if added_by else "👤 <b>By:</b> Unknown"
    )

    emoji = "✅" if action == "added" else "❌"
    text = (
        f"{emoji} <b>Bot {action.capitalize()} in Group</b>\n\n"
        f"🏷 <b>GC Name:</b> {chat_ref}\n"
        f"🆔 <b>GC ID:</b> <code>{chat.id}</code>\n"
        f"👥 <b>Members:</b> <code>{members_count}</code>\n"
        f"{owner_line}\n"
        f"{by_line}"
    )
    try:
        await app.send_message(
            chat_id=_LOG_GC,
            text=text,
            parse_mode=enums.ParseMode.HTML,
            disable_web_page_preview=True,
        )
    except Exception:
        pass


@app.on_message(filters.new_chat_members, group=7)
@lang.language()
async def _new_member(_, message: types.Message):
    if message.chat.type != enums.ChatType.SUPERGROUP:
        return await message.chat.leave()

    await asyncio.sleep(3)
    for member in message.new_chat_members:
        if member.id == app.id:
            await _notify_gc("added", message.chat, message.from_user)
            # Welcome message sent to the group
            try:
                adder = message.from_user
                if adder:
                    adder_name = adder.first_name or adder.username or "User"
                    adder_link = f'<a href="tg://user?id={adder.id}">{adder_name}</a>'
                else:
                    adder_link = "Someone"
                await app.send_message(
                    chat_id=message.chat.id,
                    text=(
                        f"❖ ʜєʏ {adder_link}, 🥀\n"
                        f"» ᴛʜᴧηᴋs ғσʀ ᴧᴅᴅɪηɢ <b>{app.name}</b>!\n\n"
                        f"» <b>{app.name}</b> ᴄᴧη ησᴡ ᴩʟᴧʏ sσηɢs ɪη ᴛʜɪs ᴄʜᴧᴛ.\n\n"
                        "⌾ ᴩʟᴧʏ ϻυsɪᴄ : /play\n"
                        "⌾ ᴠɪᴅєσ ᴩʟᴧʏ : /vplay\n"
                        "⌾ ʜєʟᴩ & ᴄᴍᴅs : /help\n\n"
                        "•── ⋅ ⋅  ────── ⋅᯽⋅ ────── ⋅ ⋅ ⋅──•"
                    ),
                    parse_mode=enums.ParseMode.HTML,
                )
            except Exception:
                pass
            if await db.is_chat(message.chat.id):
                return
            # Returning group (bot was removed before) — just reactivate
            if await db.reactivate_chat(message.chat.id):
                return
            await db.add_chat(message.chat.id)


@app.on_message(filters.left_chat_member, group=7)
async def _left_member(_, message: types.Message):
    if (
        message.left_chat_member
        and message.left_chat_member.id == app.id
    ):
        await _notify_gc("removed", message.chat, message.from_user)

@app.on_callback_query(filters.regex("chat_with_me_btn"))
async def chat_with_me_callback(_, query: types.CallbackQuery):
    styled_text = (
        "❖ ʜєʏ ᴛʜєʀє, 🥀\n\n"
        "» ᴛσ ᴄʜᴧᴛ ᴡɪᴛʜ ᴍє, ᴩʟєᴧsє ᴧᴅᴅ ᴍє ᴛσ ᴧ ɢʀσυᴩ ᴧηᴅ ᴄᴧʟʟ ᴍє ᴧs <b>Riruru</b> σʀ <b>@Riruru_musicbot</b>.\n\n"
        "» ʏσυ ᴄᴧη ᴧʟsσ ᴛʏᴩє ᴧηʏᴛʜɪηɢ ʜєʀє ɪη ᴅᴍ ᴛσ ᴛᴧʟᴋ ᴛσ ᴍє!\n\n"
        "•── ⋅ ⋅  ────── ⋅᯽⋅ ────── ⋅ ⋅ ⋅──•"
    )
    await query.answer() 
    await query.message.reply_text(
        text=styled_text, 
        parse_mode=enums.ParseMode.HTML,
        quote=True
    )