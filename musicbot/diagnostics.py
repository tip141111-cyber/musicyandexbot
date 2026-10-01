import asyncio
import logging
import time
from collections.abc import Callable
from typing import TypeVar

from discord.ext import commands

from .config import BOT_HEALTH_LOG_SECONDS, YANDEX_REQUEST_TIMEOUT_SECONDS
from .state import players


T = TypeVar("T")
logger = logging.getLogger("musicbot")
_health_task: asyncio.Task | None = None


async def run_blocking(
    label: str,
    func: Callable[..., T],
    *args,
    timeout: int = YANDEX_REQUEST_TIMEOUT_SECONDS,
) -> T:
    started = time.monotonic()
    try:
        result = await asyncio.wait_for(asyncio.to_thread(func, *args), timeout=timeout)
    except asyncio.TimeoutError as exc:
        logger.error("%s timed out after %s seconds", label, timeout)
        raise RuntimeError(f"{label} не ответил за {timeout} секунд. Попробуй ещё раз.") from exc
    except Exception:
        logger.exception("%s failed", label)
        raise

    elapsed = time.monotonic() - started
    if elapsed > 10:
        logger.warning("%s completed slowly in %.1f seconds", label, elapsed)
    return result


def start_health_logger(bot: commands.Bot) -> None:
    global _health_task
    if BOT_HEALTH_LOG_SECONDS <= 0:
        return

    if _health_task and not _health_task.done():
        return

    async def health_loop() -> None:
        await bot.wait_until_ready()
        while not bot.is_closed():
            details: list[str] = []
            for guild in bot.guilds:
                player = players.get(guild.id)
                voice = guild.voice_client
                if player is None and voice is None:
                    continue

                current = player.current.label if player and player.current else "-"
                queue_len = len(player.queue) if player else 0
                wave = "on" if player and player.wave_enabled else "off"
                voice_state = "none"
                if voice:
                    if voice.is_playing():
                        voice_state = "playing"
                    elif voice.is_paused():
                        voice_state = "paused"
                    elif voice.is_connected():
                        voice_state = "connected"

                details.append(
                    f"{guild.name}: voice={voice_state}, current={current}, queue={queue_len}, wave={wave}"
                )

            logger.info("health: %s", " | ".join(details) if details else "idle")
            await asyncio.sleep(BOT_HEALTH_LOG_SECONDS)

    _health_task = asyncio.create_task(health_loop())
