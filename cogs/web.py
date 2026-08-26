import discord
from discord.ext import commands
from quart import Quart, render_template, jsonify, request
import psutil
import datetime
import os
import asyncio
import logging
from collections import deque

from hypercorn.config import Config
from hypercorn.asyncio import serve

current_dir = os.path.dirname(os.path.abspath(__file__))
template_dir = os.path.join(os.path.dirname(current_dir), 'templates')

app = Quart(__name__, template_folder=template_dir)

# Custom In-Memory Logger for Web Dashboard
class MemoryLogHandler(logging.Handler):
    def __init__(self, capacity=50):
        super().__init__()
        self.logs = deque(maxlen=capacity)
        
    def emit(self, record):
        log_entry = self.format(record)
        self.logs.append(log_entry)

memory_handler = MemoryLogHandler(capacity=50)
memory_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s', '%H:%M:%S'))
logging.getLogger().addHandler(memory_handler)
logging.getLogger().setLevel(logging.INFO)

# Override print to also log so we capture it in the dashboard
import builtins
original_print = builtins.print
def custom_print(*args, **kwargs):
    msg = " ".join(str(a) for a in args)
    logging.info(msg)
    original_print(*args, **kwargs)
builtins.print = custom_print

class WebDashboard(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.start_time = datetime.datetime.now()
        self.web_task = None

    async def cog_load(self):
        print("🚀 Memulai Web Server Task...")
        self.web_task = asyncio.create_task(self.run_web_server())

    def cog_unload(self):
        if self.web_task:
            print("🛑 Mematikan Web Server lama yak..")
            self.web_task.cancel()
            
        # Restore print
        builtins.print = original_print

    async def run_web_server(self):
        await self.bot.wait_until_ready()

        config = Config()
        config.bind = ["0.0.0.0:5000"]
        config.accesslog = None 
        config.errorlog = "-"  
        config.loglevel = "warning"
        
        print("🌐 Dashboard Online: http://localhost:5000")
        
        try:
            await serve(app, config)
        except asyncio.CancelledError:
            print("✅ Web Server berhasil dimatikan (Reloading).")
        except Exception as e:
            print(f"❌ Error Web Server: {e}")

    def get_temp(self):
        try:
            with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
                temp = int(f.read()) / 1000
            return round(temp, 1)
        except:
            return 0 

# --- ENDPOINT API ---
@app.route('/')
async def index():
    return await render_template('index.html')

@app.route('/api/stats')
async def api_stats():
    cog = bot_instance.get_cog('WebDashboard') 
    if not cog: return jsonify({})

    cpu = psutil.cpu_percent()
    ram = psutil.virtual_memory().percent
    temp = cog.get_temp()
    ping = round(cog.bot.latency * 1000)
    
    uptime = datetime.datetime.now() - cog.start_time
    uptime_str = str(uptime).split('.')[0]

    return jsonify({
        'cpu': cpu,
        'ram': ram,
        'temp': temp,
        'ping': ping,
        'uptime': uptime_str
    })

@app.route('/api/guilds')
async def api_guilds():
    radio_cog = bot_instance.get_cog('Radio')
    if not radio_cog:
        return jsonify([])
        
    mode_mgr = radio_cog.mode_mgr
    guilds_data = []
    
    for guild in bot_instance.guilds:
        state = mode_mgr.get(guild.id)
        now_playing = "Waiting..."
        status = "Idle"
        mode = state.mode
        
        vc = guild.voice_client
        # Aman untuk VoiceClient dan wavelink.Player
        is_connected = False
        if vc:
            if hasattr(vc, 'is_connected') and callable(vc.is_connected):
                is_connected = vc.is_connected()
            elif hasattr(vc, 'connected'):
                is_connected = bool(vc.connected)
            elif hasattr(vc, 'channel'):
                is_connected = vc.channel is not None
                
        if is_connected:
            # Cek playing aman untuk VoiceClient dan wavelink.Player
            is_playing = getattr(vc, 'playing', None)
            if is_playing is None:
                is_playing = vc.is_playing() if hasattr(vc, 'is_playing') else False
            if is_playing:
                status = "Playing"
            else:
                status = "Connected"
                
        artist = ''
        thumbnail = None

        if state.is_radio():
            now_playing = state.radio_name or "No Radio"
        elif state.is_music():
            if state.current_track:
                title = state.current_track.get('title', 'Unknown')
                artist = state.current_track.get('artist', '')
                thumbnail = state.current_track.get('thumbnail')
                now_playing = title
            else:
                now_playing = "No Music"

        # Serialize queue for web dashboard
        queue_data = []
        for t in list(state.queue)[:25]:
            queue_data.append({
                'title': t.get('title', 'Unknown'),
                'artist': t.get('artist', '')
            })

        guilds_data.append({
            'id': str(guild.id),
            'name': guild.name,
            'status': status,
            'mode': mode,
            'now_playing': now_playing,
            'artist': artist,
            'thumbnail': thumbnail,
            'queue': queue_data,
            'member_count': guild.member_count
        })
        
    return jsonify(guilds_data)

@app.route('/api/logs')
async def api_logs():
    # Return formatted logs
    logs = list(memory_handler.logs)
    return jsonify(logs)

@app.route('/api/stations', methods=['GET', 'POST'])
async def api_stations():
    radio_cog = bot_instance.get_cog('Radio') 
    if not radio_cog:
        return jsonify({})
        
    if request.method == 'POST':
        form = await request.form
        name = form.get('name')
        url = form.get('url')
        if name and url:
            radio_cog.stations[name] = url
            from utils.storage import save_stations
            save_stations(radio_cog.stations)
            print(f"✅ Radio ditambahkan via web: {name}")
        return jsonify({'status': 'success'})

    return jsonify(radio_cog.stations)

@app.route('/api/stations/<name>', methods=['DELETE'])
async def api_delete_station(name):
    radio_cog = bot_instance.get_cog('Radio')
    if not radio_cog:
        return jsonify({'status': 'error', 'message': 'Radio cog not loaded'})
    
    if name not in radio_cog.stations:
        return jsonify({'status': 'error', 'message': f'Station "{name}" tidak ditemukan'})
    
    del radio_cog.stations[name]
    from utils.storage import save_stations
    save_stations(radio_cog.stations)
    print(f"🗑️ Radio dihapus via web: {name}")
    return jsonify({'status': 'success', 'message': f'Radio "{name}" dihapus'})

@app.route('/api/control/play/<name>')
async def api_play(name):
    radio_cog = bot_instance.get_cog('Radio')
    if not radio_cog:
        return jsonify({'status': 'error', 'message': 'Radio cog not loaded'})
        
    guild_id = request.args.get('guild_id')
    if not guild_id:
        return jsonify({'status': 'error', 'message': 'Missing guild_id'})
        
    guild_id = int(guild_id)
    guild = bot_instance.get_guild(guild_id)
    if not guild:
        return jsonify({'status': 'error', 'message': 'Guild not found'})

    if name in radio_cog.stations:
        url = radio_cog.stations[name]
        mode_mgr = radio_cog.mode_mgr
        
        # Switch ke radio mode
        mode_mgr.switch_to_radio(guild_id, name, url)
        
        # Restart stream
        vc = guild.voice_client
        if vc and vc.is_connected():
            await radio_cog.restart_stream(guild, force_url=url)
            
        print(f"📻 Web Control: Berpindah ke radio {name} di server {guild.name}")
        return jsonify({'status': 'playing', 'message': f'Memutar radio {name}', 'station': name})
        
    return jsonify({'status': 'error', 'message': 'Station not found'})

@app.route('/api/control/stop')
async def api_stop():
    guild_id = request.args.get('guild_id')
    if not guild_id:
        return jsonify({'status': 'error', 'message': 'Missing guild_id'})
        
    guild_id = int(guild_id)
    guild = bot_instance.get_guild(guild_id)
    if not guild:
        return jsonify({'status': 'error', 'message': 'Guild not found'})

    radio_cog = bot_instance.get_cog('Radio')
    if radio_cog:
        radio_cog.mode_mgr.clear_queue(guild_id)
        
    vc = guild.voice_client
    if vc:
        if getattr(vc, '__class__', None).__name__ == 'Player':
            await vc.stop()
        else:
            vc.stop()
        
    print(f"⏹️ Web Control: Playback dihentikan di server {guild.name}")
    return jsonify({'status': 'stopped', 'message': 'Playback dihentikan'})

@app.route('/api/control/skip', methods=['POST'])
async def api_skip():
    guild_id = request.args.get('guild_id')
    if not guild_id:
        return jsonify({'status': 'error', 'message': 'Missing guild_id'})
        
    guild_id = int(guild_id)
    guild = bot_instance.get_guild(guild_id)
    if not guild:
        return jsonify({'status': 'error', 'message': 'Guild not found'})
        
    vc = guild.voice_client
    is_playing = getattr(vc, 'playing', False) or (hasattr(vc, 'is_playing') and vc.is_playing())
    if vc and is_playing:
        if getattr(vc, '__class__', None).__name__ == 'Player':
            await vc.stop()
        else:
            vc.stop() # Triggers after_playing
        print(f"⏭️ Web Control: Skip lagu di server {guild.name}")
        return jsonify({'status': 'skipped', 'message': 'Lagu dilewati'})
        
    return jsonify({'status': 'error', 'message': 'Tidak ada yang diputar'})

@app.route('/api/control/request', methods=['POST'])
async def api_request():
    guild_id = request.args.get('guild_id')
    if not guild_id:
        return jsonify({'status': 'error', 'message': 'Missing guild_id'})
        
    guild_id = int(guild_id)
    guild = bot_instance.get_guild(guild_id)
    if not guild:
        return jsonify({'status': 'error', 'message': 'Guild not found'})
        
    data = await request.json
    query = data.get('query')
    if not query:
        return jsonify({'status': 'error', 'message': 'Missing query'})
        
    music_cog = bot_instance.get_cog('Music')
    if not music_cog:
        return jsonify({'status': 'error', 'message': 'Music cog not loaded'})
        
    # Trigger play mechanism (mocking channel and user if needed, or passing None)
    # We need a text channel to send messages to
    channel = None
    for tc in guild.text_channels:
        if tc.permissions_for(guild.me).send_messages:
            channel = tc
            break
            
    if not channel:
        return jsonify({'status': 'error', 'message': 'No text channel found to send messages'})
        
    print(f"🎵 Web Control: Request lagu '{query}' di server {guild.name}")
    
    # Run the request as a background task so it doesn't block the API response
    asyncio.create_task(
        music_cog.handle_play_request(guild, channel, guild.me, query)
    )
    
    return jsonify({'status': 'success', 'message': f'Request "{query}" ditambahkan'})

@app.route('/api/control/shuffle', methods=['POST'])
async def api_shuffle():
    guild_id = request.args.get('guild_id')
    if not guild_id:
        return jsonify({'status': 'error', 'message': 'Missing guild_id'})
        
    guild_id = int(guild_id)
    radio_cog = bot_instance.get_cog('Radio')
    if not radio_cog:
        return jsonify({'status': 'error', 'message': 'Radio cog not loaded'})
    
    radio_cog.mode_mgr.shuffle_queue(guild_id)
    size = radio_cog.mode_mgr.queue_size(guild_id)
    print(f"🔀 Web Control: Antrian diacak di guild {guild_id}")
    return jsonify({'status': 'success', 'message': f'Antrian diacak ({size} lagu)'})

@app.route('/api/control/mode/<target_mode>', methods=['POST'])
async def api_switch_mode(target_mode):
    guild_id = request.args.get('guild_id')
    if not guild_id:
        return jsonify({'status': 'error', 'message': 'Missing guild_id'})
        
    guild_id = int(guild_id)
    guild = bot_instance.get_guild(guild_id)
    if not guild:
        return jsonify({'status': 'error', 'message': 'Guild not found'})
    
    radio_cog = bot_instance.get_cog('Radio')
    if not radio_cog:
        return jsonify({'status': 'error', 'message': 'Radio cog not loaded'})
    
    mode_mgr = radio_cog.mode_mgr
    
    if target_mode == 'radio':
        state = mode_mgr.get(guild_id)
        station_name = state.radio_name or (list(radio_cog.stations.keys())[0] if radio_cog.stations else '')
        station_url = state.radio_url or (list(radio_cog.stations.values())[0] if radio_cog.stations else '')
        mode_mgr.switch_to_radio(guild_id, station_name, station_url)
        mode_mgr.clear_queue(guild_id)
        
        vc = guild.voice_client
        if vc and radio_cog._vc_is_connected(vc):
            await radio_cog.restart_stream(guild, force_url=station_url)
        
        print(f"📻 Web Control: Switch ke Radio mode di {guild.name}")
        return jsonify({'status': 'success', 'message': 'Beralih ke Radio mode'})
        
    elif target_mode == 'music':
        mode_mgr.switch_to_music(guild_id)
        print(f"🎵 Web Control: Switch ke Music mode di {guild.name}")
        return jsonify({'status': 'success', 'message': 'Beralih ke Music mode'})
    
    return jsonify({'status': 'error', 'message': f'Mode "{target_mode}" tidak dikenali'})

bot_instance = None

async def setup(bot):
    global bot_instance
    bot_instance = bot
    await bot.add_cog(WebDashboard(bot))