# Copyright (c) 2025 zefronxd
# Licensed under the MIT License.
# This file is part of ZEFXMusic


from pyrogram import enums, types

from zefron import app, config, lang
from zefron.core.lang import lang_codes


class Inline:
    def __init__(self):
        self.ikm = types.InlineKeyboardMarkup
        self.ikb = types.InlineKeyboardButton

    def cancel_dl(self, text) -> types.InlineKeyboardMarkup:
        return self.ikm([[
            self.ikb(text=text, callback_data="cancel_dl", style=enums.ButtonStyle.DANGER)
        ]])

    def controls(
        self,
        chat_id: int,
        status: str = None,
        timer: str = None,
        remove: bool = False,
        autoplay_on: bool = False,
        track_id: str = "",
        is_faved: bool = False,
    ) -> types.InlineKeyboardMarkup:
        keyboard = []
        if status:
            keyboard.append([
                self.ikb(text=status, callback_data=f"controls status {chat_id}", style=enums.ButtonStyle.PRIMARY)
            ])
        elif timer:
            keyboard.append([
                self.ikb(text=timer, callback_data=f"controls status {chat_id}", style=enums.ButtonStyle.PRIMARY)
            ])

        if not remove:
            keyboard.append([
                self.ikb(text="▶", callback_data=f"controls resume {chat_id}", style=enums.ButtonStyle.SUCCESS),
                self.ikb(text="⏸", callback_data=f"controls pause {chat_id}", style=enums.ButtonStyle.PRIMARY),
                self.ikb(text="↺", callback_data=f"controls replay {chat_id}", style=enums.ButtonStyle.PRIMARY),
                self.ikb(text="⏭", callback_data=f"controls skip {chat_id}", style=enums.ButtonStyle.PRIMARY),
                self.ikb(text="⏹", callback_data=f"controls stop {chat_id}", style=enums.ButtonStyle.DANGER),
            ])
            ap_text  = "🔁 ᴀᴜᴛᴏ ✓" if autoplay_on else "🔁 ᴀᴜᴛᴏ"
            ap_style = enums.ButtonStyle.SUCCESS if autoplay_on else enums.ButtonStyle.PRIMARY
            fav_text  = "✦ ꜰᴀᴠ" if is_faved else "ꜰᴀᴠ"
            fav_style = enums.ButtonStyle.DANGER
            row = [
                self.ikb(text="Suggest", callback_data=f"rec_show {chat_id}", style=enums.ButtonStyle.PRIMARY),
                self.ikb(text=fav_text, callback_data=f"fav_toggle {chat_id}", style=fav_style),
                self.ikb(text=ap_text, callback_data=f"autoplay_toggle {chat_id}", style=ap_style),
            ]
            keyboard.append(row)
            keyboard.append([
                self.ikb(
                    text="✕ ᴄʟᴏsᴇ",
                    callback_data=f"close_message {chat_id}",
                    style=enums.ButtonStyle.DANGER,
                )
            ])
        return self.ikm(keyboard)

    def help_markup(
        self, _lang: dict, back: bool = False
    ) -> types.InlineKeyboardMarkup:
        if back:
            rows = [[
                self.ikb(text=_lang["back"], callback_data="help back", style=enums.ButtonStyle.PRIMARY),
                self.ikb(text=_lang["close"], callback_data="help close", style=enums.ButtonStyle.DANGER),
            ]]
        else:
            cb_idx = {"admins": 0, "auth": 1, "lang": 3, "ping": 4, "play": 5, "queue": 6, "fav": 9}
            cbs = ["admins", "auth", "lang", "ping", "play", "queue", "fav"]
            buttons = [
                self.ikb(
                    text=_lang[f"help_{cb_idx[cb]}"],
                    callback_data=f"help {cb}",
                    style=enums.ButtonStyle.PRIMARY,
                )
                for cb in cbs
            ]
            rows = [buttons[i: i + 3] for i in range(0, len(buttons), 3)]

        return self.ikm(rows)

    def lang_markup(self, _lang: str) -> types.InlineKeyboardMarkup:
        langs = lang.get_languages()
        buttons = [
            self.ikb(
                text=f"{name}  {'✔' if code == _lang else ''}".strip(),
                callback_data=f"lang_change {code}",
                style=enums.ButtonStyle.SUCCESS if code == _lang else enums.ButtonStyle.PRIMARY,
            )
            for code, name in langs.items()
        ]
        rows = [buttons[i: i + 2] for i in range(0, len(buttons), 2)]
        return self.ikm(rows)

    def ping_markup(self, text: str) -> types.InlineKeyboardMarkup:
        return self.ikm([[
            self.ikb(text=text, url=config.SUPPORT_CHAT, style=enums.ButtonStyle.SUCCESS)
        ]])

    def play_queued(
        self, chat_id: int, item_id: str, _text: str
    ) -> types.InlineKeyboardMarkup:
        return self.ikm([[
            self.ikb(
                text=f"▶  {_text}",
                callback_data=f"controls force {chat_id} {item_id}",
                style=enums.ButtonStyle.SUCCESS,
            ),
            self.ikb(
                text="☘️ ᴀᴅᴅ ᴛᴏ ꜰᴀᴠ",
                callback_data=f"fav_queue {chat_id} {item_id}",
                style=enums.ButtonStyle.DANGER,
            ),
        ]])

    def queue_markup(
        self, chat_id: int, _text: str, playing: bool, page: int = 0,
        total_pages: int = 1
    ) -> types.InlineKeyboardMarkup:
        _action = "pause" if playing else "resume"
        _style = enums.ButtonStyle.PRIMARY if playing else enums.ButtonStyle.SUCCESS
        rows = [
            [
                self.ikb(text="▶ ᴘʟᴀʏ ɴᴏᴡ ", callback_data=f"controls resume {chat_id} q", style=enums.ButtonStyle.SUCCESS),
                self.ikb(text="☘️ ᴀᴅᴅ ᴛᴏ ꜰᴀᴠ", callback_data=f"fav_toggle {chat_id}", style=enums.ButtonStyle.DANGER),
            ],
            [
                self.ikb(text=_text, callback_data=f"controls {_action} {chat_id} q", style=_style)
            ],
        ]
        if total_pages > 1:
            rows.append([
                self.ikb(
                    text="◀ Previous",
                    callback_data=f"queue_page {chat_id} {page - 1}",
                    style=enums.ButtonStyle.PRIMARY,
                ) if page > 0 else self.ikb(
                    text="·",
                    callback_data=f"queue_page {chat_id} {page}",
                    style=enums.ButtonStyle.PRIMARY,
                ),
                self.ikb(
                    text=f"{page + 1}/{total_pages}",
                    callback_data=f"queue_page {chat_id} {page}",
                    style=enums.ButtonStyle.PRIMARY,
                ),
                self.ikb(
                    text="Next ▶",
                    callback_data=f"queue_page {chat_id} {page + 1}",
                    style=enums.ButtonStyle.PRIMARY,
                ) if page < total_pages - 1 else self.ikb(
                    text="·",
                    callback_data=f"queue_page {chat_id} {page}",
                    style=enums.ButtonStyle.PRIMARY,
                ),
            ])
        return self.ikm(rows)

    def playlist_pages(
        self, chat_id: int, message_id: int, page: int, total_pages: int
    ) -> types.InlineKeyboardMarkup:
        rows = []
        if total_pages > 1:
            rows.append([
                self.ikb(
                    text="◀ Previous",
                    callback_data=f"playlist_page {chat_id} {message_id} {page - 1}",
                    style=enums.ButtonStyle.PRIMARY,
                ) if page > 0 else self.ikb(
                    text="·",
                    callback_data=f"playlist_page {chat_id} {message_id} {page}",
                    style=enums.ButtonStyle.PRIMARY,
                ),
                self.ikb(
                    text=f"{page + 1}/{total_pages}",
                    callback_data=f"playlist_page {chat_id} {message_id} {page}",
                    style=enums.ButtonStyle.PRIMARY,
                ),
                self.ikb(
                    text="Next ▶",
                    callback_data=f"playlist_page {chat_id} {message_id} {page + 1}",
                    style=enums.ButtonStyle.PRIMARY,
                ) if page < total_pages - 1 else self.ikb(
                    text="·",
                    callback_data=f"playlist_page {chat_id} {message_id} {page}",
                    style=enums.ButtonStyle.PRIMARY,
                ),
            ])
        return self.ikm(rows)

    def settings_markup(
        self, lang: dict, admin_only: bool, cmd_delete: bool, language: str, chat_id: int
    ) -> types.InlineKeyboardMarkup:
        return self.ikm([
            [
                self.ikb(
                    text="🎵  " + lang["play_mode"],
                    callback_data="settings",
                    style=enums.ButtonStyle.PRIMARY,
                ),
                self.ikb(text=admin_only, callback_data="settings play", style=enums.ButtonStyle.SUCCESS),
            ],
            [
                self.ikb(
                    text="🗑  " + lang["cmd_delete"],
                    callback_data="settings",
                    style=enums.ButtonStyle.PRIMARY,
                ),
                self.ikb(text=cmd_delete, callback_data="settings delete", style=enums.ButtonStyle.SUCCESS),
            ],
            [
                self.ikb(
                    text=lang["language"],
                    callback_data="settings",
                    style=enums.ButtonStyle.PRIMARY,
                ),
                self.ikb(text=lang_codes[language], callback_data="language", style=enums.ButtonStyle.SUCCESS),
            ],
        ])

    def start_key(
        self, lang: dict, private: bool = False
    ) -> types.InlineKeyboardMarkup:
        rows = [
            [
                self.ikb(
                    text="⌾ ᴄʜᴧᴛ ᴡɪᴛʜ ϻє ⌾",
                    callback_data="chat_with_me_btn",
                    style=enums.ButtonStyle.SUCCESS,
                )
            ],
            [
                self.ikb(text=lang["help"], callback_data="start_help", style=enums.ButtonStyle.PRIMARY),
                self.ikb(text=lang["support"], url=config.SUPPORT_CHAT, style=enums.ButtonStyle.PRIMARY),
            ],
            [
                self.ikb(text=lang["channel"], url=config.SUPPORT_CHANNEL, style=enums.ButtonStyle.PRIMARY),
                self.ikb(
                    text="˹ ❍ᴡηєʀ ˼",
                    url="https://t.me/II_ALONE_III",
                    style=enums.ButtonStyle.DANGER,
                ),
            ],
            # Add me to group button sabse niche
            [
                self.ikb(
                    text=lang["add_me"],
                    url=f"https://t.me/{app.username}?startgroup=true",
                    style=enums.ButtonStyle.SUCCESS,
                )
            ],
        ]
        if not private:
            rows += [[
                self.ikb(text=lang["language"], callback_data="language", style=enums.ButtonStyle.PRIMARY)
            ]]
        return self.ikm(rows)

    def start_help_markup(self, lang: dict) -> types.InlineKeyboardMarkup:
        """Help category buttons — shown inside the start photo (edits caption)."""
        entries = [
            ("help_0", "admins"), ("help_1", "auth"),  ("help_3", "lang"),
            ("help_4", "ping"),   ("help_5", "play"),  ("help_6", "queue"),
            ("help_9", "fav"),
        ]
        btns = [
            self.ikb(
                text=lang[lk],
                callback_data=f"start_help {cat}",
                style=enums.ButtonStyle.PRIMARY,
            )
            for lk, cat in entries
        ]
        rows = [btns[i:i+3] for i in range(0, len(btns), 3)]
        rows.append([
            self.ikb(
                text=lang["back"],
                callback_data="start_help back",
                style=enums.ButtonStyle.DANGER,
            )
        ])
        return self.ikm(rows)

    def start_help_cat_markup(self, lang: dict) -> types.InlineKeyboardMarkup:
        """Back/close buttons shown inside a help category."""
        return self.ikm([[
            self.ikb(text=lang["back"],  callback_data="start_help menu", style=enums.ButtonStyle.PRIMARY),
            self.ikb(text=lang["close"], callback_data="start_help back", style=enums.ButtonStyle.DANGER),
        ]])

    def yt_key(self, link: str) -> types.InlineKeyboardMarkup:
        return self.ikm([[
            self.ikb(text="⎘  Copy Link", copy_text=link, style=enums.ButtonStyle.PRIMARY),
            self.ikb(text="▶  YouTube", url=link, style=enums.ButtonStyle.DANGER),
        ]])
