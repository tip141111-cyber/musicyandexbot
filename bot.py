import discord
from discord.ext import commands

from musicbot.commands import register_commands
from musicbot.config import COMMAND_PREFIX, DISCORD_TOKEN
from musicbot.player import configure_player


intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True

bot = commands.Bot(command_prefix=COMMAND_PREFIX, intents=intents, help_command=None)
configure_player(bot)
register_commands(bot)

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
