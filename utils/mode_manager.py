"""
ModeManager — State manager per-guild untuk sistem dual-mode Radio/Music.

Menyimpan:
- Mode aktif (radio/music)
- Track yang sedang diputar
- Antrian lagu (Music mode)
- History lagu yang pernah diputar (untuk seed rekomendasi & anti-duplikat)
"""

import json
import os
import random
from collections import deque

from config import MAX_HISTORY_SIZE

# Path file persisten
current_dir = os.path.dirname(os.path.abspath(__file__))
base_dir = os.path.dirname(current_dir)
DATA_DIR = os.path.join(base_dir, 'data')
HISTORY_FILE = os.path.join(DATA_DIR, 'music_history.json')


class GuildState:
    """State untuk satu guild."""

    def __init__(self, guild_id: int):
        self.guild_id = guild_id
        self.mode = "radio"  # "radio" atau "music"

        # Radio state
        self.radio_name = ""
        self.radio_url = ""

        # Music state
        self.current_track = None  # {title, artist, videoId, thumbnail, duration, url}
        self.queue = deque()       # deque of track dicts
        self.history = []          # list of videoIds yang pernah diputar
        self.last_video_id = None  # seed untuk /api/next
        
        # Pesan-pesan untuk dihapus agar chat bersih
        self.last_dashboard_msg = None
        self.last_now_playing_msg = None
        
        self.is_switching = False  # Flag to prevent auto-reset when intentionally switching clients

    def is_radio(self):
        return self.mode == "radio"

    def is_music(self):
        return self.mode == "music"


class ModeManager:
    """Manager global untuk semua guild states."""

    def __init__(self):
        self._states = {}
        self._load_history()

    def get(self, guild_id: int) -> GuildState:
        """Ambil state guild, buat baru jika belum ada."""
        if guild_id not in self._states:
            self._states[guild_id] = GuildState(guild_id)
            # Restore history dari file jika ada
            saved = self._saved_history.get(str(guild_id), [])
            self._states[guild_id].history = saved[-MAX_HISTORY_SIZE:]
        return self._states[guild_id]

    # ──── Mode Switch ────

    def switch_to_radio(self, guild_id: int, station_name: str = "", station_url: str = ""):
        """Switch ke Radio mode."""
        state = self.get(guild_id)
        state.mode = "radio"
        state.current_track = None
        if station_name:
            state.radio_name = station_name
            state.radio_url = station_url
        print(f"📻 [{guild_id}] Mode switched to RADIO: {state.radio_name}")

    def switch_to_music(self, guild_id: int):
        """Switch ke Music mode."""
        state = self.get(guild_id)
        state.mode = "music"
        print(f"🎵 [{guild_id}] Mode switched to MUSIC")

    # ──── Queue Management ────

    def add_to_queue(self, guild_id: int, track: dict):
        """Tambah lagu ke antrian."""
        state = self.get(guild_id)
        state.queue.append(track)

    def add_to_queue_front(self, guild_id: int, track: dict):
        """Tambah lagu ke depan antrian (prioritas)."""
        state = self.get(guild_id)
        state.queue.appendleft(track)

    def add_bulk_to_queue(self, guild_id: int, tracks: list):
        """Tambah banyak lagu sekaligus ke antrian."""
        state = self.get(guild_id)
        state.queue.extend(tracks)

    def get_next(self, guild_id: int):
        """Ambil lagu berikutnya dari antrian. Return None jika kosong."""
        state = self.get(guild_id)
        if state.queue:
            return state.queue.popleft()
        return None

    def peek_queue(self, guild_id: int):
        """Lihat antrian tanpa menghapus."""
        return list(self.get(guild_id).queue)

    def clear_queue(self, guild_id: int):
        """Bersihkan antrian."""
        self.get(guild_id).queue.clear()

    def shuffle_queue(self, guild_id: int):
        """Acak urutan antrian."""
        state = self.get(guild_id)
        items = list(state.queue)
        random.shuffle(items)
        state.queue = deque(items)

    def queue_size(self, guild_id: int) -> int:
        return len(self.get(guild_id).queue)

    # ──── Now Playing ────

    def set_current_track(self, guild_id: int, track: dict):
        """Set lagu yang sedang diputar dan simpan ke history."""
        state = self.get(guild_id)
        state.current_track = track
        video_id = track.get('videoId')
        if video_id:
            state.last_video_id = video_id
            self.add_to_history(guild_id, video_id)

    def get_current_track(self, guild_id: int):
        return self.get(guild_id).current_track

    # ──── History ────

    def add_to_history(self, guild_id: int, video_id: str):
        """Tambah videoId ke history, otomatis trim jika melebihi batas."""
        state = self.get(guild_id)
        if video_id and video_id not in state.history:
            state.history.append(video_id)
            # Trim jika melebihi batas
            if len(state.history) > MAX_HISTORY_SIZE:
                state.history = state.history[-MAX_HISTORY_SIZE:]
            self._save_history()

    def is_in_history(self, guild_id: int, video_id: str) -> bool:
        """Cek apakah videoId sudah pernah diputar."""
        return video_id in self.get(guild_id).history

    def get_last_video_id(self, guild_id: int):
        """Ambil videoId terakhir yang diputar (seed untuk rekomendasi)."""
        return self.get(guild_id).last_video_id

    # ──── Persistence ────

    def _load_history(self):
        """Load history dari file JSON."""
        self._saved_history = {}
        if not os.path.exists(DATA_DIR):
            os.makedirs(DATA_DIR)
        if os.path.exists(HISTORY_FILE):
            try:
                with open(HISTORY_FILE, 'r') as f:
                    self._saved_history = json.load(f)
            except Exception as e:
                print(f"❌ Gagal load music_history.json: {e}")
                self._saved_history = {}

    def _save_history(self):
        """Simpan history ke file JSON."""
        try:
            if not os.path.exists(DATA_DIR):
                os.makedirs(DATA_DIR)
            # Kumpulkan semua history dari states
            data = {}
            for gid, state in self._states.items():
                if state.history:
                    data[str(gid)] = state.history[-MAX_HISTORY_SIZE:]
            # Merge dengan data tersimpan yang guild-nya belum di-load
            for gid, hist in self._saved_history.items():
                if gid not in data:
                    data[gid] = hist
            with open(HISTORY_FILE, 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            print(f"❌ Gagal simpan music_history.json: {e}")

    # ──── Legacy Compat ────

    def get_voice_state_display(self, guild_id: int, stations: dict) -> dict:
        """
        Return dict {'name': ..., 'url': ...} untuk backward compat
        dengan voice_states lama di radio.py.
        """
        state = self.get(guild_id)
        if state.is_music() and state.current_track:
            return {
                'name': f"🎵 {state.current_track.get('title', 'Unknown')}",
                'url': state.current_track.get('url', '')
            }
        elif state.radio_name:
            return {'name': state.radio_name, 'url': state.radio_url}
        elif stations:
            name = list(stations.keys())[0]
            url = list(stations.values())[0]
            state.radio_name = name
            state.radio_url = url
            return {'name': name, 'url': url}
        return {'name': 'No Radio', 'url': ''}
