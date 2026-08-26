"""
Music Cog — Pemutar musik dengan Autoplay Engine.

Fitur:
- !pndq play <query>   : Request lagu
- !pndq skip           : Lewati lagu
- !pndq stop           : Hentikan & bersihkan antrian
- !pndq lyrics <judul> : Cari lirik
- !pndq queue          : Lihat antrian
- !pndq shuffle        : Acak antrian

Autoplay:
- Saat antrian habis di Music Mode, otomatis fetch rekomendasi dari
  YouTube Music via /api/next berdasarkan lagu terakhir yang diputar.
- Fallback ke /api/home (trending) jika tidak ada history.
"""

import discord
from discord.ext import commands
import asyncio
import aiohttp
import wavelink
import urllib.parse
import random
import re

from config import FFMPEG_OPTS, YTM_API_URL, AUTOPLAY_QUEUE_SIZE
from views.dashboard_ui import build_dashboard_embed, DashboardView


class Music(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.ffmpeg_opts = FFMPEG_OPTS
        self._autoplay_locks = {}  # per-guild lock untuk mencegah race condition

    def _get_mode_mgr(self):
        """Ambil ModeManager dari Radio cog."""
        radio_cog = self.bot.get_cog('Radio')
        if radio_cog and hasattr(radio_cog, 'mode_mgr'):
            return radio_cog.mode_mgr
        return None

    def _get_lock(self, guild_id: int) -> asyncio.Lock:
        """Per-guild lock untuk autoplay."""
        if guild_id not in self._autoplay_locks:
            self._autoplay_locks[guild_id] = asyncio.Lock()
        return self._autoplay_locks[guild_id]

    # ──── Core Playback ────

    async def play_next(self, guild, channel):
        """Putar lagu berikutnya dari antrian, atau trigger autoplay."""
        mode_mgr = self._get_mode_mgr()
        if not mode_mgr:
            return

        vc = guild.voice_client
        if not vc or not (getattr(vc, 'connected', False) or getattr(vc, 'channel', None) is not None or (hasattr(vc, 'is_connected') and vc.is_connected())):
            return

        if getattr(vc, '__class__', None).__name__ != 'Player':
            channel_to_join = vc.channel
            try:
                if mode_mgr: mode_mgr.get(guild.id).is_switching = True
                await vc.disconnect()
            except Exception as e:
                print(f"⚠️ Error disconnect VoiceClient in play_next: {e}")
                if mode_mgr: mode_mgr.get(guild.id).is_switching = False
            await asyncio.sleep(2.0)
            try:
                vc = await channel_to_join.connect(cls=wavelink.Player, timeout=30.0)
            except Exception as e:
                print(f"❌ Gagal switch ke wavelink.Player di play_next: {e}")
                return

        state = mode_mgr.get(guild.id)

        # Jika mode radio, serahkan ke Radio cog
        if state.is_radio():
            radio_cog = self.bot.get_cog('Radio')
            if radio_cog:
                await radio_cog.restart_stream(guild)
            return

        # Ambil lagu berikutnya dari antrian
        track = mode_mgr.get_next(guild.id)

        if not track:
            # Antrian kosong → trigger autoplay
            print(f"🔄 [{guild.name}] Antrian kosong, triggering autoplay...")
            await self._autoplay(guild, channel)
            return

        await self._play_track(guild, channel, track)

    async def _play_track(self, guild, channel, track):
        """Play sebuah track dict menggunakan Wavelink."""
        mode_mgr = self._get_mode_mgr()
        vc = guild.voice_client
        if not vc:
            return
            
        # Set text channel for Wavelink events (store in vc context)
        if not hasattr(vc, 'bound_channel'):
            vc.bound_channel = channel

        title = track.get('title', 'Unknown')
        artist = track.get('artist', '')
        vid = track.get('videoId')
        
        # Cari dan putar track di Wavelink
        try:
            wl_track = None
            played = False
            
            # === TAHAP 1: Ekstrak direct stream URL menggunakan yt-dlp ===
            if vid:
                print(f"🔄 [{guild.name}] Mem-bypass IP block YouTube via yt-dlp...")
                try:
                    import yt_dlp
                    ydl_opts = {
                        'format': 'bestaudio/best',
                        'quiet': True,
                        'no_warnings': True,
                    }
                    loop = asyncio.get_event_loop()
                    def fetch_url():
                        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                            info = ydl.extract_info(f"https://www.youtube.com/watch?v={vid}", download=False)
                            return info.get('url')
                    
                    direct_url = await loop.run_in_executor(None, fetch_url)
                    if direct_url:
                        tracks = await wavelink.Playable.search(direct_url)
                        if tracks:
                            wl_track = tracks[0]
                            if mode_mgr:
                                mode_mgr.set_current_track(guild.id, track)
                            await vc.play(wl_track)
                            played = True
                            print(f"✅ [{guild.name}] Berhasil memutar dari direct URL yt-dlp!")
                except Exception as e:
                    print(f"⚠️ [{guild.name}] Gagal ekstrak via yt-dlp: {e}")
            
            # === TAHAP 2: Fallback ke SoundCloud jika yt-dlp gagal ===
            if not played:
                # Gunakan SoundCloud pencarian judul + artis
                search_query = f"{title} {artist}"
                print(f"🔄 [{guild.name}] Memutar via SoundCloud: {search_query}")
                try:
                    tracks = await wavelink.Playable.search(search_query, source=wavelink.TrackSource.SoundCloud)
                    if not tracks:
                        print(f"❌ [{guild.name}] Wavelink tidak menemukan track di SoundCloud: {search_query}")
                        await self.play_next(guild, channel)
                        return
                    wl_track = tracks[0]
                    
                    # Update metadata lagu agar konsisten dengan yang diputar dari SoundCloud
                    title = wl_track.title
                    artist = wl_track.author
                    track['title'] = title
                    track['artist'] = artist
                    if getattr(wl_track, 'artwork', None):
                        track['thumbnail'] = wl_track.artwork
                        
                    if mode_mgr:
                        mode_mgr.set_current_track(guild.id, track)
                    await vc.play(wl_track)
                except Exception as e:
                    print(f"❌ [{guild.name}] Gagal play SoundCloud: {e}")
                    await asyncio.sleep(1)
                    await self.play_next(guild, channel)
                    return
        except Exception as e:
            print(f"❌ [{guild.name}] Wavelink play error: {e}")
            await asyncio.sleep(1)
            await self.play_next(guild, channel)
            return

        # Kirim embed now playing
        queue_size = mode_mgr.queue_size(guild.id) if mode_mgr else 0
        embed = discord.Embed(
            title="🎧 Now Playing",
            color=discord.Color.purple()
        )
        embed.add_field(name="🎵 Lagu", value=f"**{title}**", inline=False)
        if artist:
            embed.add_field(name="🎤 Artis", value=artist, inline=True)
        if queue_size > 0:
            embed.add_field(name="⏭️ Antrian", value=f"{queue_size} lagu", inline=True)
        else:
            embed.add_field(name="🔄 Autoplay", value="ON", inline=True)

        thumbnail = track.get('thumbnail') or (wl_track.artwork if hasattr(wl_track, 'artwork') else None)
        if thumbnail:
            embed.set_thumbnail(url=thumbnail)
        embed.set_footer(text="🎵 Music Mode • Autoplay ON")

        try:
            msg = await channel.send(embed=embed)
            if mode_mgr:
                state = mode_mgr.get(guild.id)
                if getattr(state, 'last_now_playing_msg', None):
                    try:
                        await state.last_now_playing_msg.delete()
                    except:
                        pass
                state.last_now_playing_msg = msg
        except Exception as e:
            print(f"⚠️ Gagal kirim embed now playing: {e}")

    @commands.Cog.listener()
    async def on_wavelink_track_end(self, payload: wavelink.TrackEndEventPayload):
        """Wavelink Event: Track Selesai"""
        reason_str = str(getattr(payload, 'reason', '')).lower()
            
        print(f"DEBUG TrackEnd: reason={reason_str}")
        if "finished" in reason_str or "load_failed" in reason_str or "stopped" in reason_str:
            guild = payload.player.guild
            channel = getattr(payload.player, 'bound_channel', None)
            if not channel:
                # Cari sembarang channel text
                for tc in guild.text_channels:
                    if tc.permissions_for(guild.me).send_messages:
                        channel = tc
                        break
            
            if channel:
                # Jadwalkan play_next
                self.bot.loop.create_task(self.play_next(guild, channel))

    # ──── Autoplay Engine ────

    async def _autoplay(self, guild, channel):
        """Fetch rekomendasi dan isi antrian otomatis."""
        lock = self._get_lock(guild.id)

        # Cegah multiple autoplay sekaligus
        if lock.locked():
            return

        async with lock:
            mode_mgr = self._get_mode_mgr()
            if not mode_mgr:
                return

            state = mode_mgr.get(guild.id)
            if state.is_radio():
                return  # Jangan autoplay di radio mode

            last_vid = mode_mgr.get_last_video_id(guild.id)
            tracks = []

            if last_vid:
                # Strategi 1: /api/next berdasarkan lagu terakhir
                tracks = await self._fetch_recommendations(last_vid, guild.id)
            else:
                print(f"🔄 [{guild.name}] Menunggu user request lagu pertama, autoplay ditunda.")
                return

            if not tracks:
                # Strategi 2: Fallback ke trending dari /api/home
                print(f"🔄 [{guild.name}] Fallback ke trending songs...")
                tracks = await self._fetch_trending(guild.id)

            if not tracks:
                print(f"⚠️ [{guild.name}] Autoplay gagal total, switch ke Radio mode")
                radio_cog = self.bot.get_cog('Radio')
                if radio_cog:
                    mode_mgr.switch_to_radio(guild.id)
                    await radio_cog.restart_stream(guild)
                    try:
                        await channel.send("⚠️ Autoplay tidak menemukan lagu. Kembali ke mode Radio.")
                    except:
                        pass
                return

            # Masukkan ke antrian
            mode_mgr.add_bulk_to_queue(guild.id, tracks)
            print(f"✅ [{guild.name}] Autoplay: {len(tracks)} lagu ditambahkan ke antrian")

            # Putar yang pertama
            await self.play_next(guild, channel)

    async def _fetch_recommendations(self, video_id: str, guild_id: int) -> list:
        """Fetch rekomendasi dari /api/next."""
        try:
            api_url = f"{YTM_API_URL}/api/next?videoId={urllib.parse.quote(video_id)}"
            async with aiohttp.ClientSession() as session:
                async with session.get(api_url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    if resp.status != 200:
                        print(f"❌ /api/next returned {resp.status}")
                        return []
                    data = await resp.json()

            queue = data.get('queue', [])
            if not queue:
                return []

            mode_mgr = self._get_mode_mgr()
            tracks = []
            for item in queue:
                vid = item.get('videoId', '')
                if not vid:
                    continue
                # Skip lagu yang sudah pernah diputar
                if mode_mgr and mode_mgr.is_in_history(guild_id, vid):
                    continue
                # Skip lagu yang sedang diputar
                if item.get('selected'):
                    continue
                tracks.append({
                    'title': item.get('title', 'Unknown'),
                    'artist': item.get('artist', ''),
                    'videoId': vid,
                    'thumbnail': item.get('thumbnail'),
                    'duration': item.get('duration', ''),
                    'url': None  # Akan di-extract saat play
                })
                if len(tracks) >= AUTOPLAY_QUEUE_SIZE:
                    break

            return tracks

        except Exception as e:
            print(f"❌ Error fetch recommendations: {e}")
            return []

    async def _fetch_trending(self, guild_id: int) -> list:
        """Fetch lagu trending dari /api/search sebagai fallback."""
        try:
            api_url = f"{YTM_API_URL}/api/search?q=top+songs+2026&filter=songs"
            async with aiohttp.ClientSession() as session:
                async with session.get(api_url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    if resp.status != 200:
                        return []
                    data = await resp.json()

            sections = data.get('sections', [])
            if not sections:
                return []

            # Kumpulkan semua lagu dari semua section
            all_songs = []
            mode_mgr = self._get_mode_mgr()
            for section in sections:
                for item in section.get('items', []):
                    vid = item.get('videoId', '')
                    if not vid:
                        continue
                    if item.get('type') not in ('song', 'video', None):
                        continue
                    if mode_mgr and mode_mgr.is_in_history(guild_id, vid):
                        continue
                    all_songs.append({
                        'title': item.get('title', 'Unknown'),
                        'artist': item.get('subtitle', ''),
                        'videoId': vid,
                        'thumbnail': item.get('thumbnail'),
                        'duration': '',
                        'url': None
                    })

            if not all_songs:
                return []

            # Ambil random subset
            random.shuffle(all_songs)
            return all_songs[:AUTOPLAY_QUEUE_SIZE]

        except Exception as e:
            print(f"❌ Error fetch trending: {e}")
            return []


    # ──── Public API untuk Dashboard UI ────

    async def handle_play_request(self, guild, channel, user, query: str):
        """
        Dipanggil dari Dashboard modal atau command !play.
        Menangani join VC, search, dan play.
        """
        mode_mgr = self._get_mode_mgr()
        if not mode_mgr:
            return await channel.send("❌ System error: ModeManager tidak tersedia.")

        # Join VC jika belum, atau ganti jika dari radio mode (VoiceClient biasa)
        vc = guild.voice_client
        if vc and getattr(vc, '__class__', None).__name__ != 'Player':
            channel_to_join = vc.channel
            try:
                mode_mgr = self._get_mode_mgr()
                if mode_mgr: mode_mgr.get(guild.id).is_switching = True
                await vc.disconnect()  # Graceful disconnect
            except Exception as e:
                print(f"⚠️ Error disconnect VoiceClient: {e}")
                mode_mgr = self._get_mode_mgr()
                if mode_mgr: mode_mgr.get(guild.id).is_switching = False
            await asyncio.sleep(2.0)  # Beri waktu Discord voice gateway reset
            try:
                vc = await channel_to_join.connect(cls=wavelink.Player, timeout=30.0)
            except Exception as e:
                return await channel.send(f"❌ Gagal switch ke music mode: {e}")
            
        if not vc:
            if user.voice and user.voice.channel:
                try:
                    vc = await user.voice.channel.connect(cls=wavelink.Player, timeout=20.0)
                except Exception as e:
                    return await channel.send(f"❌ Gagal join voice channel: {e}")
            else:
                return await channel.send("❌ Kamu harus masuk voice channel dulu!")

        # Switch ke music mode
        state = mode_mgr.get(guild.id)
        if state.is_radio():
            mode_mgr.switch_to_music(guild.id)
            is_playing = getattr(vc, 'playing', False) or (hasattr(vc, 'is_playing') and getattr(vc, 'is_playing')())
            if is_playing:
                if getattr(vc, '__class__', None).__name__ == 'Player':
                    await vc.stop()
                else:
                    vc.stop()

        # Search via YTM API for queries, fallback to yt-dlp for URLs
        track = None
        if not query.startswith('http'):
            try:
                api_url = f"{YTM_API_URL}/api/search?q={urllib.parse.quote(query)}"
                async with aiohttp.ClientSession() as session:
                    async with session.get(api_url, timeout=10) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            # Find first valid song/video in sections
                            for section in data.get('sections', []):
                                for item in section.get('items', []):
                                    if item.get('videoId'):
                                        track = {
                                            'title': item.get('title', 'Unknown'),
                                            'artist': item.get('subtitle', ''),
                                            'videoId': item.get('videoId', ''),
                                            'thumbnail': item.get('thumbnail'),
                                            'duration': str(item.get('duration', '')),
                                            'url': None # url akan diambil yt-dlp di _play_track
                                        }
                                        break
                                if track: break
            except Exception as e:
                print(f"⚠️ YTM API Search error: {e}")

        # Jika bukan query biasa atau API gagal, fallback gunakan API resolve untuk URL
        if not track and query.startswith('http'):
            try:
                api_url = f"{YTM_API_URL}/api/resolve?url={urllib.parse.quote(query)}"
                async with aiohttp.ClientSession() as session:
                    async with session.get(api_url, timeout=10) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            if data.get('kind') == 'song' and data.get('videoId'):
                                track = {
                                    'title': 'Track from URL',
                                    'artist': 'Unknown',
                                    'videoId': data.get('videoId'),
                                    'thumbnail': None,
                                    'duration': '',
                                    'url': None
                                }
            except Exception as e:
                print(f"⚠️ YTM API Resolve error: {e}")

        if not track:
            return await channel.send(f"❌ Lagu tidak ditemukan atau URL tidak dikenali.")

        # Jika sedang playing, masukkan ke antrian
        if getattr(vc, 'playing', False):
            mode_mgr.add_to_queue(guild.id, track)
            queue_size = mode_mgr.queue_size(guild.id)
            await channel.send(f"✅ **{track['title']}** masuk antrian! (Posisi #{queue_size})")
        else:
            # Langsung putar
            is_playing = getattr(vc, 'playing', False) or (hasattr(vc, 'is_playing') and getattr(vc, 'is_playing')())
            if is_playing:
                if getattr(vc, '__class__', None).__name__ == 'Player':
                    await vc.stop()
                else:
                    vc.stop()
            mode_mgr.add_to_queue_front(guild.id, track)
            await self.play_next(guild, channel)

    async def fetch_lyrics_embed(self, title: str, artist: str = '') -> discord.Embed:
        """Fetch lirik dan return embed. Dipakai oleh command dan dashboard button."""
        query_parts = [title]
        if artist:
            query_parts.append(artist)
        search_query = ' '.join(query_parts)

        api_url = f"{YTM_API_URL}/api/lyrics?title={urllib.parse.quote(search_query)}"
        if artist:
            api_url += f"&artist={urllib.parse.quote(artist)}"

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(api_url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    if resp.status != 200:
                        return discord.Embed(
                            title=f"🎤 Lirik: {title}",
                            description=f"❌ API Error: HTTP {resp.status}",
                            color=discord.Color.red()
                        )
                    data = await resp.json()
        except Exception as e:
            return discord.Embed(
                title=f"🎤 Lirik: {title}",
                description=f"❌ Gagal menghubungi Lyrics API: {e}",
                color=discord.Color.red()
            )

        if 'error' in data:
            return discord.Embed(
                title=f"🎤 Lirik: {title}",
                description=f"❌ {data['error']}",
                color=discord.Color.red()
            )

        lyrics_text = data.get('plain') or data.get('synced')
        source = data.get('source', 'Unknown')

        if not lyrics_text:
            return discord.Embed(
                title=f"🎤 Lirik: {title}",
                description="😔 Lirik tidak ditemukan.",
                color=discord.Color.greyple()
            )

        # Bersihkan timestamp synced [mm:ss.xx]
        if not data.get('plain') and data.get('synced'):
            lyrics_text = re.sub(r'\[\d+:\d+\.\d+\]', '', lyrics_text).strip()

        # Discord embed max 4096 chars
        if len(lyrics_text) > 3900:
            lyrics_text = lyrics_text[:3900] + "\n\n... *(lirik terlalu panjang, dipotong)*"

        embed = discord.Embed(
            title=f"🎤 Lirik: {title}",
            description=lyrics_text,
            color=discord.Color.purple()
        )
        embed.set_footer(text=f"Sumber: {source} • via ytm-api")
        return embed

    # ──── Commands ────

    @commands.command(aliases=['p', 'req'])
    async def play(self, ctx, *, query: str):
        """Request lagu. Otomatis switch ke Music mode."""
        if not ctx.author.voice:
            return await ctx.send("❌ Masuk voice channel dulu!")

        msg = await ctx.send(f"🔎 Mencari **{query}**...")
        await self.handle_play_request(ctx.guild, ctx.channel, ctx.author, query)
        try:
            await msg.delete()
        except:
            pass

    @commands.command()
    async def skip(self, ctx):
        """Lewati lagu yang sedang diputar."""
        vc = ctx.voice_client
        if vc and getattr(vc, 'playing', False):
            await vc.stop()  # Triggers on_wavelink_track_end -> play_next
            await ctx.send("⏭️ Lagu dilewati!")
        else:
            await ctx.send("❌ Tidak ada lagu yang diputar.")

    @commands.command()
    async def stop(self, ctx):
        """Hentikan playback dan bersihkan antrian."""
        mode_mgr = self._get_mode_mgr()
        if mode_mgr:
            mode_mgr.clear_queue(ctx.guild.id)
        if ctx.voice_client:
            if getattr(ctx.voice_client, '__class__', None).__name__ == 'Player':
                await ctx.voice_client.stop()
            else:
                ctx.voice_client.stop()
        await ctx.message.add_reaction("⏹️")

    @commands.command()
    async def lyrics(self, ctx, *, judul: str):
        """Cari lirik lagu. Contoh: !pndq lyrics Bohemian Rhapsody"""
        msg = await ctx.send(f"🔎 Mencari lirik **{judul}**...")

        # Cek apakah ada lagu yang sedang diputar untuk info artis
        mode_mgr = self._get_mode_mgr()
        artist = ''
        if mode_mgr:
            track = mode_mgr.get_current_track(ctx.guild.id)
            if track and judul.lower() in track.get('title', '').lower():
                artist = track.get('artist', '')

        embed = await self.fetch_lyrics_embed(judul, artist)
        await msg.edit(content=None, embed=embed)

    @commands.command()
    async def queue(self, ctx):
        """Lihat antrian lagu."""
        mode_mgr = self._get_mode_mgr()
        if not mode_mgr:
            return await ctx.send("❌ System error.")

        queue = mode_mgr.peek_queue(ctx.guild.id)
        current = mode_mgr.get_current_track(ctx.guild.id)

        embed = discord.Embed(title="📋 Antrian Musik", color=discord.Color.blue())

        if current:
            embed.add_field(
                name="🎧 Sedang Diputar",
                value=f"**{current.get('title', 'Unknown')}**"
                      + (f" — {current['artist']}" if current.get('artist') else ''),
                inline=False
            )

        if queue:
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

            embed.add_field(name="⏭️ Selanjutnya", value="\n".join(lines), inline=False)
        else:
            embed.add_field(name="⏭️ Selanjutnya", value="Antrian kosong • Autoplay akan mengisi otomatis 🔄", inline=False)

        embed.set_footer(text=f"Total antrian: {len(queue)} lagu")
        await ctx.send(embed=embed)

    @commands.command()
    async def shuffle(self, ctx):
        """Acak urutan antrian."""
        mode_mgr = self._get_mode_mgr()
        if not mode_mgr:
            return await ctx.send("❌ System error.")

        mode_mgr.shuffle_queue(ctx.guild.id)
        size = mode_mgr.queue_size(ctx.guild.id)
        await ctx.send(f"🔀 Antrian diacak! ({size} lagu)")


async def setup(bot):
    await bot.add_cog(Music(bot))