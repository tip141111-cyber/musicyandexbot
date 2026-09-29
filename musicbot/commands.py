import asyncio
from textwrap import dedent

import discord
from discord import app_commands
from discord.ext import commands

from .access import ensure_interaction_allowed, is_allowed, is_user_allowed, restricted
from .actions import enqueue_playlist, enqueue_playlist_interaction, enqueue_track, enqueue_track_interaction, play_search_selection, play_search_selection_interaction
from .config import COMMAND_PREFIX, HELP_TEXT
from .player import cancel_alone_disconnect, cancel_idle_disconnect, disable_wave, ensure_interaction_voice, ensure_voice, format_queue, jump_to_queue_track, request_skip, schedule_alone_disconnect, schedule_idle_disconnect
from .state import get_player, last_searches
from .ui import PlayerControls
from .yandex_service import search_yandex_artist_items, search_yandex_track_items


def register_commands(bot: commands.Bot) -> None:
    slash_commands_synced = False

    @bot.event
    async def on_ready() -> None:
        nonlocal slash_commands_synced
        if not slash_commands_synced:
            for guild in bot.guilds:
                await sync_guild_commands(guild)
            slash_commands_synced = True
        print(f"Logged in as {bot.user}")


    async def sync_guild_commands(guild: discord.Guild) -> None:
        bot.tree.copy_global_to(guild=guild)
        synced = await bot.tree.sync(guild=guild)
        print(f"Synced {len(synced)} slash commands to {guild.name}")


    @bot.event
    async def on_guild_join(guild: discord.Guild) -> None:
        await sync_guild_commands(guild)


    @bot.event
    async def on_voice_state_update(
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        if member.guild.voice_client is None:
            return

        player = get_player(member.guild.id)
        if member.guild.voice_client.channel in {before.channel, after.channel}:
            if member.guild.voice_client.channel and any(
                not voice_member.bot for voice_member in member.guild.voice_client.channel.members
            ):
                cancel_alone_disconnect(player)
            else:
                schedule_alone_disconnect(member.guild)


    @bot.event
    async def on_message(message: discord.Message) -> None:
        if message.author.bot or message.guild is None:
            return

        content = message.content.strip()
        if content == COMMAND_PREFIX:
            if is_user_allowed(message.author):
                await message.channel.send(HELP_TEXT)
            return

        if content.isdigit():
            ctx = await bot.get_context(message)
            if is_allowed(ctx):
                await play_search_selection(ctx, int(content))
                return

        await bot.process_commands(message)


    @bot.command(name="join", aliases=["зайди", "подключись"])
    @restricted()
    async def join_command(ctx: commands.Context) -> None:
        await ensure_voice(ctx)
        await ctx.reply("Я в голосовом канале.")


    @bot.command(name="play", aliases=["играть", "плей", "включи"])
    @restricted()
    async def play_command(ctx: commands.Context, *, query: str) -> None:
        await enqueue_track(ctx, query)


    @bot.command(name="wave_track", aliases=["волна_трек", "моя_волна"])
    @restricted()
    async def wave_track_command(ctx: commands.Context, *, query: str) -> None:
        await enqueue_track(ctx, query, start_wave=True)


    @bot.command(name="search", aliases=["поиск", "найди", "поиск_трек", "трек"])
    @restricted()
    async def search_command(ctx: commands.Context, *, query: str) -> None:
        await ctx.typing()

        try:
            items = await asyncio.to_thread(search_yandex_track_items, query)
        except Exception as exc:
            await ctx.reply(f"Не получилось найти треки: {exc}")
            return

        last_searches[(ctx.guild.id, ctx.author.id)] = items
        lines = [f"{index}. {item.label}" for index, item in enumerate(items, start=1)]
        await ctx.reply(
            "Нашел треки в Яндекс Музыке:\n"
            + "\n".join(lines)
            + "\n\nНапиши номер, например `3`, чтобы включить трек и продолжить волной."
        )


    @bot.command(name="artist", aliases=["исполнитель", "поиск_исполнитель", "артист"])
    @restricted()
    async def artist_search_command(ctx: commands.Context, *, query: str) -> None:
        await ctx.typing()

        try:
            items = await asyncio.to_thread(search_yandex_artist_items, query)
        except Exception as exc:
            await ctx.reply(f"Не получилось найти исполнителей: {exc}")
            return

        last_searches[(ctx.guild.id, ctx.author.id)] = items
        lines = [f"{index}. {item.label}" for index, item in enumerate(items, start=1)]
        await ctx.reply(
            "Нашел исполнителей в Яндекс Музыке:\n"
            + "\n".join(lines)
            + "\n\nНапиши номер, например `1`, чтобы добавить треки этого исполнителя в очередь."
        )


    @bot.command(name="playlist", aliases=["плейлист", "добавь_плейлист"])
    @restricted()
    async def playlist_command(ctx: commands.Context, *, url: str) -> None:
        await enqueue_playlist(ctx, url)


    @bot.command(name="select", aliases=["выбрать", "номер"])
    @restricted()
    async def select_command(ctx: commands.Context, number: int) -> None:
        await play_search_selection(ctx, number)


    @bot.command(name="pause", aliases=["пауза", "паузы"])
    @restricted()
    async def pause_command(ctx: commands.Context) -> None:
        if ctx.voice_client and ctx.voice_client.is_playing():
            ctx.voice_client.pause()
            await ctx.reply("Пауза.")
        else:
            await ctx.reply("Сейчас ничего не играет.")


    @bot.command(name="resume", aliases=["продолжить", "дальше"])
    @restricted()
    async def resume_command(ctx: commands.Context) -> None:
        if ctx.voice_client and ctx.voice_client.is_paused():
            ctx.voice_client.resume()
            await ctx.reply("Продолжаю.")
        else:
            await ctx.reply("Сейчас нет паузы.")


    @bot.command(name="skip", aliases=["скип", "пропусти"])
    @restricted()
    async def skip_command(ctx: commands.Context) -> None:
        if ctx.voice_client and (ctx.voice_client.is_playing() or ctx.voice_client.is_paused()):
            request_skip(get_player(ctx.guild.id))
            ctx.voice_client.stop()
            await ctx.reply("Пропускаю.")
        else:
            await ctx.reply("Сейчас нечего пропускать.")


    @bot.command(name="stop", aliases=["стоп", "останови"])
    @restricted()
    async def stop_command(ctx: commands.Context) -> None:
        player = get_player(ctx.guild.id)
        player.queue.clear()
        player.current = None
        disable_wave(player)
        cancel_alone_disconnect(player)

        if ctx.voice_client:
            ctx.voice_client.stop()
            schedule_idle_disconnect(ctx.guild)

        await ctx.reply("Остановил и очистил очередь.")


    @bot.command(name="leave", aliases=["выйди", "отключись"])
    @restricted()
    async def leave_command(ctx: commands.Context) -> None:
        player = get_player(ctx.guild.id)
        player.queue.clear()
        player.current = None
        disable_wave(player)
        cancel_alone_disconnect(player)
        cancel_idle_disconnect(player)

        if ctx.voice_client:
            await ctx.voice_client.disconnect()
            await ctx.reply("Вышел из голосового канала.")
        else:
            await ctx.reply("Я не в голосовом канале.")


    @bot.command(name="queue", aliases=["очередь"])
    @restricted()
    async def queue_command(ctx: commands.Context) -> None:
        player = get_player(ctx.guild.id)
        await ctx.reply(format_queue(player))


    @bot.command(name="queue_play", aliases=["выбрать_очередь", "играть_из_очереди"])
    @restricted()
    async def queue_play_command(ctx: commands.Context, number: int) -> None:
        try:
            track = await jump_to_queue_track(ctx.guild, number)
        except Exception as exc:
            await ctx.reply(str(exc))
            return

        await ctx.reply(f"Включаю из очереди: {track.label}", view=PlayerControls())


    @bot.command(name="shuffle", aliases=["random", "рандом", "случайно"])
    @restricted()
    async def shuffle_command(ctx: commands.Context, mode: str | None = None) -> None:
        player = get_player(ctx.guild.id)
        if mode is None:
            state = "включен" if player.shuffle_enabled else "выключен"
            await ctx.reply(f"Случайный порядок сейчас {state}.")
            return

        normalized = mode.lower()
        if normalized in {"on", "true", "1", "yes", "да", "вкл", "включить"}:
            player.shuffle_enabled = True
            await ctx.reply("Случайный порядок включен.")
            return

        if normalized in {"off", "false", "0", "no", "нет", "выкл", "выключить"}:
            player.shuffle_enabled = False
            await ctx.reply("Воспроизведение по порядку включено.")
            return

        await ctx.reply("Используй `!shuffle on` или `!shuffle off`.")


    @bot.command(name="np", aliases=["сейчас", "играет"])
    @restricted()
    async def now_playing_command(ctx: commands.Context) -> None:
        current = get_player(ctx.guild.id).current
        if current is None:
            await ctx.reply("Сейчас ничего не играет.")
            return

        await ctx.reply(f"Сейчас играет: {current.label}")


    @bot.command(name="help", aliases=["помощь", "команды"])
    @restricted()
    async def help_command(ctx: commands.Context) -> None:
        await ctx.reply(HELP_TEXT)


    @bot.tree.command(name="join", description="Подключить бота к твоему голосовому каналу")
    async def slash_join(interaction: discord.Interaction) -> None:
        if not await ensure_interaction_allowed(interaction):
            return

        await interaction.response.defer(ephemeral=True)
        try:
            await ensure_interaction_voice(interaction)
        except Exception as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return

        await interaction.followup.send("Я в голосовом канале.", ephemeral=True)


    @bot.tree.command(name="play", description="Найти трек в Яндекс Музыке и включить его")
    @app_commands.describe(query="Название трека или исполнитель")
    async def slash_play(interaction: discord.Interaction, query: str) -> None:
        if not await ensure_interaction_allowed(interaction):
            return

        await interaction.response.defer()
        await enqueue_track_interaction(interaction, query)


    @bot.tree.command(name="wave_track", description="Включить трек и продолжить похожей волной")
    @app_commands.describe(query="Название трека или исполнитель")
    async def slash_wave_track(interaction: discord.Interaction, query: str) -> None:
        if not await ensure_interaction_allowed(interaction):
            return

        await interaction.response.defer()
        await enqueue_track_interaction(interaction, query, start_wave=True)


    @bot.tree.command(name="playlist", description="Добавить треки из плейлиста Яндекс Музыки")
    @app_commands.describe(url="Ссылка на плейлист Яндекс Музыки")
    async def slash_playlist(interaction: discord.Interaction, url: str) -> None:
        if not await ensure_interaction_allowed(interaction):
            return

        await interaction.response.defer()
        await enqueue_playlist_interaction(interaction, url)


    @bot.tree.command(name="search_track", description="Найти 10 треков в Яндекс Музыке")
    @app_commands.describe(query="Название трека или исполнитель")
    async def slash_search_track(interaction: discord.Interaction, query: str) -> None:
        if not await ensure_interaction_allowed(interaction):
            return

        await interaction.response.defer()

        try:
            items = await asyncio.to_thread(search_yandex_track_items, query)
        except Exception as exc:
            await interaction.followup.send(f"Не получилось найти треки: {exc}", ephemeral=True)
            return

        if interaction.guild is None:
            await interaction.followup.send("Команда работает только на сервере.", ephemeral=True)
            return

        last_searches[(interaction.guild.id, interaction.user.id)] = items
        lines = [f"{index}. {item.label}" for index, item in enumerate(items, start=1)]
        await interaction.followup.send(
            "Нашел треки в Яндекс Музыке:\n"
            + "\n".join(lines)
            + "\n\nИспользуй `/select number`, чтобы включить трек и продолжить волной."
        )


    @bot.tree.command(name="search_artist", description="Найти 10 исполнителей в Яндекс Музыке")
    @app_commands.describe(query="Имя исполнителя")
    async def slash_search_artist(interaction: discord.Interaction, query: str) -> None:
        if not await ensure_interaction_allowed(interaction):
            return

        await interaction.response.defer()

        try:
            items = await asyncio.to_thread(search_yandex_artist_items, query)
        except Exception as exc:
            await interaction.followup.send(f"Не получилось найти исполнителей: {exc}", ephemeral=True)
            return

        if interaction.guild is None:
            await interaction.followup.send("Команда работает только на сервере.", ephemeral=True)
            return

        last_searches[(interaction.guild.id, interaction.user.id)] = items
        lines = [f"{index}. {item.label}" for index, item in enumerate(items, start=1)]
        await interaction.followup.send(
            "Нашел исполнителей в Яндекс Музыке:\n"
            + "\n".join(lines)
            + "\n\nИспользуй `/select number`, чтобы добавить треки выбранного исполнителя в очередь."
        )


    @bot.tree.command(name="select", description="Включить трек из последнего поиска по номеру")
    @app_commands.describe(number="Номер трека из последнего поиска")
    async def slash_select(interaction: discord.Interaction, number: app_commands.Range[int, 1, 10]) -> None:
        if not await ensure_interaction_allowed(interaction):
            return

        await interaction.response.defer()
        await play_search_selection_interaction(interaction, number)


    @bot.tree.command(name="pause", description="Поставить музыку на паузу")
    async def slash_pause(interaction: discord.Interaction) -> None:
        if not await ensure_interaction_allowed(interaction):
            return

        voice = interaction.guild.voice_client if interaction.guild else None
        if voice and voice.is_playing():
            voice.pause()
            await interaction.response.send_message("Пауза.", ephemeral=True)
        else:
            await interaction.response.send_message("Сейчас ничего не играет.", ephemeral=True)


    @bot.tree.command(name="resume", description="Продолжить воспроизведение")
    async def slash_resume(interaction: discord.Interaction) -> None:
        if not await ensure_interaction_allowed(interaction):
            return

        voice = interaction.guild.voice_client if interaction.guild else None
        if voice and voice.is_paused():
            voice.resume()
            await interaction.response.send_message("Продолжаю.", ephemeral=True)
        else:
            await interaction.response.send_message("Сейчас нет паузы.", ephemeral=True)


    @bot.tree.command(name="skip", description="Пропустить текущий трек")
    async def slash_skip(interaction: discord.Interaction) -> None:
        if not await ensure_interaction_allowed(interaction):
            return

        voice = interaction.guild.voice_client if interaction.guild else None
        if voice and (voice.is_playing() or voice.is_paused()):
            if interaction.guild:
                request_skip(get_player(interaction.guild.id))
            voice.stop()
            await interaction.response.send_message("Пропускаю.", ephemeral=True)
        else:
            await interaction.response.send_message("Сейчас нечего пропускать.", ephemeral=True)


    @bot.tree.command(name="stop", description="Остановить музыку и очистить очередь")
    async def slash_stop(interaction: discord.Interaction) -> None:
        if not await ensure_interaction_allowed(interaction):
            return

        if interaction.guild is None:
            await interaction.response.send_message("Команда работает только на сервере.", ephemeral=True)
            return

        player = get_player(interaction.guild.id)
        player.queue.clear()
        player.current = None
        disable_wave(player)
        cancel_alone_disconnect(player)

        if interaction.guild.voice_client:
            interaction.guild.voice_client.stop()
            schedule_idle_disconnect(interaction.guild)

        await interaction.response.send_message("Остановил и очистил очередь.", ephemeral=True)


    @bot.tree.command(name="queue", description="Показать очередь треков")
    async def slash_queue(interaction: discord.Interaction) -> None:
        if not await ensure_interaction_allowed(interaction):
            return

        if interaction.guild is None:
            await interaction.response.send_message("Команда работает только на сервере.", ephemeral=True)
            return

        player = get_player(interaction.guild.id)
        await interaction.response.send_message(format_queue(player), ephemeral=True)


    @bot.tree.command(name="queue_play", description="Включить трек из очереди по номеру")
    @app_commands.describe(number="Номер трека из /queue")
    async def slash_queue_play(interaction: discord.Interaction, number: app_commands.Range[int, 1, 100]) -> None:
        if not await ensure_interaction_allowed(interaction):
            return

        if interaction.guild is None:
            await interaction.response.send_message("Команда работает только на сервере.", ephemeral=True)
            return

        await interaction.response.defer()

        try:
            track = await jump_to_queue_track(interaction.guild, number)
        except Exception as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return

        await interaction.followup.send(f"Включаю из очереди: {track.label}", view=PlayerControls())


    @bot.tree.command(name="shuffle", description="Включить или выключить случайный порядок очереди")
    @app_commands.describe(enabled="true - случайно, false - по порядку")
    async def slash_shuffle(interaction: discord.Interaction, enabled: bool) -> None:
        if not await ensure_interaction_allowed(interaction):
            return

        if interaction.guild is None:
            await interaction.response.send_message("Команда работает только на сервере.", ephemeral=True)
            return

        player = get_player(interaction.guild.id)
        player.shuffle_enabled = enabled
        if enabled:
            await interaction.response.send_message("Случайный порядок включен.", ephemeral=True)
        else:
            await interaction.response.send_message("Воспроизведение по порядку включено.", ephemeral=True)


    @bot.tree.command(name="now", description="Показать, что сейчас играет")
    async def slash_now(interaction: discord.Interaction) -> None:
        if not await ensure_interaction_allowed(interaction):
            return

        if interaction.guild is None:
            await interaction.response.send_message("Команда работает только на сервере.", ephemeral=True)
            return

        current = get_player(interaction.guild.id).current
        if current is None:
            await interaction.response.send_message("Сейчас ничего не играет.", ephemeral=True)
            return

        await interaction.response.send_message(f"Сейчас играет: {current.label}", ephemeral=True)


    @bot.tree.command(name="leave", description="Отключить бота от голосового канала")
    async def slash_leave(interaction: discord.Interaction) -> None:
        if not await ensure_interaction_allowed(interaction):
            return

        if interaction.guild is None:
            await interaction.response.send_message("Команда работает только на сервере.", ephemeral=True)
            return

        player = get_player(interaction.guild.id)
        player.queue.clear()
        player.current = None
        disable_wave(player)
        cancel_alone_disconnect(player)
        cancel_idle_disconnect(player)

        if interaction.guild.voice_client:
            await interaction.guild.voice_client.disconnect()
            await interaction.response.send_message("Вышел из голосового канала.", ephemeral=True)
        else:
            await interaction.response.send_message("Я не в голосовом канале.", ephemeral=True)


    @bot.tree.command(name="commands", description="Показать список команд")
    async def slash_commands(interaction: discord.Interaction) -> None:
        if not await ensure_interaction_allowed(interaction):
            return

        slash_help = dedent("""Slash-команды:
    `/join` - подключиться к голосовому каналу
    `/play query` - найти и включить трек
    `/wave_track query` - включить трек и продолжить похожей волной
    `/playlist url` - добавить треки из плейлиста Яндекс Музыки
    `/search_track query` - найти 10 треков
    `/search_artist query` - найти 10 исполнителей
    `/select number` - включить трек из последнего поиска
    `/pause` - пауза
    `/resume` - продолжить
    `/skip` - следующий трек
    `/stop` - остановить и очистить очередь
    `/queue` - показать очередь
    `/queue_play number` - включить трек из очереди
    `/shuffle enabled` - включить или выключить случайный порядок
    `/now` - что играет сейчас
    `/leave` - отключиться""").strip()
        await interaction.response.send_message(slash_help, ephemeral=True)


    @bot.event
    async def on_command_error(ctx: commands.Context, error: commands.CommandError) -> None:
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.reply("Не хватает аргумента команды.")
            return

        if isinstance(error, commands.CommandNotFound):
            return

        await ctx.reply(f"Ошибка: {error}")
