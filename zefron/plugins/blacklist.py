# Copyright (c) 2025 zefronxd
# Licensed under the MIT License.
# This file is part of ZEFXMusic


from pyrogram import enums, filters, types

from zefron import app, db, lang


async def _resolve_target(m: types.Message):
    """
    Resolve target user/chat from:
      1. Reply to a message  → returns replied user's id
      2. Argument as @username → resolves via get_chat
      3. Argument as numeric id → int cast

    Returns (target_id, error_text_key_or_None).
    """
    # 1. Reply
    if m.reply_to_message and m.reply_to_message.from_user:
        return m.reply_to_message.from_user.id, None

    # 2. Argument
    if len(m.command) >= 2:
        arg = m.command[1]
        if arg.startswith("@"):
            try:
                entity = await app.get_chat(arg)
                return entity.id, None
            except Exception:
                return None, "bl_invalid"
        try:
            return int(arg), None
        except ValueError:
            return None, "bl_invalid"

    return None, "bl_usage"


@app.on_message(filters.command(["blacklist", "unblacklist", "whitelist"]) & app.sudoers)
@lang.language()
async def _blacklist(_, m: types.Message):
    target_id, err = await _resolve_target(m)
    if err:
        fmt = m.lang[err]
        if err == "bl_usage":
            fmt = fmt.format(m.command[0])
        return await m.reply_text(fmt)

    chat_id = target_id

    if m.command[0] == "blacklist":
        if chat_id in db.blacklisted or chat_id in app.bl_users:
            return await m.reply_text(m.lang["bl_already"])
        if not str(chat_id).startswith("-100"):
            app.bl_users.add(chat_id)
        await db.add_blacklist(chat_id)
        await m.reply_text(m.lang["bl_added"])
    else:
        is_global = chat_id in db.blacklisted or chat_id in app.bl_users
        # Also check group blacklist when used inside a group
        is_in_group = m.chat.type in (enums.ChatType.GROUP, enums.ChatType.SUPERGROUP)
        is_group_bl = (
            is_in_group and chat_id > 0
            and await db.is_group_bl(m.chat.id, chat_id)
        )

        if not is_global and not is_group_bl:
            return await m.reply_text(m.lang["bl_not"])

        if is_global:
            if not str(chat_id).startswith("-100"):
                app.bl_users.discard(chat_id)
            await db.del_blacklist(chat_id)

        if is_group_bl:
            await db.del_group_bl(m.chat.id, chat_id)

        await m.reply_text(m.lang["bl_removed"])


@app.on_message(filters.command(["blacklist", "unblacklist"]) & filters.group & ~app.sudoers)
@lang.language()
async def _group_blacklist(_, m: types.Message):
    """Group admins with ban permission can blacklist/unblacklist a user in their group."""
    try:
        member = await app.get_chat_member(m.chat.id, m.from_user.id)
    except Exception:
        return

    is_owner = member.status == enums.ChatMemberStatus.OWNER
    has_ban = (
        is_owner
        or (
            member.status == enums.ChatMemberStatus.ADMINISTRATOR
            and member.privileges
            and member.privileges.can_restrict_members
        )
    )
    if not has_ban:
        return await m.reply_text(m.lang["gbl_no_perms"])

    target_id, err = await _resolve_target(m)
    if err:
        fmt = m.lang[err]
        if err == "bl_usage":
            fmt = fmt.format(m.command[0])
        return await m.reply_text(fmt)

    user_id = target_id

    if m.command[0] == "blacklist":
        if await db.is_group_bl(m.chat.id, user_id):
            return await m.reply_text(m.lang["gbl_already"])
        await db.add_group_bl(m.chat.id, user_id)
        await m.reply_text(m.lang["gbl_added"])
    else:
        if not await db.is_group_bl(m.chat.id, user_id):
            return await m.reply_text(m.lang["gbl_not"])
        await db.del_group_bl(m.chat.id, user_id)
        await m.reply_text(m.lang["gbl_removed"])
