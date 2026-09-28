import discord
from discord.ext import commands

from .config import ALLOW_ALL_GUILD_MEMBERS, ALLOWED_ROLE_IDS, ALLOWED_USER_IDS, OWNER_USER_IDS


def is_allowed(ctx: commands.Context) -> bool:
    return is_user_allowed(ctx.author)


def is_user_allowed(user: discord.abc.User) -> bool:
    if ALLOW_ALL_GUILD_MEMBERS:
        return True

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
