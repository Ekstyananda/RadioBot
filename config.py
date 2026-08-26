import os
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')

raw_ids = os.getenv('TARGET_CHANNEL_ID')
if raw_ids:
    TARGET_CHANNEL_IDS = [int(x.strip()) for x in raw_ids.split(',')]
else:
    TARGET_CHANNEL_IDS = []

# URL ytm-api service (Docker internal network)
YTM_API_URL = os.getenv('YTM_API_URL', 'http://ytm-api:3000')

# Autoplay settings
AUTOPLAY_QUEUE_SIZE = 1    # Jumlah lagu rekomendasi yang di-fetch sekaligus
MAX_HISTORY_SIZE = 50      # Batas history per guild (untuk seed rekomendasi)

# Opsi FFmpeg (Baju Zirah Badak - Anti Error 404 & Anti Lag)
# - reconnect_on_network_error: auto-reconnect saat koneksi putus
# - reconnect_on_http_error 5xx: retry saat server error (500-599)
# - err_detect ignore_err: abaikan error minor di stream, jangan crash
# - bufsize 3000k: buffer besar untuk menahan jitter jaringan
FFMPEG_OPTS = {
    'before_options': (
        '-reconnect 1 '
        '-reconnect_streamed 1 '
        '-reconnect_delay_max 5 '
        '-reconnect_on_network_error 1 '
        '-reconnect_on_http_error 5xx '
        '-rw_timeout 15000000 '
    ),
    'options': '-vn -err_detect ignore_err -bufsize 3000k'
}