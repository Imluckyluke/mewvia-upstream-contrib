"""Visual progress indicator and live progress bar for media operations.

Provides:
- Sleek unicode visual progress bars: [▰▰▰▰▱▱▱▱▱▱] ۳۵٪
- Dynamic loading GIF animation support with animated captions
- Resilient fallback to high-tech text progress indicators
- Background progress ticker with smooth progression
- Duck-typed interface compatible with aiogram Message (edit_text, delete, message_id)
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any

from aiogram import Bot, html
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.units import fa_digits
from app.media.progress_gif import get_random_loading_gif
from app.ui.emoji import EmojiRegistry, get_emoji_registry

if TYPE_CHECKING:
    pass

log = get_logger(__name__)


def render_progress_bar(percent: int, length: int = 10) -> str:
    """Format visual progress bar: e.g. [▰▰▰▰▱▱▱▱▱▱] ۳۵٪."""
    p = max(0, min(100, int(percent)))
    filled = round(length * p / 100)
    bar = "▰" * filled + "▱" * (length - filled)
    return f"[{bar}] {fa_digits(str(p))}%"


def format_progress_caption(
    stage_title: str,
    percent: int,
    registry: EmojiRegistry | None = None,
    detail: str | None = None,
) -> str:
    """Format sleek progress message or animation caption."""
    ic_clock = registry.text("clock") if registry else "⏳"
    ic_lightning = registry.text("lightning") if registry else "⚡"
    bar_str = render_progress_bar(percent)

    lines = [
        f"{ic_clock} <b>{html.quote(stage_title)}</b>\n",
        f"<code>{bar_str}</code>",
    ]
    if detail:
        lines.append(f"{ic_lightning} <i>{html.quote(detail)}</i>")
    else:
        lines.append(f"{ic_lightning} <i>لطفاً چند لحظه شکیبا باشید…</i>")

    return "\n".join(lines)


class ProgressController:
    """Live progress indicator controller for long-running media operations."""

    def __init__(
        self,
        bot: Bot,
        chat_id: int,
        message_id: int,
        is_animation: bool,
        stage_title: str,
        registry: EmojiRegistry | None = None,
        auto_tick: bool = True,
    ) -> None:
        self.bot = bot
        self.chat_id = chat_id
        self.message_id = message_id
        self.is_animation = is_animation
        self.stage_title = stage_title
        self.percent = 0 if not auto_tick else 15
        self.registry = registry
        self._completed = False
        self._last_edit_time = time.perf_counter()
        self._tick_task: asyncio.Task[None] | None = None

        if auto_tick:
            self._tick_task = asyncio.create_task(self._ticker_loop())

    async def _ticker_loop(self) -> None:
        """Gradually advance progress percentage while operation runs."""
        progression_steps = [28, 42, 57, 71, 82, 89, 94]
        for step in progression_steps:
            try:
                await asyncio.sleep(1.8)
                if self._completed:
                    break
                await self.update_stage(self.stage_title, percent=step)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                log.debug("progress_ticker_tick_failed", error=str(exc))
                break

    @classmethod
    async def start(
        cls,
        bot: Bot,
        chat_id: int,
        reply_to_id: int | None = None,
        initial_stage: str = "در حال پردازش رسانه…",
        session: AsyncSession | None = None,
        replace_msg: Message | None = None,
        auto_tick: bool = True,
    ) -> ProgressController:
        """Instantiate and send a progress indicator (GIF or text) into the chat."""
        registry: EmojiRegistry | None = None
        try:
            registry = get_emoji_registry()
            await registry.refresh_premium_flag()
            await registry.load()
        except Exception:
            pass

        # Check for configured loading GIF
        gif_file_id: str | None = None
        try:
            gif_file_id = await get_random_loading_gif(session)
        except Exception as exc:
            log.warning("get_random_loading_gif_failed", error=str(exc))

        caption = format_progress_caption(
            initial_stage, percent=(0 if not auto_tick else 15), registry=registry
        )

        if replace_msg is not None:
            try:
                await replace_msg.delete()
            except Exception:
                pass

        # Try sending as animation if GIF is available
        if gif_file_id:
            try:
                sent_anim = await bot.send_animation(
                    chat_id=chat_id,
                    animation=gif_file_id,
                    caption=caption,
                    parse_mode="HTML",
                    reply_to_message_id=reply_to_id,
                )
                if sent_anim and sent_anim.message_id:
                    return cls(
                        bot=bot,
                        chat_id=chat_id,
                        message_id=sent_anim.message_id,
                        is_animation=True,
                        stage_title=initial_stage,
                        registry=registry,
                        auto_tick=auto_tick,
                    )
            except Exception as anim_exc:
                log.warning("send_loading_animation_failed_fallback_text", error=str(anim_exc))

        # Fallback to high-tech text message progress indicator
        sent_msg = await bot.send_message(
            chat_id=chat_id,
            text=caption,
            parse_mode="HTML",
            reply_to_message_id=reply_to_id,
        )
        msg_id = sent_msg.message_id if sent_msg else 0
        return cls(
            bot=bot,
            chat_id=chat_id,
            message_id=msg_id,
            is_animation=False,
            stage_title=initial_stage,
            registry=registry,
            auto_tick=auto_tick,
        )

    async def update_stage(
        self,
        stage_title: str,
        percent: int | None = None,
        detail: str | None = None,
        force: bool = False,
    ) -> None:
        """Update the displayed stage and progress bar."""
        if self._completed or not self.message_id:
            return

        self.stage_title = stage_title
        if percent is not None:
            self.percent = max(0, min(100, percent))
            if self._tick_task and not self._tick_task.done():
                self._tick_task.cancel()

        now = time.perf_counter()
        if not force and (now - self._last_edit_time < 1.4):
            return
        self._last_edit_time = now

        caption = format_progress_caption(
            self.stage_title, self.percent, registry=self.registry, detail=detail
        )

        try:
            if self.is_animation:
                await self.bot.edit_message_caption(
                    chat_id=self.chat_id,
                    message_id=self.message_id,
                    caption=caption,
                    parse_mode="HTML",
                )
            else:
                await self.bot.edit_message_text(
                    chat_id=self.chat_id,
                    message_id=self.message_id,
                    text=caption,
                    parse_mode="HTML",
                )
        except TelegramBadRequest as exc:
            if "message is not modified" not in str(exc).lower():
                log.debug("progress_edit_failed", error=str(exc))
        except Exception as exc:
            log.debug("progress_edit_error", error=str(exc))

    async def complete(self, delete: bool = True) -> None:
        """Complete the operation and cleanup the loading indicator."""
        self._completed = True
        if self._tick_task and not self._tick_task.done():
            self._tick_task.cancel()

        if delete and self.message_id:
            try:
                await self.bot.delete_message(self.chat_id, self.message_id)
            except Exception as exc:
                log.debug("progress_delete_failed", error=str(exc))
            self.message_id = 0

    async def fail(self, error_message: str) -> None:
        """Show an error message and cleanup."""
        self._completed = True
        if self._tick_task and not self._tick_task.done():
            self._tick_task.cancel()

        if self.message_id:
            if self.is_animation:
                try:
                    await self.bot.delete_message(self.chat_id, self.message_id)
                except Exception:
                    pass
                try:
                    await self.bot.send_message(self.chat_id, error_message)
                except Exception:
                    pass
                self.message_id = 0
            else:
                try:
                    await self.bot.edit_message_text(
                        chat_id=self.chat_id,
                        message_id=self.message_id,
                        text=error_message,
                    )
                except Exception:
                    try:
                        await self.bot.send_message(self.chat_id, error_message)
                    except Exception:
                        pass

    # Duck-typing methods for compatibility with code that treats status_msg as a Message
    async def edit_text(self, text: str, **kwargs: Any) -> Any:
        if "❌" in text or "خطا" in text or "ناموفق" in text or "نیاز به" in text:
            await self.fail(text)
        else:
            await self.update_stage(text, force=True)
        return self

    async def delete(self) -> Any:
        await self.complete(delete=True)

    async def __aenter__(self) -> ProgressController:
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if exc_val is not None:
            await self.fail(str(exc_val))
        else:
            await self.complete(delete=True)
