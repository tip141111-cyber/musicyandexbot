import re
from urllib.parse import urlparse

from yandex_music import Client

from .config import (
    ARTIST_QUEUE_LIMIT,
    PLAYLIST_QUEUE_LIMIT,
    SEARCH_LIMIT,
    WAVE_DIVERSITY,
    WAVE_LANGUAGE,
    WAVE_MOOD_ENERGY,
    WAVE_TYPE,
    YANDEX_MUSIC_TOKEN,
)
from .models import SearchItem, TrackRequest


UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)

ym_client = Client(YANDEX_MUSIC_TOKEN).init() if YANDEX_MUSIC_TOKEN else None


def get_track_stream_url(track) -> str:
    download_info = track.get_download_info()
    if not download_info:
        raise RuntimeError("Не удалось получить ссылку на аудио.")

    best = max(download_info, key=lambda item: item.bitrate_in_kbps or 0)
    return best.get_direct_link()


def get_track_request_id(track) -> str | int | None:
    track_id = getattr(track, "id", None)
    albums = getattr(track, "albums", None) or []
    if track_id is not None and albums:
        album_id = getattr(albums[0], "id", None)
        if album_id is not None:
            return f"{track_id}:{album_id}"

    return track_id


def get_wave_track_id(track_id: str | int | None) -> str | int | None:
    if isinstance(track_id, str) and ":" in track_id:
        return track_id.split(":", 1)[0]
    return track_id


def build_track_request(track, requested_by: str) -> TrackRequest:
    artists = ", ".join(artist.name for artist in track.artists)
    track_id = get_track_request_id(track)
    return TrackRequest(
        title=track.title,
        artist=artists,
        requested_by=requested_by,
        track_id=track_id,
        wave_track_id=get_wave_track_id(track_id),
        duration_seconds=(track.duration_ms / 1000) if getattr(track, "duration_ms", None) else None,
    )


def build_wave_station(track_request: TrackRequest) -> str:
    wave_track_id = get_wave_track_id(track_request.wave_track_id or track_request.track_id)
    if wave_track_id is None:
        raise RuntimeError("У трека нет ID для запуска волны.")
    return f"track:{wave_track_id}"


def resolve_track_stream_url(track_request: TrackRequest) -> str:
    if ym_client is None:
        raise RuntimeError("YANDEX_MUSIC_TOKEN не задан в .env")

    if track_request.track_id is None:
        if track_request.stream_url:
            return track_request.stream_url
        raise RuntimeError("У трека нет ID для обновления ссылки.")

    tracks = ym_client.tracks(track_request.track_id)
    if not tracks:
        raise RuntimeError("Не удалось обновить ссылку на трек.")

    stream_url = get_track_stream_url(tracks[0])
    track_request.stream_url = stream_url
    return stream_url


def find_yandex_track(query: str, requested_by: str) -> TrackRequest:
    if ym_client is None:
        raise RuntimeError("YANDEX_MUSIC_TOKEN не задан в .env")

    result = ym_client.search(query, type_="track")
    if not result.tracks or not result.tracks.results:
        raise RuntimeError("Трек не найден.")

    return build_track_request(result.tracks.results[0], requested_by)


def find_yandex_track_by_id(track_id: str | int, requested_by: str) -> TrackRequest:
    if ym_client is None:
        raise RuntimeError("YANDEX_MUSIC_TOKEN не задан в .env")

    tracks = ym_client.tracks(track_id)
    if not tracks:
        raise RuntimeError("Трек не найден.")

    return build_track_request(tracks[0], requested_by)


def find_wave_seed_track(query: str, requested_by: str) -> TrackRequest:
    track = find_yandex_track(query, requested_by)
    track.starts_wave = True
    track.wave_station = build_wave_station(track)
    return track


