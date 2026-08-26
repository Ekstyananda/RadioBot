"""
DashboardView — Unified Discord UI untuk Radio + Music mode.

Satu View yang menampilkan semua kontrol:
- Row 1: Mode switch (Radio/Music) + Transport (Play/Stop/Refresh|Skip)
- Row 2: Music controls (Request/Skip/Shuffle/Queue/Lyrics) — hanya di Music Mode
- Row 3: Radio dropdown — hanya di Radio Mode
"""

import discord
from config import FFMPEG_OPTS


class RequestSongModal(discord.ui.Modal, title="🎵 Request Lagu"):
    """Modal popup untuk user mengetik judul lagu."""

    query = discord.ui.TextInput(
        label="Judul Lagu / Link YouTube",
        placeholder="Contoh: Bohemian Rhapsody, atau paste link YouTube...",
        style=discord.TextStyle.short,
        required=True,
        max_length=200
    )

    def __init__(self, dashboard_view):
        super().__init__()
        self.dashboard_view = dashboard_view

    async def on_submit(self, interaction: discord.Interaction):
        music_cog = self.dashboard_view.bot_cog.bot.get_cog('Music')
        if not music_cog:
            return await interaction.response.send_message("❌ Music module tidak tersedia.", ephemeral=True)

        await interaction.response.send_message(f"🔎 Mencari **{self.query.value}**...", ephemeral=False)

        # Trigger play dari Music cog
        await music_cog.handle_play_request(
            interaction.guild,
            interaction.channel,
            interaction.user,
            self.query.value
        )


class RadioSelect(discord.ui.Select):
    """Dropdown pemilih stasiun radio."""

    def __init__(self, bot_cog, stations):
        self.bot_cog = bot_cog
        self.stations = stations

        options = [discord.SelectOption(label=name, value=name) for name in self.stations.keys()]
        options = options[:25]

        if not options:
            options = [discord.SelectOption(label="Tidak ada stasiun", value="_none")]

        super().__init__(
            placeholder="📻 Pilih Stasiun Radio...",
            min_values=1,
            max_values=1,
            options=options,
            row=4
        )

    async def callback(self, interaction: discord.Interaction):
        chosen_name = self.values[0]
        if chosen_name == "_none" or chosen_name not in self.stations:
            await interaction.response.send_message("❌ Radio ini tidak tersedia.", ephemeral=True)
            return

        chosen_url = self.stations[chosen_name]
        await self.bot_cog.handle_station_switch(interaction, chosen_name, chosen_url, self.view)


