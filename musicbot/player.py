import asyncio
import concurrent.futures
import logging
import sys

import discord
from discord.ext import commands

from .config import (
    FFMPEG_AUDIO_FILTER,
    FFMPEG_EXECUTABLE,
    FFMPEG_VOLUME,
    IDLE_DISCONNECT_SECONDS,
    PLAYBACK_STALL_GRACE_SECONDS,
)
from .diagnostics import run_blocking
from .models import GuildPlayer, TrackRequest
from .state import get_player, pop_next_track
from .yandex_service import get_wave_tracks, resolve_track_stream_url


_bot: commands.Bot | None = None
logger = logging.getLogger("musicbot.player")


def configure_player(bot: commands.Bot) -> None:
    global _bot
    _bot = bot


def build_ffmpeg_options() -> str:
    filters = []
    if FFMPEG_AUDIO_FILTER:
        filters.append(FFMPEG_AUDIO_FILTER)

    if FFMPEG_VOLUME and FFMPEG_VOLUME != "1.0":
        filters.append(f"volume={FFMPEG_VOLUME}")

    options = ["-vn", "-loglevel", "warning", "-ar", "48000", "-ac", "2"]
    if filters:
        options.extend(["-af", ",".join(filters)])

    return " ".join(options)

def cancel_idle_disconnect(player: GuildPlayer) -> None:
    if player.idle_disconnect_task and not player.idle_disconnect_task.done():
        player.idle_disconnect_task.cancel()
    player.idle_disconnect_task = None


def cancel_alone_disconnect(player: GuildPlayer) -> None:
    if player.alone_disconnect_task and not player.alone_disconnect_task.done():
        player.alone_disconnect_task.cancel()
    player.alone_disconnect_task = None


def cancel_playback_watchdog(player: GuildPlayer) -> None:
    if player.playback_watchdog_task and not player.playback_watchdog_task.done():
        player.playback_watchdog_task.cancel()
    player.playback_watchdog_task = None


def disable_wave(player: GuildPlayer) -> None:
    player.wave_enabled = False
    player.wave_queue_id = None
    player.wave_recent_track_ids.clear()
    player.wave_seed_label = None
    player.wave_requested_by = None


def schedule_playback_watchdog(guild: discord.Guild, track: TrackRequest) -> None:
    if track.duration_seconds is None or PLAYBACK_STALL_GRACE_SECONDS <= 0:
        return

    player = get_player(guild.id)
    cancel_playback_watchdog(player)
    timeout = track.duration_seconds + PLAYBACK_STALL_GRACE_SECONDS

    async def stop_if_stalled() -> None:
        try:
            await asyncio.sleep(timeout)
            voice = guild.voice_client
            if (
                voice is not None
                and voice.is_connected()
                and voice.is_playing()
                and player.current is track
            ):
                logger.warning(
                    "Playback watchdog stopped stalled track after %.1f seconds: %s",
                    timeout,
                    track.label,
                )
                voice.stop()
        except asyncio.CancelledError:
            pass
        finally:
            if player.playback_watchdog_task is asyncio.current_task():
                player.playback_watchdog_task = None

    player.playback_watchdog_task = asyncio.create_task(stop_if_stalled())


def request_skip(player: GuildPlayer) -> None:
    player.skip_requested = True


def drop_pending_wave_tracks(player: GuildPlayer) -> None:
    player.queue = type(player.queue)(track for track in player.queue if not track.is_wave_track)


def activate_wave_from_track(player: GuildPlayer, track: TrackRequest) -> None:
    if track.wave_track_id is None:
        return

    player.wave_enabled = True
    player.wave_queue_id = track.wave_track_id
    player.wave_seed_label = track.label
    player.wave_requested_by = track.requested_by
    player.wave_recent_track_ids.add(str(track.wave_track_id))


def send_finished_feedback(player: GuildPlayer, track: TrackRequest | None) -> None:
    if track is None or track.wave_track_id is None:
        player.skip_requested = False
        return

    if player.wave_enabled:
        player.wave_queue_id = track.wave_track_id
        player.wave_recent_track_ids.add(str(track.wave_track_id))
        if len(player.wave_recent_track_ids) > 200:
            player.wave_recent_track_ids = set(list(player.wave_recent_track_ids)[-100:])

    player.skip_requested = False


