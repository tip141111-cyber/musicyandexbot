import asyncio

import discord
from discord.ext import commands

from .models import SearchItem
from .player import drop_pending_wave_tracks, ensure_interaction_voice, ensure_voice, play_next
from .state import get_player, last_searches
from .ui import PlayerControls
from .yandex_service import build_wave_station, find_artist_tracks, find_playlist_tracks, find_wave_seed_track, find_yandex_track, find_yandex_track_by_id


async def enqueue_track(ctx: commands.Context, query: str, start_wave: bool = False) -> None:
    voice = await ensure_voice(ctx)
    await ctx.typing()

    try:
        finder = find_wave_seed_track if start_wave else find_yandex_track
        track = await asyncio.to_thread(finder, query, str(ctx.author))
    except Exception as exc:
        await ctx.reply(f"Не получилось найти или открыть трек: {exc}")
        return

    player = get_player(ctx.guild.id)
    player.text_channel_id = ctx.channel.id
    drop_pending_wave_tracks(player)
    player.queue.append(track)

    if not voice.is_playing() and not voice.is_paused():
        await play_next(ctx.guild)
        message = f"Играет: {track.label}"
    else:
        message = f"Добавил в очередь: {track.label}"

    if start_wave:
        message += "\nВолна включится после этого трека и ручной очереди."

    await ctx.reply(message, view=PlayerControls())


async def enqueue_search_track(ctx: commands.Context, item: SearchItem) -> None:
    voice = await ensure_voice(ctx)
    await ctx.typing()

    try:
        if item.track_id is not None:
            track = await asyncio.to_thread(find_yandex_track_by_id, item.track_id, str(ctx.author))
            track.starts_wave = True
            track.wave_station = build_wave_station(track)
        else:
            track = await asyncio.to_thread(find_wave_seed_track, item.query, str(ctx.author))
    except Exception as exc:
        await ctx.reply(f"Не получилось открыть выбранный трек: {exc}")
        return

    player = get_player(ctx.guild.id)
    player.text_channel_id = ctx.channel.id
    drop_pending_wave_tracks(player)
    player.queue.append(track)

    if not voice.is_playing() and not voice.is_paused():
        await play_next(ctx.guild)
        message = f"Играет: {track.label}"
    else:
        message = f"Добавил в очередь: {track.label}"

    await ctx.reply(message + "\nВолна включится после этого трека и ручной очереди.", view=PlayerControls())


async def enqueue_artist(ctx: commands.Context, item: SearchItem) -> None:
    if item.artist_id is None:
        await ctx.reply("У выбранного исполнителя нет ID для поиска треков.")
        return

    voice = await ensure_voice(ctx)
    await ctx.typing()

    try:
        tracks = await asyncio.to_thread(find_artist_tracks, item.artist_id, str(ctx.author))
    except Exception as exc:
        await ctx.reply(f"Не получилось открыть треки исполнителя: {exc}")
        return

    player = get_player(ctx.guild.id)
    player.text_channel_id = ctx.channel.id
    drop_pending_wave_tracks(player)
    was_idle = not voice.is_playing() and not voice.is_paused()
    player.queue.extend(tracks)

    if was_idle:
        await play_next(ctx.guild)

    await ctx.reply(
        f"Добавил треки исполнителя {item.label}: {len(tracks)} шт.",
        view=PlayerControls(),
    )


async def enqueue_playlist(ctx: commands.Context, playlist_reference: str) -> None:
    voice = await ensure_voice(ctx)
    await ctx.typing()

    try:
        tracks = await asyncio.to_thread(find_playlist_tracks, playlist_reference, str(ctx.author))
    except Exception as exc:
        await ctx.reply(f"Не получилось открыть плейлист: {exc}")
        return

    player = get_player(ctx.guild.id)
    player.text_channel_id = ctx.channel.id
    drop_pending_wave_tracks(player)
    was_idle = not voice.is_playing() and not voice.is_paused()
    player.queue.extend(tracks)

    if was_idle:
        await play_next(ctx.guild)

    await ctx.reply(
        f"Добавил треки из плейлиста: {len(tracks)} шт.",
        view=PlayerControls(),
    )


async def enqueue_track_interaction(interaction: discord.Interaction, query: str, start_wave: bool = False) -> None:
    if interaction.guild is None:
        await interaction.followup.send("Команда работает только на сервере.", ephemeral=True)
        return

    try:
        voice = await ensure_interaction_voice(interaction)
    except Exception as exc:
        await interaction.followup.send(str(exc), ephemeral=True)
        return

    try:
        finder = find_wave_seed_track if start_wave else find_yandex_track
        track = await asyncio.to_thread(finder, query, str(interaction.user))
    except Exception as exc:
        await interaction.followup.send(f"Не получилось найти или открыть трек: {exc}", ephemeral=True)
        return

    player = get_player(interaction.guild.id)
    player.text_channel_id = interaction.channel_id
    drop_pending_wave_tracks(player)
    player.queue.append(track)

    if not voice.is_playing() and not voice.is_paused():
        await play_next(interaction.guild)
        message = f"Играет: {track.label}"
    else:
        message = f"Добавил в очередь: {track.label}"

    if start_wave:
        message += "\nВолна включится после этого трека и ручной очереди."

    await interaction.followup.send(message, view=PlayerControls())


