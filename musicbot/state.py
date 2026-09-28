import random

from .models import GuildPlayer, SearchItem, TrackRequest


players: dict[int, GuildPlayer] = {}
last_searches: dict[tuple[int, int], list[SearchItem]] = {}

def get_player(guild_id: int) -> GuildPlayer:
    if guild_id not in players:
        players[guild_id] = GuildPlayer()
    return players[guild_id]

def pop_next_track(player: GuildPlayer) -> TrackRequest:
    if not player.shuffle_enabled or len(player.queue) <= 1:
        return player.queue.popleft()

    selected_index = random.randrange(len(player.queue))
    player.queue.rotate(-selected_index)
    track = player.queue.popleft()
    player.queue.rotate(selected_index)
    return track