def fetch_more_wave_tracks(player: GuildPlayer) -> list[TrackRequest]:
    if not player.wave_enabled or player.wave_queue_id is None:
        return []

    requested_by = player.wave_requested_by or "Yandex Wave"
    return get_wave_tracks(
        player.wave_queue_id,
        requested_by=requested_by,
        exclude_track_ids=player.wave_recent_track_ids,
    )


def is_bot_alone_in_voice(guild: discord.Guild) -> bool:
    voice = guild.voice_client
    if voice is None or not voice.channel:
        return False

    human_members = [member for member in voice.channel.members if not member.bot]
    return len(human_members) == 0


def schedule_alone_disconnect(guild: discord.Guild) -> None:
    if IDLE_DISCONNECT_SECONDS <= 0:
        return

    player = get_player(guild.id)
    if not is_bot_alone_in_voice(guild):
        cancel_alone_disconnect(player)
        return

    if player.alone_disconnect_task and not player.alone_disconnect_task.done():
        return

    async def disconnect_if_still_alone() -> None:
        try:
            await asyncio.sleep(IDLE_DISCONNECT_SECONDS)
            async with player.lock:
                voice = guild.voice_client
                if voice is None or not voice.is_connected() or not is_bot_alone_in_voice(guild):
                    return

                player.queue.clear()
                player.current = None
                disable_wave(player)
                cancel_idle_disconnect(player)
                await voice.disconnect()

                channel = guild.get_channel(player.text_channel_id) if player.text_channel_id else None
                if channel:
                    minutes = IDLE_DISCONNECT_SECONDS // 60
                    await channel.send(f"Вышел из голосового канала: {minutes} минут в комнате никого не было.")
        except asyncio.CancelledError:
            pass
        finally:
            if player.alone_disconnect_task is asyncio.current_task():
                player.alone_disconnect_task = None

    player.alone_disconnect_task = asyncio.create_task(disconnect_if_still_alone())


def schedule_idle_disconnect(guild: discord.Guild) -> None:
    if IDLE_DISCONNECT_SECONDS <= 0:
        return

    player = get_player(guild.id)
    if player.idle_disconnect_task and not player.idle_disconnect_task.done():
        return

    async def disconnect_when_idle() -> None:
        try:
            await asyncio.sleep(IDLE_DISCONNECT_SECONDS)
            async with player.lock:
                voice = guild.voice_client
                is_busy = (
                    player.current is not None
                    or bool(player.queue)
                    or (voice is not None and (voice.is_playing() or voice.is_paused()))
                )
                if voice is None or not voice.is_connected() or is_busy:
                    return

                await voice.disconnect()
                channel = guild.get_channel(player.text_channel_id) if player.text_channel_id else None
                if channel:
                    minutes = IDLE_DISCONNECT_SECONDS // 60
                    await channel.send(f"Вышел из голосового канала после {minutes} минут бездействия.")
        except asyncio.CancelledError:
            pass
        finally:
            if player.idle_disconnect_task is asyncio.current_task():
                player.idle_disconnect_task = None

    player.idle_disconnect_task = asyncio.create_task(disconnect_when_idle())

async def ensure_voice(ctx: commands.Context) -> discord.VoiceClient:
    if not ctx.author.voice or not ctx.author.voice.channel:
        raise RuntimeError("Сначала зайди в голосовой канал.")

    channel = ctx.author.voice.channel
    if ctx.voice_client:
        if ctx.voice_client.channel != channel:
            await ctx.voice_client.move_to(channel)
        schedule_idle_disconnect(ctx.guild)
        schedule_alone_disconnect(ctx.guild)
        return ctx.voice_client

    voice = await channel.connect()
    schedule_idle_disconnect(ctx.guild)
    schedule_alone_disconnect(ctx.guild)
    return voice


async def ensure_interaction_voice(interaction: discord.Interaction) -> discord.VoiceClient:
    if interaction.guild is None:
        raise RuntimeError("Команда работает только на сервере.")

    user_voice = getattr(interaction.user, "voice", None)
    if not user_voice or not user_voice.channel:
        raise RuntimeError("Сначала зайди в голосовой канал.")

    channel = user_voice.channel
    voice = interaction.guild.voice_client
    if voice:
        if voice.channel != channel:
            await voice.move_to(channel)
        schedule_idle_disconnect(interaction.guild)
        schedule_alone_disconnect(interaction.guild)
        return voice

    voice = await channel.connect()
    schedule_idle_disconnect(interaction.guild)
    schedule_alone_disconnect(interaction.guild)
    return voice