def get_wave_tracks(
    station: str,
    requested_by: str,
    batch_id: str | None = None,
    queue_id: str | int | None = None,
    limit: int = 10,
) -> tuple[list[TrackRequest], str | None]:
    if ym_client is None:
        raise RuntimeError("YANDEX_MUSIC_TOKEN не задан в .env")

    try:
        ym_client.rotor_station_settings2(
            station,
            mood_energy=WAVE_MOOD_ENERGY,
            diversity=WAVE_DIVERSITY,
            language=WAVE_LANGUAGE,
            type_=WAVE_TYPE,
        )
    except Exception as exc:
        print(f"Wave settings failed for {station}: {exc}")

    result = ym_client.rotor_station_tracks(station, queue=queue_id)
    if result is None or not result.sequence:
        raise RuntimeError("Яндекс Музыка не вернула треки для волны.")

    tracks: list[TrackRequest] = []
    next_batch_id = result.batch_id or batch_id
    for sequence_item in result.sequence:
        track = getattr(sequence_item, "track", None)
        if track is None:
            continue

        track_request = build_track_request(track, requested_by)
        track_request.wave_station = station
        track_request.wave_batch_id = next_batch_id
        track_request.is_wave_track = True
        tracks.append(track_request)

        if len(tracks) >= limit:
            break

    if not tracks:
        raise RuntimeError("В волне не нашлось треков.")

    return tracks, next_batch_id


def send_wave_radio_started(station: str, batch_id: str | None = None) -> None:
    if ym_client is not None:
        ym_client.rotor_station_feedback_radio_started(station, from_="discord", batch_id=batch_id)


def send_wave_track_started(station: str, track_id: str | int, batch_id: str | None = None) -> None:
    if ym_client is not None:
        ym_client.rotor_station_feedback_track_started(station, track_id, batch_id=batch_id)


def send_wave_track_finished(
    station: str,
    track_id: str | int,
    total_played_seconds: float,
    batch_id: str | None = None,
) -> None:
    if ym_client is not None:
        ym_client.rotor_station_feedback_track_finished(
            station,
            track_id,
            total_played_seconds=total_played_seconds,
            batch_id=batch_id,
        )


def send_wave_track_skipped(
    station: str,
    track_id: str | int,
    total_played_seconds: float,
    batch_id: str | None = None,
) -> None:
    if ym_client is not None:
        ym_client.rotor_station_feedback_skip(
            station,
            track_id,
            total_played_seconds=total_played_seconds,
            batch_id=batch_id,
        )


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


def parse_playlist_reference(playlist_reference: str) -> tuple[str | None, str]:
    parsed = urlparse(playlist_reference.strip())
    if not parsed.scheme:
        return None, playlist_reference.strip()

    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) >= 4 and parts[0] == "users" and parts[2] == "playlists":
        return parts[1], parts[3]

    if len(parts) >= 2 and parts[0] == "playlists":
        return None, parts[1]

    raise RuntimeError(
        "Не понял ссылку на плейлист. Нужна ссылка вида "
        "https://music.yandex.ru/users/user/playlists/1000"
    )


def load_yandex_playlist(user_id: str | None, kind: str):
    if kind.startswith("lk."):
        playlist_uuid = kind.removeprefix("lk.")
        return ym_client.playlist(playlist_uuid)

    if UUID_RE.fullmatch(kind):
        return ym_client.playlist(kind)

    try:
        return ym_client.users_playlists(kind=kind, user_id=user_id)
    except Exception:
        if "-" in kind:
            return ym_client.playlist(kind.removeprefix("lk."))
        raise


def find_playlist_tracks(playlist_reference: str, requested_by: str, limit: int = PLAYLIST_QUEUE_LIMIT) -> list[TrackRequest]:
    if ym_client is None:
        raise RuntimeError("YANDEX_MUSIC_TOKEN не задан в .env")

    user_id, kind = parse_playlist_reference(playlist_reference)
    playlist = load_yandex_playlist(user_id, kind)
    if playlist is None:
        raise RuntimeError("Плейлист не найден или нет доступа.")

    playlist_tracks = playlist.tracks or []
    if not playlist_tracks:
        raise RuntimeError("В плейлисте не нашлось треков.")

    tracks: list[TrackRequest] = []
    errors: list[str] = []
    for playlist_track in playlist_tracks[:limit]:
        track = getattr(playlist_track, "track", playlist_track)
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

        items.append(
            SearchItem(
                kind="track",
                label=label,
                query=label,
                track_id=get_track_request_id(track),
            )
        )

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
