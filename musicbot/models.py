import asyncio
from collections import deque
from dataclasses import dataclass


@dataclass
class TrackRequest:
    title: str
    artist: str
    requested_by: str
    track_id: str | int | None = None
    stream_url: str | None = None
    starts_wave: bool = False
    wave_station: str | None = None
    wave_batch_id: str | None = None
    wave_track_id: str | int | None = None
    duration_seconds: float | None = None
    is_wave_track: bool = False

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
    track_id: str | int | None = None
    artist_id: str | int | None = None


class GuildPlayer:
    def __init__(self) -> None:
        self.queue: deque[TrackRequest] = deque()
        self.current: TrackRequest | None = None
        self.text_channel_id: int | None = None
        self.idle_disconnect_task: asyncio.Task | None = None
        self.shuffle_enabled = False
        self.wave_enabled = False
        self.wave_station: str | None = None
        self.wave_batch_id: str | None = None
        self.wave_queue_id: str | int | None = None
        self.wave_seed_label: str | None = None
        self.wave_requested_by: str | None = None
        self.skip_requested = False
        self.lock = asyncio.Lock()
