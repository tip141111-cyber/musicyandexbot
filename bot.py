import asyncio
import os
from collections import deque
from dataclasses import dataclass

import discord
import imageio_ffmpeg
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv
from yandex_music import Client


load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
YANDEX_MUSIC_TOKEN = os.getenv("YANDEX_MUSIC_TOKEN")
COMMAND_PREFIX = os.getenv("COMMAND_PREFIX", "!")


def parse_ids(raw_value: str | None) -> set[int]:
    if not raw_value:
        return set()

    ids: set[int] = set()
    for part in raw_value.split(","):
        part = part.strip()
        if part:
            ids.add(int(part))
    return ids


ALLOWED_USER_IDS = parse_ids(os.getenv("ALLOWED_USER_IDS"))
OWNER_USER_IDS = parse_ids(os.getenv("OWNER_USER_IDS"))
ALLOWED_ROLE_IDS = parse_ids(os.getenv("ALLOWED_ROLE_IDS"))
SEARCH_LIMIT = 10
ARTIST_QUEUE_LIMIT = 20


HELP_TEXT = """Команды бота:
`!зайди` - подключиться к твоему голосовому каналу
`!играть запрос` - найти и включить первый трек
`!поиск_трек запрос` - поиск треков, 10 результатов
`!поиск_исполнитель запрос` - поиск исполнителей, 10 результатов
`1` ... `10` - включить трек из последнего поиска
`!выбрать номер` - включить трек из последнего поиска
`!пауза` - пауза
`!продолжить` - продолжить
`!скип` - следующий трек
`!стоп` - остановить и очистить очередь
`!очередь` - показать очередь
`!играть_из_очереди номер` - включить трек из очереди
`!сейчас` - что играет сейчас
`!выйди` - отключиться"""


@dataclass
class TrackRequest:
    title: str
    artist: str
    stream_url: str
    requested_by: str

    @property
    def label(self) -> str:
        if self.artist:
            return f"{self.artist} - {self.title}"
        return self.title


@dataclass
class SearchItem:
    kind: str
    label: str
    query: str
    artist_id: str | int | None = None


class GuildPlayer:
    def __init__(self) -> None:
        self.queue: deque[TrackRequest] = deque()
        self.current: TrackRequest | None = None
        self.lock = asyncio.Lock()


players: dict[int, GuildPlayer] = {}
last_searches: dict[tuple[int, int], list[SearchItem]] = {}
slash_commands_synced = False

intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True

bot = commands.Bot(command_prefix=COMMAND_PREFIX, intents=intents, help_command=None)
ym_client = Client(YANDEX_MUSIC_TOKEN).init() if YANDEX_MUSIC_TOKEN else None
FFMPEG_EXECUTABLE = imageio_ffmpeg.get_ffmpeg_exe()


def get_player(guild_id: int) -> GuildPlayer:
    if guild_id not in players:
        players[guild_id] = GuildPlayer()
    return players[guild_id]


def is_allowed(ctx: commands.Context) -> bool:
    return is_user_allowed(ctx.author)


def is_user_allowed(user: discord.abc.User) -> bool:
    if not ALLOWED_USER_IDS and not ALLOWED_ROLE_IDS:
        return True

    if user.id in ALLOWED_USER_IDS:
        return True

    author_roles = getattr(user, "roles", [])
    return any(role.id in ALLOWED_ROLE_IDS for role in author_roles)


def is_owner_user(user: discord.abc.User) -> bool:
    if not OWNER_USER_IDS:
        return is_user_allowed(user)

    return user.id in OWNER_USER_IDS


async def ensure_interaction_allowed(interaction: discord.Interaction) -> bool:
    if is_user_allowed(interaction.user):
        return True

    await interaction.response.send_message(
        "У тебя нет доступа к управлению этим ботом.",
        ephemeral=True,
    )
    return False


