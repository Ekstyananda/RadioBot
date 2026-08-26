"""
Radio Cog — Sistem Radio 24/7 dengan integrasi Music Mode.

Fitur:
- Auto-join & auto-play radio stream
- Dashboard unified (Radio + Music mode switch)
- Mode management via ModeManager
- Anti-Kutu Loncat (jangan pindah channel jika sudah di target)
- Error logging (tidak ada except: pass)
"""

import discord
from discord.ext import commands, tasks
import asyncio
import psutil
import datetime

from config import TARGET_CHANNEL_IDS, FFMPEG_OPTS
from utils.storage import load_stations, save_stations
from utils.mode_manager import ModeManager
from views.dashboard_ui import DashboardView, build_dashboard_embed


class Radio(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.mode_mgr = ModeManager()
        self.stations = load_stations()
        self.start_time = datetime.datetime.now()
        self._dashboard_messages = {}  # guild_id -> message_id (untuk update embed)
        self.check_radio_status.start()

        # Init radio stations di ModeManager
        if self.stations:
            default_name = list(self.stations.keys())[0]
            default_url = list(self.stations.values())[0]
            # Default state akan di-set saat guild pertama kali diakses

    def cog_unload(self):
        self.check_radio_status.cancel()

    # ──── Legacy Compat ────
    # Property ini menjaga backward compat untuk kode yang masih akses self.voice_states

    @property
    def voice_states(self):
        """Backward compat — return proxy yang delegate ke ModeManager."""
        return _VoiceStateProxy(self.mode_mgr, self.stations)

    def get_guild_state(self, guild_id):
        """Backward compat — return dict {name, url}."""
        return self.mode_mgr.get_voice_state_display(guild_id, self.stations)

    @staticmethod
    def _vc_is_playing(vc):
        """Cek apakah vc sedang playing, aman untuk VoiceClient dan wavelink.Player."""
        if vc is None:
            return False
        if hasattr(vc, 'playing') and isinstance(vc.playing, bool):
            return vc.playing
        if hasattr(vc, 'is_playing') and callable(vc.is_playing):
            return vc.is_playing()
        return False

    @staticmethod
    def _vc_is_connected(vc):
        """Cek apakah vc sedang connected, aman untuk VoiceClient dan wavelink.Player."""
        if vc is None:
            return False
        # wavelink.Player v3 mungkin tidak punya is_connected()
        if hasattr(vc, 'is_connected') and callable(vc.is_connected):
            return vc.is_connected()
        if hasattr(vc, 'connected'):
            return bool(vc.connected)
        if hasattr(vc, 'channel'):
            return vc.channel is not None
        return False

    @staticmethod
    async def _vc_stop(vc):
        """Stop playback, aman untuk VoiceClient dan wavelink.Player."""
        if vc is None:
            return
        try:
            if getattr(vc, '__class__', None).__name__ == 'Player':
                await vc.stop()
            elif hasattr(vc, 'stop') and callable(vc.stop):
                vc.stop()
        except Exception as e:
            print(f"⚠️ Error stopping vc: {e}")

    @staticmethod
    def _vc_is_playing(vc):
        """Cek apakah VoiceClient atau wavelink.Player sedang memutar lagu."""
        if vc is None:
            return False
        if getattr(vc, '__class__', None).__name__ == 'Player':
            return getattr(vc, 'playing', False)
        if hasattr(vc, 'is_playing') and callable(vc.is_playing):
            return vc.is_playing()
        return False

    # ──── Stream Control ────

    async def restart_stream(self, guild, force_url=None):
        """Restart stream radio. Jika mode music, delegate ke Music cog."""
        vc = guild.voice_client
        if not vc:
            return

        state = self.mode_mgr.get(guild.id)

        # Jika mode music dan tidak ada force_url, delegate ke Music cog
        if state.is_music() and not force_url:
            music_cog = self.bot.get_cog('Music')
            if music_cog:
                # Cari channel untuk kirim embed
                for ch_id in TARGET_CHANNEL_IDS:
                    ch = self.bot.get_channel(ch_id)
                    if ch and ch.guild.id == guild.id:
                        text_channels = [c for c in guild.text_channels if c.permissions_for(guild.me).send_messages]
                        if text_channels:
                            await music_cog.play_next(guild, text_channels[0])
                        return
                # Fallback: cari text channel apapun
                for tc in guild.text_channels:
                    if tc.permissions_for(guild.me).send_messages:
                        await music_cog.play_next(guild, tc)
                        return
            return

        # Mode Radio — play stream
        url = force_url
        station_name = "Radio"
        if not url:
            display = self.mode_mgr.get_voice_state_display(guild.id, self.stations)
            if "🎵" in display['name'] and self.stations:
                # Lagu music habis, kembali ke radio default
                default_name = list(self.stations.keys())[0]
                default_url = list(self.stations.values())[0]
                self.mode_mgr.switch_to_radio(guild.id, default_name, default_url)
                url = default_url
                station_name = default_name
            else:
                url = display['url']
                station_name = display['name']

        if not self._vc_is_connected(vc):
            return
        
        if getattr(vc, '__class__', None).__name__ == 'Player':
            channel_ref = vc.channel
            try:
                self.mode_mgr.get(guild.id).is_switching = True
                await vc.disconnect()  # Graceful disconnect
            except Exception as e:
                print(f"⚠️ Error disconnect Player: {e}")
                self.mode_mgr.get(guild.id).is_switching = False
            await asyncio.sleep(2.0)  # Beri waktu Discord voice gateway reset
            try:
                vc = await channel_ref.connect(timeout=30.0)
            except Exception as e:
                print(f"❌ Error reconnect VoiceClient: {e}")
                return

        # Stop current playback
        if vc.is_playing():
            vc.stop()
        
        FFMPEG_OPTS = {
            'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
            'options': '-vn'
        }
        
        if url:
            try:
                vc.play(discord.FFmpegPCMAudio(url, **FFMPEG_OPTS))
                print(f"📻 Memutar {station_name} di {guild.name}")
            except Exception as e:
                print(f"❌ Error memutar radio {station_name}: {e}")

    # ──── Dashboard Handlers (dipanggil dari DashboardView) ────

    async def handle_mode_switch(self, interaction: discord.Interaction, target_mode: str, view: DashboardView):
        """Handle switch mode dari dashboard button."""
        guild = interaction.guild
        state = self.mode_mgr.get(guild.id)

        if target_mode == "radio":
            # Switch ke Radio
            station_name = state.radio_name or (list(self.stations.keys())[0] if self.stations else "")
            station_url = state.radio_url or (list(self.stations.values())[0] if self.stations else "")
            self.mode_mgr.switch_to_radio(guild.id, station_name, station_url)
            self.mode_mgr.clear_queue(guild.id)

            # Update view
            view.mode = "radio"
            view._build_buttons()

            embed = build_dashboard_embed("radio", state)
            await interaction.response.edit_message(embed=embed, view=view)

            # Play radio stream
            vc = guild.voice_client
            if self._vc_is_connected(vc):
                await self.restart_stream(guild, force_url=station_url)

        elif target_mode == "music":
            # Switch ke Music
            self.mode_mgr.switch_to_music(guild.id)

            view.mode = "music"
            view._build_buttons()

            embed = build_dashboard_embed("music", state, self.mode_mgr.queue_size(guild.id))
            await interaction.response.edit_message(embed=embed, view=view)

            # Jika ada antrian atau history, mulai autoplay
            vc = guild.voice_client
            if self._vc_is_connected(vc):
                await self._vc_stop(vc)
                    
                music_cog = self.bot.get_cog('Music')
                if music_cog:
                    # Cari text channel
                    for tc in guild.text_channels:
                        if tc.permissions_for(guild.me).send_messages:
                            await music_cog.play_next(guild, tc)
                            break

    async def handle_station_switch(self, interaction: discord.Interaction, name: str, url: str, view):
        """Handle pilih stasiun radio dari dropdown."""
        guild_id = interaction.guild.id
        self.mode_mgr.switch_to_radio(guild_id, name, url)

        embed = build_dashboard_embed("radio", self.mode_mgr.get(guild_id))
        await interaction.response.edit_message(embed=embed, view=view)
        await self.restart_stream(interaction.guild, force_url=url)

    async def handle_play(self, interaction: discord.Interaction, view: DashboardView):
        """Handle tombol Play."""
        state = self.mode_mgr.get(interaction.guild.id)

        if state.is_radio():
            embed = build_dashboard_embed("radio", state)
            await interaction.response.edit_message(embed=embed, view=view)
            await self.restart_stream(interaction.guild)
        else:
            embed = build_dashboard_embed("music", state, self.mode_mgr.queue_size(interaction.guild.id))
            await interaction.response.edit_message(embed=embed, view=view)

            vc = interaction.guild.voice_client
            if vc and not self._vc_is_playing(vc):
                music_cog = self.bot.get_cog('Music')
                if music_cog:
                    for tc in interaction.guild.text_channels:
                        if tc.permissions_for(interaction.guild.me).send_messages:
                            await music_cog.play_next(interaction.guild, tc)
                            break

    async def handle_stop(self, interaction: discord.Interaction, view: DashboardView):
        """Handle tombol Stop."""
        vc = interaction.guild.voice_client
        if vc:
            await self._vc_stop(vc)
            self.mode_mgr.clear_queue(interaction.guild.id)

        state = self.mode_mgr.get(interaction.guild.id)
        embed = discord.Embed(
            title="📻 Dashboard" if state.is_radio() else "🎵 Dashboard",
            color=discord.Color.red()
        )
        embed.add_field(name="Status", value="**⏹️ Dihentikan**")
        embed.set_footer(text="Tekan ▶️ Play untuk lanjut")
        await interaction.response.edit_message(embed=embed, view=view)

    async def handle_refresh(self, interaction: discord.Interaction):
        """Handle tombol Refresh (Radio mode)."""
        if interaction.guild.voice_client:
            await self.restart_stream(interaction.guild)
            await interaction.response.send_message("🔄 Stream direfresh!", ephemeral=True)
        else:
            await interaction.response.send_message("❌ Bot tidak terhubung ke voice channel.", ephemeral=True)

    # ──── Legacy Handlers (backward compat) ────

    async def update_station(self, interaction, name, url, view):
        """Backward compat untuk RadioSelect callback."""
        await self.handle_station_switch(interaction, name, url, view)

    async def action_play(self, interaction, view):
        await self.handle_play(interaction, view)

    async def action_stop(self, interaction, view):
        await self.handle_stop(interaction, view)

    async def action_refresh(self, interaction):
        await self.handle_refresh(interaction)

    # ──── Commands ────

    @commands.command()
    async def addstation(self, ctx, nama: str, url: str):
        self.stations[nama] = url
        if save_stations(self.stations):
            await ctx.send(f"✅ Radio **{nama}** ditambahkan!")
        else:
            await ctx.send("❌ Gagal simpan file.")

    @commands.command()
    async def delstation(self, ctx, *, nama: str):
        if nama not in self.stations:
            return await ctx.send(f"⚠️ **{nama}** tidak ditemukan.")
        del self.stations[nama]
        if save_stations(self.stations):
            await ctx.send(f"🗑️ Radio **{nama}** dihapus.")
        else:
            await ctx.send("❌ Gagal update file.")

    @commands.command()
    async def liststation(self, ctx):
        if not self.stations:
            return await ctx.send("Radio kosong.")
        desc = "\n".join([f"• {k}" for k in self.stations.keys()])
        await ctx.send(embed=discord.Embed(title="Daftar Radio", description=desc, color=discord.Color.gold()))

    @commands.command()
    async def menu(self, ctx):
        """Tampilkan Dashboard unified."""
        state = self.mode_mgr.get(ctx.guild.id)

        # Init default radio jika belum
        if not state.radio_name and self.stations:
            state.radio_name = list(self.stations.keys())[0]
            state.radio_url = list(self.stations.values())[0]

        if getattr(state, 'last_dashboard_msg', None):
            try:
                await state.last_dashboard_msg.delete()
            except:
                pass

        mode = state.mode
        queue_size = self.mode_mgr.queue_size(ctx.guild.id)
        embed = build_dashboard_embed(mode, state, queue_size)
        view = DashboardView(self, self.stations, mode=mode)
        msg = await ctx.send(embed=embed, view=view)
        state.last_dashboard_msg = msg

    @commands.command(name='radio')
    async def switch_radio(self, ctx):
        """Force switch ke Radio mode."""
        state = self.mode_mgr.get(ctx.guild.id)
        station_name = state.radio_name or (list(self.stations.keys())[0] if self.stations else "")
        station_url = state.radio_url or (list(self.stations.values())[0] if self.stations else "")
        self.mode_mgr.switch_to_radio(ctx.guild.id, station_name, station_url)
        self.mode_mgr.clear_queue(ctx.guild.id)

        vc = ctx.voice_client
        if self._vc_is_connected(vc):
            await self.restart_stream(ctx.guild, force_url=station_url)

        embed = build_dashboard_embed("radio", state)
        await ctx.send("📻 Beralih ke mode Radio!", embed=embed)

    @commands.command()
    async def status(self, ctx):
        cpu_usage = psutil.cpu_percent()
        ram = psutil.virtual_memory()
        ping = round(self.bot.latency * 1000)

        state = self.mode_mgr.get(ctx.guild.id)
        mode_str = "📻 Radio" if state.is_radio() else "🎵 Music"
        queue_size = self.mode_mgr.queue_size(ctx.guild.id)

        embed = discord.Embed(title="📊 System Status", color=discord.Color.gold())
        embed.add_field(name="CPU", value=f"{cpu_usage}%", inline=True)
        embed.add_field(name="RAM", value=f"{ram.percent}%", inline=True)
        embed.add_field(name="Ping", value=f"{ping}ms", inline=True)
        embed.add_field(name="Mode", value=mode_str, inline=True)
        if state.is_music():
            embed.add_field(name="Antrian", value=f"{queue_size} lagu", inline=True)
        await ctx.send(embed=embed)

    @commands.command()
    async def help(self, ctx):
        embed = discord.Embed(title="📻 Bantuan Radio Bot", description="Prefix bot: `!pndq`", color=discord.Color.gold())
        embed.add_field(name="🎛️ **Dashboard**", value=(
            "`!pndq menu` : Dashboard (Radio + Music)\n"
            "`!pndq status` : Cek server & mode"
        ), inline=False)
        embed.add_field(name="📻 **Radio**", value=(
            "`!pndq radio` : Switch ke Radio mode\n"
            "`!pndq liststation` : Daftar stasiun\n"
            "`!pndq addstation \"Nama\" \"Link\"` : Tambah stasiun\n"
            "`!pndq delstation Nama` : Hapus stasiun"
        ), inline=False)
        embed.add_field(name="🎵 **Music Player**", value=(
            "`!pndq play <judul/link>` : Request lagu (auto switch ke Music)\n"
            "`!pndq skip` : Lewati lagu\n"
            "`!pndq stop` : Hentikan & bersihkan antrian\n"
            "`!pndq queue` : Lihat antrian\n"
            "`!pndq shuffle` : Acak antrian\n"
            "`!pndq lyrics <judul>` : Cari lirik lagu"
        ), inline=False)
        embed.add_field(name="💡 **Tips**", value=(
            "• Saat Music Mode, bot otomatis putar lagu rekomendasi (Autoplay)\n"
            "• Gunakan tombol 🎵 Request di Dashboard untuk request tanpa command\n"
            "• Klik 📻 Radio di Dashboard untuk kembali ke Radio"
        ), inline=False)
        await ctx.send(embed=embed)

    # ──── Voice Events ────

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if member.id != self.bot.user.id:
            return
        if before.channel is not None and after.channel is None:
            state = self.mode_mgr.get(member.guild.id)
            if getattr(state, 'is_switching', False):
                state.is_switching = False
                return

            print(f"⚠️ Bot terputus dari Voice Channel di {member.guild.name}!")
            music_cog = self.bot.get_cog('Music')
            if music_cog and hasattr(music_cog, '_autoplay_locks'):
                # Clear lock jika ada
                if member.guild.id in music_cog._autoplay_locks:
                    del music_cog._autoplay_locks[member.guild.id]

            # Reset ke radio mode
            if self.stations:
                default_name = list(self.stations.keys())[0]
                default_url = list(self.stations.values())[0]
                self.mode_mgr.switch_to_radio(member.guild.id, default_name, default_url)
                self.mode_mgr.clear_queue(member.guild.id)

            vc = member.guild.voice_client
            if self._vc_is_connected(vc):
                try:
                    await vc.disconnect(force=True)
                except Exception as e:
                    print(f"❌ Error saat disconnect paksa di {member.guild.name}: {e}")
            print(f"✅ Memori Voice di {member.guild.name} berhasil di-reset.")

    # ──── Background Loop ────

    @tasks.loop(seconds=60)
    async def check_radio_status(self):
        await self.bot.wait_until_ready()

        for channel_id in TARGET_CHANNEL_IDS:
            channel = self.bot.get_channel(channel_id)
            if not channel:
                continue
            guild = channel.guild
            vc = guild.voice_client

            # --- ANTI-ZOMBIE: Bersihkan koneksi yang bengong ---
            if vc and not self._vc_is_connected(vc):
                print(f"⚠️ [ANTI-ZOMBIE] Bot bengong di {guild.name}. Melakukan CPR...")
                try:
                    await vc.disconnect(force=True)
                    await asyncio.sleep(2)
                except Exception as e:
                    print(f"❌ [ANTI-ZOMBIE] Gagal disconnect zombie di {guild.name}: {e}")
                vc = None

            # --- ANTI-KUTU LONCAT: Jangan pindah jika sudah di target channel ---
            if self._vc_is_connected(vc):
                if vc.channel.id in TARGET_CHANNEL_IDS:
                    # Bot sudah di salah satu target channel, JANGAN pindah
                    continue
                else:
                    # Bot ada di channel yang bukan target, pindahkan ke target
                    try:
                        print(f"🔄 [ANTI-KUTU LONCAT] Bot di {vc.channel.name}, pindah ke {channel.name}...")
                        await vc.move_to(channel)
                        print(f"✅ Berhasil pindah ke {channel.name}")
                    except Exception as e:
                        print(f"❌ Gagal move_to {channel.name}: {e}")
                continue

            # --- Bot belum terhubung, coba join ---
            if not vc:
                try:
                    print(f"🔄 Mencoba bergabung ke {channel.name}...")
                    await channel.connect(timeout=20.0)
                    print(f"✅ Bot bergabung ke {channel.name}")
                    await asyncio.sleep(1)
                except Exception as e:
                    print(f"❌ CRITICAL ERROR Gagal Join Voice di {channel.name}: {repr(e)}")

        # --- Auto-play berdasarkan mode ---
        for vc in self.bot.voice_clients:
            if not self._vc_is_connected(vc) or self._vc_is_playing(vc):
                continue

            state = self.mode_mgr.get(vc.guild.id)

            if state.is_music():
                # Music mode — trigger autoplay via Music cog
                current = self.mode_mgr.get_current_track(vc.guild.id)
                if current:
                    continue  # Mungkin sedang buffering, jangan ganggu

                music_cog = self.bot.get_cog('Music')
                if music_cog:
                    # Cari text channel
                    for tc in vc.guild.text_channels:
                        if tc.permissions_for(vc.guild.me).send_messages:
                            print(f"🔄 [AUTO] Music autoplay trigger di {vc.guild.name}")
                            await music_cog.play_next(vc.guild, tc)
                            break
            else:
                # Radio mode — play stream
                display = self.mode_mgr.get_voice_state_display(vc.guild.id, self.stations)
                if "🎵" in display['name']:
                    continue

                if display['url']:
                    print(f"Auto-play Radio: {display['name']} di {vc.guild.name}")
                    try:
                        if vc.is_playing():
                            vc.stop()
                            
                        FFMPEG_OPTS = {
                            'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
                            'options': '-vn'
                        }
                        vc.play(discord.FFmpegPCMAudio(display['url'], **FFMPEG_OPTS))
                    except Exception as e:
                        print(f"❌ Error Auto-play Radio: {e}")


class _VoiceStateProxy:
    """
    Proxy dict-like untuk backward compat.
    Kode lama: radio_cog.voice_states[guild_id] = {name, url}
    """

    def __init__(self, mode_mgr, stations):
        self._mode_mgr = mode_mgr
        self._stations = stations

    def __getitem__(self, guild_id):
        return self._mode_mgr.get_voice_state_display(guild_id, self._stations)

    def __setitem__(self, guild_id, value):
        state = self._mode_mgr.get(guild_id)
        name = value.get('name', '')
        url = value.get('url', '')
        if '🎵' in name:
            self._mode_mgr.switch_to_music(guild_id)
            state.current_track = {
                'title': name.replace('🎵 ', ''),
                'url': url
            }
        else:
            self._mode_mgr.switch_to_radio(guild_id, name, url)

    def __contains__(self, guild_id):
        return guild_id in self._mode_mgr._states

    def get(self, guild_id, default=None):
        try:
            return self[guild_id]
        except:
            return default


async def setup(bot):
    await bot.add_cog(Radio(bot))