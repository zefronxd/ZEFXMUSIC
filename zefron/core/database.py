# Riruru database.py


import asyncpg
import os
import json
import pytz
from datetime import datetime, timedelta
import asyncio
from collections import OrderedDict
from zefron import logger 
from dotenv import load_dotenv

load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")

db_pool = None
MAX_PROFILE_CACHE = 100000
known_info_cache = OrderedDict()
profile_cache_lock = asyncio.Lock()   

async def init_db_pool():
    global db_pool
    if not db_pool:
        try:
            db_pool = await asyncpg.create_pool(
                dsn=DATABASE_URL,
                min_size=5,
                max_size=50,
                command_timeout=10 
            )
            logger.info("PostgreSQL Connection Pool Initialized for Zefron Chatbot")
        except Exception as e:
            logger.error(f"Failed to create database pool: {e}")

async def get_db_pool():
    global db_pool
    if not db_pool:
        await init_db_pool()
    return db_pool

async def execute_query(query: str, *args):
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        return await conn.execute(query, *args)

async def fetch_query(query: str, *args):
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        return await conn.fetch(query, *args)

def get_block_info():
    tz = pytz.timezone('Asia/Kolkata')
    now = datetime.now(tz)
    block_index = now.hour // 6
    block_key = f"{now.strftime('%Y-%m-%d')}_B{block_index}"
    block_start = now.replace(hour=block_index * 6, minute=0, second=0, microsecond=0)
    next_reset = block_start + timedelta(hours=6)
    return block_key, next_reset

LIMIT_PER_BLOCK = 400  

async def check_usage_and_allow(user_id: int):
    block_key, next_reset = get_block_info()
    rows = await fetch_query("SELECT * FROM user_usage WHERE user_id=$1", user_id)
    
    if not rows:
        return True, None

    user = rows[0]
    if user["usage_date"] != block_key:
        return True, None

    if user["message_count"] >= LIMIT_PER_BLOCK:
        tz = pytz.timezone('Asia/Kolkata')
        now = datetime.now(tz)
        remaining = next_reset - now
        total_seconds = max(int(remaining.total_seconds()), 0)
        hours, rem = divmod(total_seconds, 3600)
        minutes = rem // 60
        return False, f"{hours:02d}H:{minutes:02d}M"

    return True, None

async def increment_message_and_tokens(user_id: int, in_tokens: int, out_tokens: int):
    try:
        block_key, _ = get_block_info()  
        user_query = """
            INSERT INTO user_usage (user_id, usage_date, message_count, tokens_used)
            VALUES ($1, $2, 1, $3)
            ON CONFLICT (user_id) DO UPDATE SET
            message_count = CASE 
                WHEN user_usage.usage_date = EXCLUDED.usage_date THEN user_usage.message_count + 1 
                ELSE 1 
            END,
            tokens_used = CASE 
                WHEN user_usage.usage_date = EXCLUDED.usage_date THEN user_usage.tokens_used + EXCLUDED.tokens_used
                ELSE EXCLUDED.tokens_used 
            END,
            usage_date = EXCLUDED.usage_date
        """
        await execute_query(user_query, user_id, block_key, in_tokens + out_tokens) 
    except Exception as e:
        logger.error(f"Error in background token logging: {e}")

async def save_user_personal_info(user_id: int, info: dict):
    if not info:
        return
    query = """
        INSERT INTO user_profiles (user_id, profile_data, updated_at)
        VALUES ($1, $2::jsonb, now())
        ON CONFLICT (user_id)
        DO UPDATE SET profile_data = user_profiles.profile_data || EXCLUDED.profile_data,
                      updated_at = now();
    """
    await execute_query(query, user_id, json.dumps(info))

async def get_user_personal_info(user_id: int) -> dict:
    rows = await fetch_query("SELECT profile_data FROM user_profiles WHERE user_id=$1", user_id)
    if not rows:
        return {}
    raw = rows[0]["profile_data"]
    if isinstance(raw, dict):
        return raw          
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except:
            return {}
    return {}

async def get_cached_known_info(user_id: int) -> dict:
    async with profile_cache_lock:
        if user_id in known_info_cache:
            known_info_cache.move_to_end(user_id)
            return known_info_cache[user_id]

    info = await get_user_personal_info(user_id)

    async with profile_cache_lock:
        known_info_cache[user_id] = info
        known_info_cache.move_to_end(user_id)
        if len(known_info_cache) > MAX_PROFILE_CACHE:
            known_info_cache.popitem(last=False)   
    return info

async def update_cached_known_info(user_id: int, new_info: dict):
    async with profile_cache_lock:
        existing = known_info_cache.get(user_id, {})
        merged = {**existing, **new_info}   
        known_info_cache[user_id] = merged
        known_info_cache.move_to_end(user_id)