async def ensure_interaction_owner(interaction: discord.Interaction) -> bool:
    if is_owner_user(interaction.user):
        return True

    await interaction.response.send_message(
        "Это действие доступно только владельцу бота.",
        ephemeral=True,
    )
    return False


def restricted():
    async def predicate(ctx: commands.Context) -> bool:
        if is_allowed(ctx):
            return True
        await ctx.reply("У тебя нет доступа к управлению этим ботом.")
        return False

    return commands.check(predicate)


def owner_restricted():
    async def predicate(ctx: commands.Context) -> bool:
        if is_owner_user(ctx.author):
            return True
        await ctx.reply("Это действие доступно только владельцу бота.")
        return False

    return commands.check(predicate)


def build_track_request(track, requested_by: str) -> TrackRequest:
    download_info = track.get_download_info()
    if not download_info:
        raise RuntimeError("Не удалось получить ссылку на аудио.")

    best = max(download_info, key=lambda item: item.bitrate_in_kbps or 0)
    stream_url = best.get_direct_link()

    artists = ", ".join(artist.name for artist in track.artists)
    return TrackRequest(
        title=track.title,
        artist=artists,
        stream_url=stream_url,
        requested_by=requested_by,
    )


def find_yandex_track(query: str, requested_by: str) -> TrackRequest:
    if ym_client is None:
        raise RuntimeError("YANDEX_MUSIC_TOKEN не задан в .env")

    result = ym_client.search(query, type_="track")
    if not result.tracks or not result.tracks.results:
        raise RuntimeError("Трек не найден.")

    return build_track_request(result.tracks.results[0], requested_by)


def find_artist_tracks(artist_id: str | int, requested_by: str, limit: int = ARTIST_QUEUE_LIMIT) -> list[TrackRequest]:
    if ym_client is None:
        raise RuntimeError("YANDEX_MUSIC_TOKEN не задан в .env")

    artist_tracks = ym_client.artists_tracks(artist_id, page=0, page_size=limit)
    if not artist_tracks or not artist_tracks.tracks:
        raise RuntimeError("У этого исполнителя не нашлось треков.")

    tracks: list[TrackRequest] = []
    errors: list[str] = []
    for track in artist_tracks.tracks[:limit]:
        try:
            tracks.append(build_track_request(track, requested_by))
        except Exception as exc:
            errors.append(str(exc))

    if not tracks:
        detail = errors[0] if errors else "Не удалось получить аудиоссылки."
        raise RuntimeError(detail)

    return tracks


def search_yandex_track_items(query: str, limit: int = SEARCH_LIMIT) -> list[SearchItem]:
    if ym_client is None:
        raise RuntimeError("YANDEX_MUSIC_TOKEN не задан в .env")

    result = ym_client.search(query, type_="track")
    if not result.tracks or not result.tracks.results:
        raise RuntimeError("Треки не найдены.")

    items: list[SearchItem] = []
    for track in result.tracks.results[:limit]:
        artists = ", ".join(artist.name for artist in track.artists)
        if artists:
            label = f"{artists} - {track.title}"
        else:
            label = track.title

        items.append(SearchItem(kind="track", label=label, query=label))

    return items


def search_yandex_artist_items(query: str, limit: int = SEARCH_LIMIT) -> list[SearchItem]:
    if ym_client is None:
        raise RuntimeError("YANDEX_MUSIC_TOKEN не задан в .env")

    result = ym_client.search(query, type_="artist")
    if not result.artists or not result.artists.results:
        raise RuntimeError("Исполнители не найдены.")

    items: list[SearchItem] = []
    for artist in result.artists.results[:limit]:
        items.append(
            SearchItem(
                kind="artist",
                label=artist.name,
                query=artist.name,
                artist_id=artist.id,
            )
        )

    return items


async def ensure_voice(ctx: commands.Context) -> discord.VoiceClient:
    if not ctx.author.voice or not ctx.author.voice.channel:
        raise RuntimeError("Сначала зайди в голосовой канал.")

    channel = ctx.author.voice.channel
    if ctx.voice_client:
        if ctx.voice_client.channel != channel:
            await ctx.voice_client.move_to(channel)
        return ctx.voice_client

    return await channel.connect()


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
        return voice

    return await channel.connect()


