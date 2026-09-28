import os
import shutil

import imageio_ffmpeg
from dotenv import load_dotenv


load_dotenv()


def get_secret(name: str) -> str | None:
    value = os.getenv(name)
    if value is None:
        return None

    value = value.strip().strip('"').strip("'")
    if value.startswith(f"{name}="):
        value = value.split("=", 1)[1].strip().strip('"').strip("'")

    if any(ord(char) > 127 for char in value):
        raise RuntimeError(
            f"{name} содержит русские буквы или лишний текст. "
            "В .env должен быть только сам токен после знака ="
        )

    return value or None


DISCORD_TOKEN = get_secret("DISCORD_TOKEN")
YANDEX_MUSIC_TOKEN = get_secret("YANDEX_MUSIC_TOKEN")
COMMAND_PREFIX = os.getenv("COMMAND_PREFIX", "!")
ALLOW_ALL_GUILD_MEMBERS = os.getenv("ALLOW_ALL_GUILD_MEMBERS", "false").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
    "да",
}


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
PLAYLIST_QUEUE_LIMIT = int(os.getenv("PLAYLIST_QUEUE_LIMIT", "50"))

FFMPEG_EXECUTABLE = os.getenv("FFMPEG_EXECUTABLE") or shutil.which("ffmpeg") or imageio_ffmpeg.get_ffmpeg_exe()
FFMPEG_AUDIO_FILTER = os.getenv("FFMPEG_AUDIO_FILTER", "aresample=48000").strip()
FFMPEG_VOLUME = os.getenv("FFMPEG_VOLUME", "1.0").strip()
IDLE_DISCONNECT_SECONDS = int(os.getenv("IDLE_DISCONNECT_SECONDS", "900"))
WAVE_MOOD_ENERGY = os.getenv("WAVE_MOOD_ENERGY", "all").strip()
WAVE_DIVERSITY = os.getenv("WAVE_DIVERSITY", "discover").strip()
WAVE_LANGUAGE = os.getenv("WAVE_LANGUAGE", "any").strip()
WAVE_TYPE = os.getenv("WAVE_TYPE", "rotor").strip()


HELP_TEXT = """Команды бота:
`!зайди` - подключиться к твоему голосовому каналу
`!играть запрос` - найти и включить первый трек
`!wave_track запрос` - включить трек и продолжить похожей волной
`!поиск_трек запрос` - поиск треков, 10 результатов
`!поиск_исполнитель запрос` - поиск исполнителей, 10 результатов
`!плейлист ссылка` - добавить треки из плейлиста Яндекс Музыки
`1` ... `10` - включить трек из последнего поиска
`!выбрать номер` - включить трек из последнего поиска
`!пауза` - пауза
`!продолжить` - продолжить
`!скип` - следующий трек
`!стоп` - остановить и очистить очередь
`!очередь` - показать очередь
`!играть_из_очереди номер` - включить трек из очереди
`!shuffle on/off` - включить или выключить случайный порядок
`!сейчас` - что играет сейчас
`!выйди` - отключиться"""