async def play_next(guild: discord.Guild) -> None:
    if _bot is None:
        raise RuntimeError("Player is not configured with a Discord bot instance.")

    player = get_player(guild.id)

    async with player.lock:
        cancel_playback_watchdog(player)
        finished_track = player.current
        send_finished_feedback(player, finished_track)

        voice = guild.voice_client
        if voice is None or not voice.is_connected():
            player.current = None
            cancel_idle_disconnect(player)
            cancel_alone_disconnect(player)
            cancel_playback_watchdog(player)
            return

        if not player.queue:
            try:
                tracks = await run_blocking("load wave tracks", fetch_more_wave_tracks, player)
                for track in tracks:
                    if track.wave_track_id is not None:
                        player.wave_recent_track_ids.add(str(track.wave_track_id))
                player.queue.extend(tracks)
            except Exception as exc:
                logger.exception("Wave load failed")
                player.current = None
                cancel_playback_watchdog(player)
                schedule_idle_disconnect(guild)
                return

            if not player.queue:
                player.current = None
                cancel_playback_watchdog(player)
                schedule_idle_disconnect(guild)
                return

        track = pop_next_track(player)
        player.current = track
        player.skip_requested = False
        cancel_idle_disconnect(player)
        schedule_alone_disconnect(guild)

        if track.starts_wave:
            activate_wave_from_track(player, track)

        if track.wave_track_id is not None:
            player.wave_queue_id = track.wave_track_id

        try:
            stream_url = await run_blocking("resolve stream url", resolve_track_stream_url, track)
            source = discord.FFmpegPCMAudio(
                stream_url,
                executable=FFMPEG_EXECUTABLE,
                before_options=(
                    "-reconnect 1 "
                    "-reconnect_streamed 1 "
                    "-reconnect_on_network_error 1 "
                    "-reconnect_on_http_error 5xx "
                    "-reconnect_delay_max 5 "
                    "-rw_timeout 15000000"
                ),
                options=build_ffmpeg_options(),
                stderr=sys.stderr,
            )
        except Exception as exc:
            logger.exception("Failed to prepare audio source for %s", track.label)
            player.current = None
            cancel_playback_watchdog(player)
            channel = guild.get_channel(player.text_channel_id) if player.text_channel_id else None
            if channel:
                await channel.send(f"Не получилось подготовить звук для трека: {track.label}")
            if player.queue:
                asyncio.create_task(play_next(guild))
            return

        def after_play(error: Exception | None) -> None:
            if error:
                logger.error("Playback error for %s: %s", track.label, error)
            future = asyncio.run_coroutine_threadsafe(play_next(guild), _bot.loop)
            try:
                future.result(timeout=60)
            except concurrent.futures.TimeoutError:
                logger.error("Queue advance timed out after playback for %s", track.label)
            except Exception as exc:
                logger.exception("Queue error")

        voice.play(source, after=after_play)
        schedule_playback_watchdog(guild, track)


async def jump_to_queue_track(guild: discord.Guild, number: int) -> TrackRequest:
    player = get_player(guild.id)
    if number < 1 or number > len(player.queue):
        raise RuntimeError(f"Выбери номер от 1 до {len(player.queue)}.")

    selected_index = number - 1
    selected_track = player.queue[selected_index]

    remaining_tracks = list(player.queue)
    player.queue.clear()
    player.queue.append(selected_track)
    player.queue.extend(remaining_tracks[:selected_index])
    player.queue.extend(remaining_tracks[selected_index + 1 :])

    voice = guild.voice_client
    if voice and (voice.is_playing() or voice.is_paused()):
        voice.stop()
    else:
        await play_next(guild)

    return selected_track


def format_queue(player: GuildPlayer, limit: int = 20) -> str:
    lines: list[str] = []
    mode = "случайный" if player.shuffle_enabled else "по порядку"
    lines.append(f"Режим: {mode}")
    if player.wave_enabled:
        seed = f" по треку {player.wave_seed_label}" if player.wave_seed_label else ""
        lines.append(f"Волна включена{seed}.")

    if player.current:
        lines.append(f"Сейчас играет: {player.current.label}")

    if not player.queue:
        lines.append("Очередь пустая.")
        return "\n".join(lines)

    lines.append("Очередь:")
    lines.extend(
        f"{index}. {track.label}"
        for index, track in enumerate(list(player.queue)[:limit], start=1)
    )

    if len(player.queue) > limit:
        lines.append(f"...ещё {len(player.queue) - limit}")

    lines.append("\nИспользуй `/queue_play number`, чтобы включить трек из очереди.")
    return "\n".join(lines)