class DashboardView(discord.ui.View):
    """
    View utama Dashboard.
    Tombol yang tampil berubah berdasarkan mode (radio/music).
    """

    def __init__(self, bot_cog, stations, mode="radio"):
        super().__init__(timeout=None)
        self.bot_cog = bot_cog
        self.stations = stations
        self.mode = mode
        self._build_buttons()

    def _build_buttons(self):
        """Rebuild items berdasarkan mode aktif."""
        self.clear_items()

        # ─── Row 0: Mode Switch ───
        radio_style = discord.ButtonStyle.primary if self.mode == "radio" else discord.ButtonStyle.secondary
        music_style = discord.ButtonStyle.primary if self.mode == "music" else discord.ButtonStyle.secondary

        radio_btn = discord.ui.Button(label="📻 Radio", style=radio_style, row=0, custom_id="dash_radio")
        radio_btn.callback = self._on_radio_mode
        self.add_item(radio_btn)

        music_btn = discord.ui.Button(label="🎵 Music", style=music_style, row=0, custom_id="dash_music")
        music_btn.callback = self._on_music_mode
        self.add_item(music_btn)

        play_btn = discord.ui.Button(label="▶️ Play", style=discord.ButtonStyle.green, row=0, custom_id="dash_play")
        play_btn.callback = self._on_play
        self.add_item(play_btn)

        stop_btn = discord.ui.Button(label="⏹️ Stop", style=discord.ButtonStyle.red, row=0, custom_id="dash_stop")
        stop_btn.callback = self._on_stop
        self.add_item(stop_btn)

        # ─── Row 1: Context actions ───
        if self.mode == "radio":
            refresh_btn = discord.ui.Button(label="🔄 Refresh", style=discord.ButtonStyle.secondary, row=1, custom_id="dash_refresh")
            refresh_btn.callback = self._on_refresh
            self.add_item(refresh_btn)
        else:
            skip_btn = discord.ui.Button(label="⏭️ Skip", style=discord.ButtonStyle.secondary, row=1, custom_id="dash_skip")
            skip_btn.callback = self._on_skip
            self.add_item(skip_btn)

            shuffle_btn = discord.ui.Button(label="🔀 Shuffle", style=discord.ButtonStyle.secondary, row=1, custom_id="dash_shuffle")
            shuffle_btn.callback = self._on_shuffle
            self.add_item(shuffle_btn)

            queue_btn = discord.ui.Button(label="📋 Queue", style=discord.ButtonStyle.secondary, row=1, custom_id="dash_queue")
            queue_btn.callback = self._on_queue
            self.add_item(queue_btn)

            lyrics_btn = discord.ui.Button(label="🎤 Lyrics", style=discord.ButtonStyle.secondary, row=1, custom_id="dash_lyrics")
            lyrics_btn.callback = self._on_lyrics
            self.add_item(lyrics_btn)

        # ─── Row 2: Music request (only music mode) ───
        if self.mode == "music":
            request_btn = discord.ui.Button(label="🎵 Request Lagu", style=discord.ButtonStyle.success, row=2, custom_id="dash_request")
            request_btn.callback = self._on_request
            self.add_item(request_btn)

        # ─── Row 4: Radio dropdown (only radio mode) ───
        if self.mode == "radio" and self.stations:
            self.add_item(RadioSelect(self.bot_cog, self.stations))

    # ──── Callbacks ────

    async def _on_radio_mode(self, interaction: discord.Interaction):
        await self.bot_cog.handle_mode_switch(interaction, "radio", self)

    async def _on_music_mode(self, interaction: discord.Interaction):
        await self.bot_cog.handle_mode_switch(interaction, "music", self)

    async def _on_play(self, interaction: discord.Interaction):
        await self.bot_cog.handle_play(interaction, self)

    async def _on_stop(self, interaction: discord.Interaction):
        await self.bot_cog.handle_stop(interaction, self)

    async def _on_refresh(self, interaction: discord.Interaction):
        await self.bot_cog.handle_refresh(interaction)

    async def _on_skip(self, interaction: discord.Interaction):
        music_cog = self.bot_cog.bot.get_cog('Music')
        if music_cog:
            vc = interaction.guild.voice_client
            is_playing = getattr(vc, 'playing', False) or (hasattr(vc, 'is_playing') and (vc.is_playing() if callable(vc.is_playing) else vc.is_playing))
            if vc and is_playing:
                stop_func = getattr(vc, 'stop', None)
                if stop_func:
                    import asyncio
                    if asyncio.iscoroutinefunction(stop_func) or 'Player' in vc.__class__.__name__:
                        await vc.stop()
                    else:
                        vc.stop()
                await interaction.response.send_message("⏭️ Lagu dilewati!", ephemeral=True)
            else:
                await interaction.response.send_message("❌ Tidak ada lagu yang diputar.", ephemeral=True)
        else:
            await interaction.response.send_message("❌ Music module tidak tersedia.", ephemeral=True)

    async def _on_shuffle(self, interaction: discord.Interaction):
        from utils.mode_manager import ModeManager
        radio_cog = self.bot_cog
        if hasattr(radio_cog, 'mode_mgr'):
            radio_cog.mode_mgr.shuffle_queue(interaction.guild.id)
            size = radio_cog.mode_mgr.queue_size(interaction.guild.id)
            await interaction.response.send_message(f"🔀 Antrian diacak! ({size} lagu)", ephemeral=True)
        else:
            await interaction.response.send_message("❌ Tidak bisa shuffle.", ephemeral=True)

    async def _on_queue(self, interaction: discord.Interaction):
        radio_cog = self.bot_cog
        if not hasattr(radio_cog, 'mode_mgr'):
            return await interaction.response.send_message("❌ Error.", ephemeral=True)

        queue = radio_cog.mode_mgr.peek_queue(interaction.guild.id)
        if not queue:
            return await interaction.response.send_message("📋 Antrian kosong. Autoplay akan mengisi otomatis!", ephemeral=True)

        lines = []
        for i, track in enumerate(queue[:15], 1):
            title = track.get('title', 'Unknown')
            artist = track.get('artist', '')
            line = f"`{i}.` **{title}**"
            if artist:
                line += f" — {artist}"
            lines.append(line)

        if len(queue) > 15:
            lines.append(f"\n*...dan {len(queue) - 15} lagu lainnya*")

        embed = discord.Embed(
            title="📋 Antrian Musik",
            description="\n".join(lines),
            color=discord.Color.blue()
        )
        embed.set_footer(text=f"Total: {len(queue)} lagu")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def _on_lyrics(self, interaction: discord.Interaction):
        music_cog = self.bot_cog.bot.get_cog('Music')
        radio_cog = self.bot_cog

        if not hasattr(radio_cog, 'mode_mgr'):
            return await interaction.response.send_message("❌ Error.", ephemeral=True)

        track = radio_cog.mode_mgr.get_current_track(interaction.guild.id)
        if not track:
            return await interaction.response.send_message("❌ Tidak ada lagu yang sedang diputar.", ephemeral=True)

        title = track.get('title', '')
        if not title:
            return await interaction.response.send_message("❌ Judul lagu tidak diketahui.", ephemeral=True)

        await interaction.response.defer(ephemeral=True)

        if music_cog:
            embed = await music_cog.fetch_lyrics_embed(title, track.get('artist', ''))
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.followup.send("❌ Music module tidak tersedia.", ephemeral=True)

    async def _on_request(self, interaction: discord.Interaction):
        modal = RequestSongModal(self)
        await interaction.response.send_modal(modal)


