# Copyright (c) 2025 zefronxd
# Licensed under the MIT License.
# This file is part of ZEFXMusic


import pyrogram
import os
import time
import asyncio
from zefron import config, logger
from zefron.core.database import init_db_pool

async def auto_clean_downloads():
    """Background task jo har ghante purane downloaded songs ko delete karta hai okei zefron beta."""
    while True:
        try:
            downloads_dir = "downloads"
            age_limit = 7200 
            current_time = time.time()
            
            if os.path.exists(downloads_dir):
                count = 0
                for file in os.listdir(downloads_dir):
                    file_path = os.path.join(downloads_dir, file)
                    if os.path.isfile(file_path):
                        if (current_time - os.path.getctime(file_path)) > age_limit:
                            os.remove(file_path)
                            count += 1
                
                if count > 0:
                    from zefron import logger
                    logger.info(f"🧹 Storage Saver: Automatically deleted {count} old downloaded songs.")
        except Exception as e:
            from zefron import logger
            logger.error(f"Cleanup Error: {e}")
        
        await asyncio.sleep(3600)

class Bot(pyrogram.Client):
    def __init__(self):
        super().__init__(
            name="zefron",
            api_id=config.API_ID,
            api_hash=config.API_HASH,
            bot_token=config.BOT_TOKEN,
            parse_mode=pyrogram.enums.ParseMode.HTML,
            max_concurrent_transmissions=7,
            link_preview_options=pyrogram.types.LinkPreviewOptions(is_disabled=True),
        )
        self.owner = config.OWNER_ID
        self.logger = config.LOGGER_ID
        self.bl_users = pyrogram.filters.user()
        self.sudoers = pyrogram.filters.user(self.owner)

    async def boot(self):
        """
        Starts the bot and performs initial setup.

        Raises:
            SystemExit: If the bot fails to access the log group or is not an administrator in the logger group.
        """

        asyncio.create_task(auto_clean_downloads())
        await init_db_pool()
        await super().start()
        self.id = self.me.id
        self.name = self.me.first_name
        self.username = self.me.username
        self.mention = self.me.mention

        try:
            await self.send_message(self.logger, "Bot Started")
            get = await self.get_chat_member(self.logger, self.id)
        except Exception as ex:
            raise SystemExit(f"Bot has failed to access the log group: {self.logger}\nReason: {ex}")

        if get.status != pyrogram.enums.ChatMemberStatus.ADMINISTRATOR:
            raise SystemExit("Please promote the bot as an admin in logger group.")
        logger.info(f"Bot started as @{self.username}")

    async def exit(self):
        """
        Asynchronously stops the bot.
        """
        await super().stop()
        logger.info("Bot stopped.")
