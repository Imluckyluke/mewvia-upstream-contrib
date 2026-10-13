"""Bot and Dispatcher construction.

Kept separate from `__main__` so tests can build a dispatcher without starting
polling, and so the session choice (public vs local Bot API) lives in one place.
"""

from __future__ import annotations

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.enums import ParseMode
from aiogram.fsm.storage.base import BaseStorage
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.storage.redis import RedisStorage
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.core.logging import get_logger
from app.features import load_features
from app.middlewares import setup_middlewares
from app.ui.premium_guard import PremiumEmojiGuard

log = get_logger(__name__)


def build_bot(settings: Settings) -> Bot:
    """Create the Bot, pointing at the local API server when configured."""
    session: AiohttpSession | None = None
    if settings.uses_local_api:
        base = settings.telegram_api_url.rstrip("/")
        session = AiohttpSession(
            api=TelegramAPIServer(
                base=f"{base}/bot{{token}}/{{method}}",
                file=f"{base}/file/bot{{token}}/{{path}}",
                is_local=True,
            ),
            timeout=300,
        )
        log.info("using_local_bot_api", base=base)
    else:
        session = AiohttpSession(timeout=300)

    bot = Bot(
        token=settings.bot_token,
        session=session,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    # Retries any request Telegram refuses because of a custom emoji, with the
    # premium emoji stripped out. Registered on the session so every send in the
    # application is covered without per-call-site guards.
    bot.session.middleware(PremiumEmojiGuard())
    return bot


def build_storage(settings: Settings) -> BaseStorage:
    """Redis-backed FSM so state survives restarts; memory as a fallback."""
    if settings.redis_url:
        return RedisStorage.from_url(
            settings.redis_url,
            connection_kwargs={"max_connections": 50},
        )
    log.warning("redis_url_missing_using_memory_storage")
    return MemoryStorage()


def build_dispatcher(
    settings: Settings,
    sessionmaker: async_sessionmaker[AsyncSession] | None = None,
) -> Dispatcher:
    """Wire storage, middlewares and every registered feature router.

    `sessionmaker` is optional so tests can build a dispatcher without a
    database; middlewares that need one are then skipped.
    """
    dp = Dispatcher(storage=build_storage(settings), settings=settings)

    if sessionmaker is not None:
        setup_middlewares(dp, settings, sessionmaker)

    features = load_features()
    for feature in features:
        dp.include_router(feature.router)
    log.info("features_loaded", count=len(features), names=[f.name for f in features])

    return dp
