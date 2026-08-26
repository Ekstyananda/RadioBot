import json
import os

current_dir = os.path.dirname(os.path.abspath(__file__))
base_dir = os.path.dirname(current_dir)

DATA_DIR = os.path.join(base_dir, 'data')
STATIONS_FILE = os.path.join(DATA_DIR, 'stations.json')

def load_stations():
    if not os.path.exists(DATA_DIR):
        os.makedirs(DATA_DIR)
        
    if not os.path.exists(STATIONS_FILE):
        return {}
    
    try:
        with open(STATIONS_FILE, 'r') as f:
            return json.load(f)
    except Exception as e:
        print(f"❌ Gagal Memuat Data : {e}")
        return {}

def save_stations(data):
    try:
        if not os.path.exists(DATA_DIR):
            os.makedirs(DATA_DIR)

        with open(STATIONS_FILE, 'w') as f:
            json.dump(data, f, indent=4)
        return True
    except Exception as e:
        print(f"❌ Gagal Menyimpan Data : {e}")
        return False