async def enqueue_search_track_interaction(interaction: discord.Interaction, item: SearchItem) -> None:
    if interaction.guild is None:
        await interaction.followup.send("Команда работает только на сервере.", ephemeral=True)
        return

    try:
        voice = await ensure_interaction_voice(interaction)
    except Exception as exc:
        await interaction.followup.send(str(exc), ephemeral=True)
        return

    try:
        if item.track_id is not None:
            track = await asyncio.to_thread(find_yandex_track_by_id, item.track_id, str(interaction.user))
            track.starts_wave = True
            track.wave_station = build_wave_station(track)
        else:
            track = await asyncio.to_thread(find_wave_seed_track, item.query, str(interaction.user))
    except Exception as exc:
        await interaction.followup.send(f"Не получилось открыть выбранный трек: {exc}", ephemeral=True)
        return

    player = get_player(interaction.guild.id)
    player.text_channel_id = interaction.channel_id
    drop_pending_wave_tracks(player)
    player.queue.append(track)

    if not voice.is_playing() and not voice.is_paused():
        await play_next(interaction.guild)
        message = f"Играет: {track.label}"
    else:
        message = f"Добавил в очередь: {track.label}"

    await interaction.followup.send(
        message + "\nВолна включится после этого трека и ручной очереди.",
        view=PlayerControls(),
    )


async def enqueue_artist_interaction(interaction: discord.Interaction, item: SearchItem) -> None:
    if interaction.guild is None:
        await interaction.followup.send("Команда работает только на сервере.", ephemeral=True)
        return

    if item.artist_id is None:
        await interaction.followup.send("У выбранного исполнителя нет ID для поиска треков.", ephemeral=True)
        return

    try:
        voice = await ensure_interaction_voice(interaction)
    except Exception as exc:
        await interaction.followup.send(str(exc), ephemeral=True)
        return

    try:
        tracks = await asyncio.to_thread(find_artist_tracks, item.artist_id, str(interaction.user))
    except Exception as exc:
        await interaction.followup.send(f"Не получилось открыть треки исполнителя: {exc}", ephemeral=True)
        return

    player = get_player(interaction.guild.id)
    player.text_channel_id = interaction.channel_id
    drop_pending_wave_tracks(player)
    was_idle = not voice.is_playing() and not voice.is_paused()
    player.queue.extend(tracks)

    if was_idle:
        await play_next(interaction.guild)

    await interaction.followup.send(
        f"Добавил треки исполнителя {item.label}: {len(tracks)} шт.",
        view=PlayerControls(),
    )


async def enqueue_playlist_interaction(interaction: discord.Interaction, playlist_reference: str) -> None:
    if interaction.guild is None:
        await interaction.followup.send("Команда работает только на сервере.", ephemeral=True)
        return

    try:
        voice = await ensure_interaction_voice(interaction)
    except Exception as exc:
        await interaction.followup.send(str(exc), ephemeral=True)
        return

    try:
        tracks = await asyncio.to_thread(find_playlist_tracks, playlist_reference, str(interaction.user))
    except Exception as exc:
        await interaction.followup.send(f"Не получилось открыть плейлист: {exc}", ephemeral=True)
        return

    player = get_player(interaction.guild.id)
    player.text_channel_id = interaction.channel_id
    drop_pending_wave_tracks(player)
    was_idle = not voice.is_playing() and not voice.is_paused()
    player.queue.extend(tracks)

    if was_idle:
        await play_next(interaction.guild)

    await interaction.followup.send(
        f"Добавил треки из плейлиста: {len(tracks)} шт.",
        view=PlayerControls(),
    )


async def play_search_selection(ctx: commands.Context, selection: int) -> None:
    items = last_searches.get((ctx.guild.id, ctx.author.id))
    if not items:
        await ctx.reply("Сначала сделай поиск: `!поиск название трека`.")
        return

    if selection < 1 or selection > len(items):
        await ctx.reply(f"Выбери номер от 1 до {len(items)}.")
        return

    item = items[selection - 1]
    if item.kind == "artist":
        await enqueue_artist(ctx, item)
    else:
        await enqueue_search_track(ctx, item)


async def play_search_selection_interaction(interaction: discord.Interaction, selection: int) -> None:
    if interaction.guild is None:
        await interaction.followup.send("Команда работает только на сервере.", ephemeral=True)
        return

    items = last_searches.get((interaction.guild.id, interaction.user.id))
    if not items:
        await interaction.followup.send("Сначала сделай поиск: `/search_track`.", ephemeral=True)
        return

    if selection < 1 or selection > len(items):
        await interaction.followup.send(f"Выбери номер от 1 до {len(items)}.", ephemeral=True)
        return

    item = items[selection - 1]
    if item.kind == "artist":
        await enqueue_artist_interaction(interaction, item)
    else:
        await enqueue_search_track_interaction(interaction, item)
