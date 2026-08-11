# Copyright (c) 2025 zefronxd
# Licensed under the MIT License.
# This file is part of ZEFXMusic


from random import randint
from time import time

from pymongo import AsyncMongoClient

from zefron import config, logger, userbot


class MongoDB:
    def __init__(self):
        """
        Initialize the MongoDB connection.
        """
        self.mongo = AsyncMongoClient(config.MONGO_URL, serverSelectionTimeoutMS=12500)
        self.db = self.mongo.Anon

        self.admin_list = {}
        self.active_calls = {}
        self.admin_play = []
        self.play_locked = False
        self.blacklisted = []
        self.cmd_delete = []
        self.loop = {}
        self.autoplay: dict[int, dict] = {}   # {chat_id: {"on": bool, "count": int}}
        self.notified = []
        self.cache = self.db.cache
        self.logger = False

        self.rec_cache = self.db.rec_cache          # Last.fm recommendation cache
        self.spotify_cachedb = self.db.spotify_cache  # Spotify playlist cache

        self.assistant = {}
        self.assistantdb = self.db.assistant

        self.auth = {}
        self.authdb = self.db.auth

        self.group_bl = {}
        self.group_bldb = self.db.group_bl

        self.chats = []
        self.chatsdb = self.db.chats

        self.lang = {}
        self.langdb = self.db.lang

        self.users = []
        self.usersdb = self.db.users

    async def connect(self) -> None:
        """Check if we can connect to the database.

        Raises:
            SystemExit: If the connection to the database fails.
        """
        try:
            start = time()
            await self.mongo.admin.command("ping")
            logger.info(f"Database connection successful. ({time() - start:.2f}s)")
            await self.load_cache()
        except Exception as e:
            raise SystemExit(f"Database connection failed: {type(e).__name__}") from e

    async def close(self) -> None:
        """Close the connection to the database."""
        await self.mongo.close()
        logger.info("Database connection closed.")

    # CACHE
    async def get_call(self, chat_id: int) -> bool:
        return chat_id in self.active_calls

    async def add_call(self, chat_id: int) -> None:
        self.active_calls[chat_id] = 1

    async def remove_call(self, chat_id: int) -> None:
        self.active_calls.pop(chat_id, None)

    async def playing(self, chat_id: int, paused: bool = None) -> bool | None:
        if paused is not None:
            self.active_calls[chat_id] = int(not paused)
        return bool(self.active_calls.get(chat_id, 0))

    async def get_admins(self, chat_id: int, reload: bool = False) -> list[int]:
        from zefron.helpers._admins import reload_admins

        if chat_id not in self.admin_list or reload:
            self.admin_list[chat_id] = await reload_admins(chat_id)
        return self.admin_list[chat_id]

    async def get_loop(self, chat_id: int) -> int:
        return self.loop.get(chat_id, 0)

    async def set_loop(self, chat_id: int, count: int) -> None:
        self.loop[chat_id] = count

    # AUTOPLAY
    _AP_EMPTY: dict = {"on": False, "count": 0, "played_ids": set(), "seed": None}

    def _ap_state(self, chat_id: int) -> dict:
        return self.autoplay.get(chat_id, dict(self._AP_EMPTY))

    async def get_autoplay(self, chat_id: int) -> dict:
        """Return autoplay state dict for the chat."""
        return self._ap_state(chat_id)

    async def toggle_autoplay(self, chat_id: int) -> bool:
        """Toggle autoplay and reset count. Returns the new on/off state."""
        new_on = not self._ap_state(chat_id)["on"]
        self.autoplay[chat_id] = {"on": new_on, "count": 0, "played_ids": set(), "seed": None}
        return new_on

    async def set_autoplay(self, chat_id: int, enabled: bool) -> None:
        """Set Smart DJ state explicitly and reset its session."""
        self.autoplay[chat_id] = {
            "on": bool(enabled),
            "count": 0,
            "played_ids": set(),
            "seed": None,
        }

    async def autoplay_tick(self, chat_id: int) -> int:
        """Increment autoplay song counter and return the new count."""
        state = self._ap_state(chat_id)
        state["count"] = state.get("count", 0) + 1
        self.autoplay[chat_id] = state
        return state["count"]

    async def autoplay_set_seed(
        self, chat_id: int, video_id: str, title: str, channel: str
    ) -> None:
        """Store the original user-played song so autoplay can fall back to its recs."""
        state = self._ap_state(chat_id)
        state["seed"] = {"video_id": video_id, "title": title, "channel": channel}
        self.autoplay[chat_id] = state

    async def autoplay_mark_played(self, chat_id: int, video_id: str) -> None:
        """Record a video ID as played during this autoplay session."""
        state = self._ap_state(chat_id)
        state.setdefault("played_ids", set()).add(video_id)
        self.autoplay[chat_id] = state

    async def autoplay_reset_played(self, chat_id: int) -> None:
        """Clear played_ids in the DB (called when top-5 pool is exhausted)."""
        state = self._ap_state(chat_id)
        state["played_ids"] = set()
        self.autoplay[chat_id] = state

    async def reset_autoplay(self, chat_id: int) -> None:
        """Turn off autoplay and reset counter (called on manual stop)."""
        self.autoplay[chat_id] = {"on": False, "count": 0, "played_ids": set(), "seed": None}

    # AUTH METHODS
    async def _get_auth(self, chat_id: int) -> set[int]:
        if chat_id not in self.auth:
            doc = await self.authdb.find_one({"_id": chat_id}) or {}
            self.auth[chat_id] = set(doc.get("user_ids", []))
        return self.auth[chat_id]

    async def is_auth(self, chat_id: int, user_id: int) -> bool:
        return user_id in await self._get_auth(chat_id)

    async def add_auth(self, chat_id: int, user_id: int) -> None:
        users = await self._get_auth(chat_id)
        if user_id not in users:
            users.add(user_id)
            await self.authdb.update_one(
                {"_id": chat_id}, {"$addToSet": {"user_ids": user_id}}, upsert=True
            )

    async def rm_auth(self, chat_id: int, user_id: int) -> None:
        users = await self._get_auth(chat_id)
        if user_id in users:
            users.discard(user_id)
            await self.authdb.update_one(
                {"_id": chat_id}, {"$pull": {"user_ids": user_id}}
            )

    # ASSISTANT METHODS
    async def set_assistant(self, chat_id: int) -> int:
        num = randint(1, len(userbot.clients))
        await self.assistantdb.update_one(
            {"_id": chat_id},
            {"$set": {"num": num}},
            upsert=True,
        )
        self.assistant[chat_id] = num
        return num

    async def get_assistant(self, chat_id: int):
        from zefron import zefx

        if chat_id not in self.assistant:
            doc = await self.assistantdb.find_one({"_id": chat_id})
            num = doc["num"] if doc else None

            if not num or num > len(zefx.clients):
                num = await self.set_assistant(chat_id)
            self.assistant[chat_id] = num

        return zefx.clients[self.assistant[chat_id] - 1]

    async def get_client(self, chat_id: int):
        if chat_id not in self.assistant:
            await self.get_assistant(chat_id)

        num = self.assistant[chat_id]
        if num > len(userbot.clients):
            num = await self.set_assistant(chat_id)
            self.assistant[chat_id] = num

        return {1: userbot.one, 2: userbot.two, 3: userbot.three}.get(num)

    # GROUP BLACKLIST METHODS (per-group, set by group admins)
    async def _get_group_bl(self, chat_id: int) -> set[int]:
        if chat_id not in self.group_bl:
            doc = await self.group_bldb.find_one({"_id": chat_id}) or {}
            self.group_bl[chat_id] = set(doc.get("user_ids", []))
        return self.group_bl[chat_id]

    async def is_group_bl(self, chat_id: int, user_id: int) -> bool:
        return user_id in await self._get_group_bl(chat_id)

    async def add_group_bl(self, chat_id: int, user_id: int) -> None:
        await self.group_bldb.update_one(
            {"_id": chat_id}, {"$addToSet": {"user_ids": user_id}}, upsert=True
        )
        # Invalidate cache so next lookup reads fresh state from DB
        self.group_bl.pop(chat_id, None)

    async def del_group_bl(self, chat_id: int, user_id: int) -> None:
        await self.group_bldb.update_one(
            {"_id": chat_id}, {"$pull": {"user_ids": user_id}}
        )
        # Invalidate cache so next lookup reads fresh state from DB
        self.group_bl.pop(chat_id, None)

    # BLACKLIST METHODS
    async def add_blacklist(self, chat_id: int) -> None:
        if str(chat_id).startswith("-"):
            self.blacklisted.append(chat_id)
            return await self.cache.update_one(
                {"_id": "bl_chats"},
                {"$addToSet": {"chat_ids": chat_id}},
                upsert=True,
            )
        await self.cache.update_one(
            {"_id": "bl_users"},
            {"$addToSet": {"user_ids": chat_id}},
            upsert=True,
        )

    async def del_blacklist(self, chat_id: int) -> None:
        if str(chat_id).startswith("-"):
            self.blacklisted.remove(chat_id)
            return await self.cache.update_one(
                {"_id": "bl_chats"},
                {"$pull": {"chat_ids": chat_id}},
            )
        await self.cache.update_one(
            {"_id": "bl_users"},
            {"$pull": {"user_ids": chat_id}},
        )

    async def get_blacklisted(self, chat: bool = False) -> list[int]:
        if chat:
            if not self.blacklisted:
                doc = await self.cache.find_one({"_id": "bl_chats"})
                self.blacklisted.extend(doc.get("chat_ids", []) if doc else [])
            return self.blacklisted
        doc = await self.cache.find_one({"_id": "bl_users"})
        return doc.get("user_ids", []) if doc else []

    # CHAT METHODS
    async def is_chat(self, chat_id: int) -> bool:
        return chat_id in self.chats

    async def add_chat(self, chat_id: int) -> None:
        if not await self.is_chat(chat_id):
            self.chats.append(chat_id)
            await self.chatsdb.update_one(
                {"_id": chat_id},
                {"$set": {"active": True}},
                upsert=True,
            )

    async def rm_chat(self, chat_id: int) -> None:
        if await self.is_chat(chat_id):
            self.chats.remove(chat_id)
            await self.chatsdb.delete_one({"_id": chat_id})

    async def mark_chat_inactive(self, chat_id: int) -> None:
        """Mark a group as inactive (bot removed/banned). Excludes from future broadcasts."""
        if chat_id in self.chats:
            self.chats.remove(chat_id)
        await self.chatsdb.update_one(
            {"_id": chat_id},
            {"$set": {"active": False}},
            upsert=True,
        )

    async def reactivate_chat(self, chat_id: int) -> bool:
        """If group was previously inactive, re-activate it and return True."""
        doc = await self.chatsdb.find_one({"_id": chat_id, "active": False})
        if not doc:
            return False
        if chat_id not in self.chats:
            self.chats.append(chat_id)
        await self.chatsdb.update_one({"_id": chat_id}, {"$set": {"active": True}})
        return True

    async def get_chats(self) -> list:
        """Return only active chat IDs (active != False)."""
        if not self.chats:
            self.chats.extend([
                chat["_id"]
                async for chat in self.chatsdb.find({"active": {"$ne": False}})
            ])
        return self.chats

    # COMMAND DELETE
    async def get_cmd_delete(self, chat_id: int) -> bool:
        if chat_id not in self.cmd_delete:
            doc = await self.chatsdb.find_one({"_id": chat_id})
            if doc and doc.get("cmd_delete"):
                self.cmd_delete.append(chat_id)
        return chat_id in self.cmd_delete

    async def set_cmd_delete(self, chat_id: int, delete: bool = False) -> None:
        if delete:
            self.cmd_delete.append(chat_id)
        else:
            self.cmd_delete.remove(chat_id)
        await self.chatsdb.update_one(
            {"_id": chat_id},
            {"$set": {"cmd_delete": delete}},
            upsert=True,
        )

    # LANGUAGE METHODS
    async def set_lang(self, chat_id: int, lang_code: str):
        await self.langdb.update_one(
            {"_id": chat_id},
            {"$set": {"lang": lang_code}},
            upsert=True,
        )
        self.lang[chat_id] = lang_code

    async def get_lang(self, chat_id: int) -> str:
        if chat_id not in self.lang:
            doc = await self.langdb.find_one({"_id": chat_id})
            self.lang[chat_id] = doc["lang"] if doc else config.LANG_CODE
        return self.lang[chat_id]

    # LOGGER METHODS
    async def is_logger(self) -> bool:
        return self.logger

    async def get_logger(self) -> bool:
        doc = await self.cache.find_one({"_id": "logger"})
        if doc:
            self.logger = doc["status"]
        return self.logger

    async def set_logger(self, status: bool) -> None:
        self.logger = status
        await self.cache.update_one(
            {"_id": "logger"},
            {"$set": {"status": status}},
            upsert=True,
        )

    # PLAY MODE METHODS
    async def get_play_mode(self, chat_id: int) -> bool:
        if chat_id not in self.admin_play:
            doc = await self.chatsdb.find_one({"_id": chat_id})
            if doc and doc.get("admin_play"):
                self.admin_play.append(chat_id)
        return chat_id in self.admin_play

    async def set_play_mode(self, chat_id: int, remove: bool = False) -> None:
        if remove and chat_id in self.admin_play:
            self.admin_play.remove(chat_id)
        else:
            self.admin_play.append(chat_id)
        await self.chatsdb.update_one(
            {"_id": chat_id},
            {"$set": {"admin_play": not remove}},
            upsert=True,
        )

    # PLAY LOCK METHODS (global, in-memory only)
    def is_play_locked(self) -> bool:
        return self.play_locked

    def lock_play(self) -> None:
        self.play_locked = True

    def unlock_play(self) -> None:
        self.play_locked = False

    # SUDO METHODS
    async def add_sudo(self, user_id: int) -> None:
        await self.cache.update_one(
            {"_id": "sudoers"}, {"$addToSet": {"user_ids": user_id}}, upsert=True
        )

    async def del_sudo(self, user_id: int) -> None:
        await self.cache.update_one(
            {"_id": "sudoers"}, {"$pull": {"user_ids": user_id}}
        )

    async def get_sudoers(self) -> list[int]:
        doc = await self.cache.find_one({"_id": "sudoers"})
        return doc.get("user_ids", []) if doc else []

    # USER METHODS
    async def is_user(self, user_id: int) -> bool:
        return user_id in self.users

    async def add_user(self, user_id: int) -> None:
        if not await self.is_user(user_id):
            self.users.append(user_id)
            await self.usersdb.update_one(
                {"_id": user_id},
                {"$set": {"active": True}},
                upsert=True,
            )

    async def rm_user(self, user_id: int) -> None:
        if await self.is_user(user_id):
            self.users.remove(user_id)
            await self.usersdb.delete_one({"_id": user_id})

    async def mark_user_inactive(self, user_id: int) -> None:
        """Mark a user as inactive (e.g. blocked the bot). Excludes from future broadcasts."""
        if user_id in self.users:
            self.users.remove(user_id)
        await self.usersdb.update_one(
            {"_id": user_id},
            {"$set": {"active": False}},
            upsert=True,
        )

    async def reactivate_user(self, user_id: int) -> bool:
        """If user was previously inactive, re-activate them and return True."""
        doc = await self.usersdb.find_one({"_id": user_id, "active": False})
        if not doc:
            return False
        if user_id not in self.users:
            self.users.append(user_id)
        await self.usersdb.update_one({"_id": user_id}, {"$set": {"active": True}})
        return True

    async def get_users(self) -> list:
        """Return only active user IDs (active != False)."""
        if not self.users:
            self.users.extend([
                user["_id"]
                async for user in self.usersdb.find({"active": {"$ne": False}})
            ])
        return self.users

    # SPOTIFY PLAYLIST CACHE (official Web API metadata)
    _SP_TTL = 7 * 86400  # 7 days

    async def get_spotify_cache(self, spotify_id: str) -> list[dict] | None:
        """Return cached Spotify metadata for a playlist/album, or None if stale."""
        doc = await self.spotify_cachedb.find_one({"_id": spotify_id})
        if not doc:
            return None
        if time() - doc.get("cached_at", 0) > self._SP_TTL:
            return None
        return doc.get("tracks") or None

    async def set_spotify_cache(self, spotify_id: str, tracks: list[dict]) -> None:
        """Upsert Spotify metadata for a playlist/album with current timestamp."""
        await self.spotify_cachedb.update_one(
            {"_id": spotify_id},
            {"$set": {"tracks": tracks, "cached_at": time()}},
            upsert=True,
        )

    async def invalidate_spotify_cache(self, spotify_id: str) -> None:
        """Force-delete a cached playlist (e.g. on manual refresh)."""
        await self.spotify_cachedb.delete_one({"_id": spotify_id})

    # FAVOURITE METHODS (per-user, stored in MongoDB)
    @property
    def _favdb(self):
        return self.db.favourites

    async def get_favourites(self, user_id: int) -> list[dict]:
        """Return the list of favourite song dicts for a user."""
        try:
            doc = await self._favdb.find_one({"_id": user_id})
            return doc.get("songs", []) if doc else []
        except Exception:
            return []

    async def is_favourite(self, user_id: int, video_id: str) -> bool:
        """Check if a song is already in user's favourites."""
        songs = await self.get_favourites(user_id)
        return any(s.get("id") == video_id for s in songs)

    async def add_favourite(self, user_id: int, song: dict) -> bool:
        """Add a song to user's favourites. Returns True if added, False if already existed."""
        if await self.is_favourite(user_id, song.get("id", "")):
            return False
        await self._favdb.update_one(
            {"_id": user_id},
            {"$push": {"songs": song}},
            upsert=True,
        )
        return True

    async def remove_favourite(self, user_id: int, video_id: str) -> bool:
        """Remove a song from user's favourites. Returns True if removed."""
        songs = await self.get_favourites(user_id)
        if not any(s.get("id") == video_id for s in songs):
            return False
        await self._favdb.update_one(
            {"_id": user_id},
            {"$pull": {"songs": {"id": video_id}}},
        )
        return True

    async def migrate_coll(self) -> None:
        logger.info("Migrating users and chats from old collections...")

        users, musers, mchats = [], [], []
        seen_chats, seen_users = set(), set()
        users.extend([user async for user in self.usersdb.find()])
        users.extend([user async for user in self.db.tgusersdb.find()])

        for user in users:
            _id = user.get("_id")
            if isinstance(_id, int):
                user_id = _id
            else:
                user_id = int(user.get("user_id"))

            if user_id in seen_users:
                continue
            seen_users.add(user_id)
            musers.append({"_id": user_id})

        await self.usersdb.drop()
        await self.db.tgusersdb.drop()
        if musers:
            await self.usersdb.insert_many(musers)

        async for chat in self.chatsdb.find():
            _id = chat.get("_id")
            if isinstance(_id, int):
                chat_id = _id
            else:
                chat_id = int(chat.get("chat_id"))

            if chat_id in seen_chats:
                continue
            seen_chats.add(chat_id)
            mchats.append({"_id": chat_id})

        await self.chatsdb.drop()
        if mchats:
            await self.chatsdb.insert_many(mchats)

        await self.cache.insert_one({"_id": "migrated"})
        logger.info("Migration completed successfully.")

    async def load_cache(self) -> None:
        doc = await self.cache.find_one({"_id": "migrated"})
        if not doc:
            await self.migrate_coll()

        await self.get_chats()
        await self.get_users()
        await self.get_blacklisted(True)
        await self.get_logger()
        logger.info("Database cache loaded.")
