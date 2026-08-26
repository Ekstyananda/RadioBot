# Gunakan Python 3.10 versi Slim (Ringan & Stabil)
FROM python:3.11-slim

# Set folder kerja
WORKDIR /app

# 1. Install System Dependencies
# - ffmpeg: Wajib buat muter radio
# - libopus0: Codec audio Discord
# - build-essential & libffi-dev: Wajib biar PyNaCl (suara) bisa terinstall tanpa error
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    ffmpeg \
    nodejs \
    libopus0 \
    build-essential \
    libffi-dev \
    libnacl-dev \
    python3-dev && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# 2. Copy Requirements & Install Library
# Kita copy file ini DULUAN supaya Docker bisa pakai Cache (biar build cepet)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
ENV PYTHONUNBUFFERED=1

# 3. Copy Sisanya (Kodingan Bot)
COPY . .

# 4. Jalankan Main System
CMD ["python", "main.py"]