async def play_next(guild: discord.Guild) -> None:
    player = get_player(guild.id)

    async with player.lock:
        voice = guild.voice_client
        if voice is None or not voice.is_connected():
            player.current = None
            return

        if not player.queue:
            player.current = None
            return

        track = player.queue.popleft()
        player.current = track

        source = discord.FFmpegPCMAudio(
            track.stream_url,
            executable=FFMPEG_EXECUTABLE,
            before_options="-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
            options="-vn",
        )

        def after_play(error: Exception | None) -> None:
            if error:
                print(f"Playback error: {error}")
            future = asyncio.run_coroutine_threadsafe(play_next(guild), bot.loop)
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


class PlayerControls(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=600)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if is_user_allowed(interaction.user):
            return True

        await interaction.response.send_message(
            "У тебя нет доступа к управлению этим ботом.",
            ephemeral=True,
        )
        return False

    @discord.ui.button(label="Пауза", style=discord.ButtonStyle.secondary)
    async def pause_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        voice = interaction.guild.voice_client if interaction.guild else None
        if voice and voice.is_playing():
            voice.pause()
            await interaction.response.send_message("Пауза.", ephemeral=True)
        else:
            await interaction.response.send_message("Сейчас ничего не играет.", ephemeral=True)

    @discord.ui.button(label="Играть", style=discord.ButtonStyle.success)
    async def resume_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        voice = interaction.guild.voice_client if interaction.guild else None
        if voice and voice.is_paused():
            voice.resume()
            await interaction.response.send_message("Продолжаю.", ephemeral=True)
        else:
            await interaction.response.send_message("Сейчас нет паузы.", ephemeral=True)

    @discord.ui.button(label="Следующая", style=discord.ButtonStyle.primary)
    async def skip_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        voice = interaction.guild.voice_client if interaction.guild else None
        if voice and (voice.is_playing() or voice.is_paused()):
            voice.stop()
            await interaction.response.send_message("Пропускаю.", ephemeral=True)
        else:
            await interaction.response.send_message("Сейчас нечего пропускать.", ephemeral=True)

    @discord.ui.button(label="Стоп", style=discord.ButtonStyle.danger)
    async def stop_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("Команда работает только на сервере.", ephemeral=True)
            return

        player = get_player(interaction.guild.id)
        player.queue.clear()
        player.current = None

        voice = interaction.guild.voice_client
        if voice:
            voice.stop()

        await interaction.response.send_message("Остановил и очистил очередь.", ephemeral=True)


async def enqueue_track(ctx: commands.Context, query: str) -> None:
    voice = await ensure_voice(ctx)
    await ctx.typing()

    try:
        track = await asyncio.to_thread(find_yandex_track, query, str(ctx.author))
    except Exception as exc:
        await ctx.reply(f"Не получилось найти или открыть трек: {exc}")
        return

    player = get_player(ctx.guild.id)
    player.queue.append(track)

    if not voice.is_playing() and not voice.is_paused():
        await play_next(ctx.guild)
        await ctx.reply(f"Играет: {track.label}", view=PlayerControls())
    else:
        await ctx.reply(f"Добавил в очередь: {track.label}", view=PlayerControls())


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
    was_idle = not voice.is_playing() and not voice.is_paused()
    player.queue.extend(tracks)

    if was_idle:
        await play_next(ctx.guild)

    await ctx.reply(
        f"Добавил треки исполнителя {item.label}: {len(tracks)} шт.",
        view=PlayerControls(),
    )


