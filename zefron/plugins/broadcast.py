# Copyright (c) 2025 zefronxd
# Licensed under the MIT License.
# This file is part of ZEFXMusic

import os
import asyncio

from pyrogram import errors, filters, types

from zefron import app, db, lang


broadcasting = asyncio.Lock()

# ── Helpers ────────────────────────────────────────────────────────────────

_USAGE = (
    "<b>📢 Broadcast Usage</b>\n\n"
    "<b>Reply to a message:</b>\n"
    "  <code>/broadcast</code> — send to all groups\n"
    "  <code>/broadcast -user</code> — groups + users\n"
    "  <code>/broadcast -onlyuser</code> — only users\n\n"
    "<b>Inline text:</b>\n"
    "  <code>/broadcast Hello everyone!</code>\n"
    "  <code>/broadcast -user Hello!</code>\n\n"
    "<b>Extra flags:</b>\n"
    "  <code>-copy</code> — strip forward tag\n"
    "  <code>-pin</code> — pin silently in groups\n"
    "  <code>-pinloud</code> — pin with notification\n\n"
    "<i>Flags can be combined, e.g. <code>/broadcast -user -copy</code> + reply</i>"
)


def _parse_flags(message: types.Message):
    """Return (flags_set, inline_text_or_None)."""
    raw = message.text or ""
    parts = raw.split(None, 1)               # ["/broadcast", "rest"]
    rest = parts[1] if len(parts) > 1 else ""

    flags = set()
    for flag in ("-user", "-onlyuser", "-copy", "-pin", "-pinloud"):
        if flag in rest:
            flags.add(flag)
            rest = rest.replace(flag, "")

    inline_text = rest.strip() or None
    return flags, inline_text


# ── Command ────────────────────────────────────────────────────────────────

@app.on_message(filters.command(["broadcast", "gcast"]) & app.sudoers)
@lang.language()
async def _broadcast(_, message: types.Message):

    flags, inline_text = _parse_flags(message)
    has_reply = bool(message.reply_to_message)

    # Validate input
    if not has_reply and not inline_text:
        return await message.reply_text(_USAGE, quote=True)

    if broadcasting.locked():
        return await message.reply_text(
            "⏳ <b>A broadcast is already running.</b> Please wait for it to finish.",
            quote=True,
        )

    # Decide targets
    send_groups = "-onlyuser" not in flags
    send_users  = "-user" in flags or "-onlyuser" in flags

    if not send_groups and not send_users:
        send_groups = True   # default

    # Build target list
    groups: set = set(await db.get_chats()) if send_groups else set()
    users:  set = set(await db.get_users()) if send_users  else set()
    targets = list(groups | users)

    if not targets:
        return await message.reply_text("❌ No chats/users found in database.", quote=True)

    # Progress message
    target_label = []
    if send_groups: target_label.append(f"{len(groups)} groups")
    if send_users:  target_label.append(f"{len(users)} users")
    progress = await message.reply_text(
        f"📢 <b>Broadcasting to {' + '.join(target_label)}…</b>",
        quote=True,
    )

    # ── Send loop ──────────────────────────────────────────────────────────
    g_ok = g_pin = g_inactive = u_ok = u_inactive = 0
    copy_flag    = "-copy"     in flags
    pin_silent   = "-pin"      in flags
    pin_loud     = "-pinloud"  in flags
    reply_msg    = message.reply_to_message
    failed_log   = None

    _user_blocked_errors = (
        errors.UserIsBlocked,
        errors.InputUserDeactivated,
    )
    _group_removed_errors = (
        errors.ChatWriteForbidden,
        errors.ChannelPrivate,
        errors.ChatIdInvalid,
        errors.BannedInChannel,
    )

    async with broadcasting:
        for chat_id in targets:
            try:
                if inline_text:
                    m = await app.send_message(chat_id, inline_text)
                elif copy_flag:
                    m = await reply_msg.copy(chat_id, reply_markup=reply_msg.reply_markup)
                else:
                    m = await reply_msg.forward(chat_id)

                in_group = chat_id in groups

                # Pin if requested (groups only)
                if in_group and (pin_silent or pin_loud):
                    try:
                        await m.pin(disable_notification=pin_silent)
                        g_pin += 1
                    except Exception:
                        pass

                if in_group:
                    g_ok += 1
                else:
                    u_ok += 1

                await asyncio.sleep(0.2)

            except errors.FloodWait as fw:
                await asyncio.sleep(fw.value + 5)
            except _user_blocked_errors:
                if chat_id in users:
                    await db.mark_user_inactive(chat_id)
                    u_inactive += 1
            except _group_removed_errors:
                if chat_id in groups:
                    await db.mark_chat_inactive(chat_id)
                    g_inactive += 1
            except Exception as ex:
                if failed_log is None:
                    failed_log = open("broadcast_errors.txt", "w")
                failed_log.write(f"{chat_id} — {ex}\n")
                continue

    # ── Result ────────────────────────────────────────────────────────────
    lines = ["<b>✅ Broadcast Complete!</b>\n"]
    if send_groups:
        lines.append(f"🏘 <b>Groups:</b> {g_ok} / {len(groups)} sent")
        if pin_silent or pin_loud:
            lines.append(f"📌 <b>Pinned:</b> {g_pin} groups")
        if g_inactive:
            lines.append(f"🚫 <b>Removed groups:</b> {g_inactive} marked & skipped next time")
    if send_users:
        lines.append(f"👤 <b>Users:</b> {u_ok} / {len(users)} sent")
        if u_inactive:
            lines.append(f"🚫 <b>Blocked users:</b> {u_inactive} marked & skipped next time")
    result_text = "\n".join(lines)

    if failed_log:
        failed_log.close()
        await progress.delete()
        await message.reply_document(
            document="broadcast_errors.txt",
            caption=result_text,
        )
        try:
            os.remove("broadcast_errors.txt")
        except Exception:
            pass
    else:
        await progress.edit_text(result_text)
