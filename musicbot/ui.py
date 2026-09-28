import discord

from .access import is_user_allowed
from .player import disable_wave, request_skip, schedule_idle_disconnect
from .state import get_player


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
            if interaction.guild:
                request_skip(get_player(interaction.guild.id))
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
        disable_wave(player)

        voice = interaction.guild.voice_client
        if voice:
            voice.stop()
            schedule_idle_disconnect(interaction.guild)

        await interaction.response.send_message("Остановил и очистил очередь.", ephemeral=True)

