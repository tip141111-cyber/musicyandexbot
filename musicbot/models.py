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
        self.text_channel_id: int | None = None
        self.idle_disconnect_task: asyncio.Task | None = None
        self.shuffle_enabled = False
        self.lock = asyncio.Lock()
