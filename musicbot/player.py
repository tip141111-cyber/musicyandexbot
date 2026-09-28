import asyncio
import sys

import discord
from discord.ext import commands

from .config import FFMPEG_AUDIO_FILTER, FFMPEG_EXECUTABLE, FFMPEG_VOLUME, IDLE_DISCONNECT_SECONDS
from .models import GuildPlayer, TrackRequest
from .state import get_player, pop_next_track
from .yandex_service import resolve_track_stream_url


_bot: commands.Bot | None = None


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
        return ctx.voice_client

    voice = await channel.connect()
    schedule_idle_disconnect(ctx.guild)
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
        return voice

    voice = await channel.connect()
    schedule_idle_disconnect(interaction.guild)
    return voice


async def play_next(guild: discord.Guild) -> None:
    if _bot is None:
        raise RuntimeError("Player is not configured with a Discord bot instance.")

    player = get_player(guild.id)

    async with player.lock:
        voice = guild.voice_client
        if voice is None or not voice.is_connected():
            player.current = None
            cancel_idle_disconnect(player)
            return

        if not player.queue:
            player.current = None
            schedule_idle_disconnect(guild)
            return

        track = pop_next_track(player)
        player.current = track
        cancel_idle_disconnect(player)

        try:
            stream_url = await asyncio.to_thread(resolve_track_stream_url, track)
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
            print(f"Failed to prepare audio source for {track.label}: {exc}")
            player.current = None
            channel = guild.get_channel(player.text_channel_id) if player.text_channel_id else None
            if channel:
                await channel.send(f"Не получилось подготовить звук для трека: {track.label}")
            if player.queue:
                asyncio.create_task(play_next(guild))
            return

        def after_play(error: Exception | None) -> None:
            if error:
                print(f"Playback error for {track.label}: {error}")
            future = asyncio.run_coroutine_threadsafe(play_next(guild), _bot.loop)
            try:
                future.result()
            except Exception as exc:
                print(f"Queue error: {exc}")

        voice.play(source, after=after_play)


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