async def enqueue_track_interaction(interaction: discord.Interaction, query: str) -> None:
    if interaction.guild is None:
        await interaction.followup.send("Команда работает только на сервере.", ephemeral=True)
        return

    try:
        voice = await ensure_interaction_voice(interaction)
    except Exception as exc:
        await interaction.followup.send(str(exc), ephemeral=True)
        return

    try:
        track = await asyncio.to_thread(find_yandex_track, query, str(interaction.user))
    except Exception as exc:
        await interaction.followup.send(f"Не получилось найти или открыть трек: {exc}", ephemeral=True)
        return

    player = get_player(interaction.guild.id)
    player.queue.append(track)

    if not voice.is_playing() and not voice.is_paused():
        await play_next(interaction.guild)
        await interaction.followup.send(f"Играет: {track.label}", view=PlayerControls())
    else:
        await interaction.followup.send(f"Добавил в очередь: {track.label}", view=PlayerControls())


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
    was_idle = not voice.is_playing() and not voice.is_paused()
    player.queue.extend(tracks)

    if was_idle:
        await play_next(interaction.guild)

    await interaction.followup.send(
        f"Добавил треки исполнителя {item.label}: {len(tracks)} шт.",
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
        await enqueue_track(ctx, item.query)


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
        await enqueue_track_interaction(interaction, item.query)


@bot.event
async def on_ready() -> None:
    global slash_commands_synced
    if not slash_commands_synced:
        for guild in bot.guilds:
            bot.tree.copy_global_to(guild=guild)
            synced = await bot.tree.sync(guild=guild)
            print(f"Synced {len(synced)} slash commands to {guild.name}")
        slash_commands_synced = True
    print(f"Logged in as {bot.user}")


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
    await ctx.reply("Нашел треки в Яндекс Музыке:\n" + "\n".join(lines) + "\n\nНапиши номер, например `3`, чтобы включить трек.")


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

    if ctx.voice_client:
        ctx.voice_client.stop()

    await ctx.reply("Остановил и очистил очередь.")


@bot.command(name="leave", aliases=["выйди", "отключись"])
@restricted()
async def leave_command(ctx: commands.Context) -> None:
    player = get_player(ctx.guild.id)
    player.queue.clear()
    player.current = None

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
        + "\n\nИспользуй `/select number`, чтобы включить трек."
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

    if interaction.guild.voice_client:
        interaction.guild.voice_client.stop()

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

    if interaction.guild.voice_client:
        await interaction.guild.voice_client.disconnect()
        await interaction.response.send_message("Вышел из голосового канала.", ephemeral=True)
    else:
        await interaction.response.send_message("Я не в голосовом канале.", ephemeral=True)


@bot.tree.command(name="commands", description="Показать список команд")
async def slash_commands(interaction: discord.Interaction) -> None:
    if not await ensure_interaction_allowed(interaction):
        return

    slash_help = """Slash-команды:
`/join` - подключиться к голосовому каналу
`/play query` - найти и включить трек
`/search_track query` - найти 10 треков
`/search_artist query` - найти 10 исполнителей
`/select number` - включить трек из последнего поиска
`/pause` - пауза
`/resume` - продолжить
`/skip` - следующий трек
`/stop` - остановить и очистить очередь
`/queue` - показать очередь
`/queue_play number` - включить трек из очереди
`/now` - что играет сейчас
`/leave` - отключиться"""
    await interaction.response.send_message(slash_help, ephemeral=True)


@bot.event
async def on_command_error(ctx: commands.Context, error: commands.CommandError) -> None:
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.reply("Не хватает аргумента команды.")
        return

    if isinstance(error, commands.CommandNotFound):
        return

    await ctx.reply(f"Ошибка: {error}")


if not DISCORD_TOKEN:
    raise RuntimeError("DISCORD_TOKEN не задан в .env")

try:
    bot.run(DISCORD_TOKEN)
except discord.PrivilegedIntentsRequired as exc:
    raise RuntimeError(
        "В Discord Developer Portal нужно включить MESSAGE CONTENT INTENT: "
        "Applications -> твой бот -> Bot -> Privileged Gateway Intents -> "
        "Message Content Intent -> Save Changes."
    ) from exc