# ──── Embed Builder ────

def build_dashboard_embed(mode: str, state, queue_size: int = 0) -> discord.Embed:
    """
    Buat embed dashboard berdasarkan mode dan state.
    
    Args:
        mode: "radio" atau "music"
        state: GuildState object atau dict dengan info now playing
        queue_size: jumlah lagu dalam antrian
    """
    if mode == "radio":
        embed = discord.Embed(
            title="📻 Radio Mode",
            color=discord.Color.blue()
        )
        radio_name = "No Radio"
        if hasattr(state, 'radio_name') and state.radio_name:
            radio_name = state.radio_name
        elif isinstance(state, dict):
            radio_name = state.get('name', 'No Radio')

        embed.add_field(name="🔊 Sedang Memutar", value=f"**{radio_name}**", inline=False)
        embed.add_field(name="📊 Status", value="▶️ Streaming", inline=True)
        embed.set_footer(text="📻 Radio System • 24/7 | Klik 🎵 Music untuk mode pemutar lagu")

    else:  # music
        embed = discord.Embed(
            title="🎵 Music Mode",
            color=discord.Color.purple()
        )

        now_playing = "Tidak ada lagu"
        artist_text = ""
        thumbnail = None

        if hasattr(state, 'current_track') and state.current_track:
            track = state.current_track
            now_playing = track.get('title', 'Unknown')
            artist_text = track.get('artist', '')
            thumbnail = track.get('thumbnail')
        elif isinstance(state, dict) and state.get('title'):
            now_playing = state.get('title', 'Unknown')
            artist_text = state.get('artist', '')
            thumbnail = state.get('thumbnail')

        embed.add_field(name="🎧 Sedang Memutar", value=f"**{now_playing}**", inline=False)
        if artist_text:
            embed.add_field(name="🎤 Artis", value=artist_text, inline=True)

        if queue_size > 0:
            embed.add_field(name="⏭️ Antrian", value=f"{queue_size} lagu", inline=True)
        else:
            embed.add_field(name="⏭️ Antrian", value="Autoplay 🔄", inline=True)

        embed.add_field(name="📊 Status", value="▶️ Playing", inline=True)

        if thumbnail:
            embed.set_thumbnail(url=thumbnail)

        embed.set_footer(text="🎵 Music Player • Autoplay ON | Klik 📻 Radio untuk mode radio")

    return embed


# ──── Backward Compat ────
# radio_ui.py akan import ini
ControlButtons = DashboardView
