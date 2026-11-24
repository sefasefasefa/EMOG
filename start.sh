#!/bin/bash
# EMOG - Dynamic waitress starter
# Sistem kaynaklarina gore thread sayisini otomatik belirler

# CPU cekirdek sayisini al
CPU_CORES=$(nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 4)

# RAM'i MB cinsinden al
if [ -f /proc/meminfo ]; then
    RAM_MB=$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo)
elif command -v sysctl >/dev/null 2>&1; then
    RAM_MB=$(sysctl -n hw.memsize 2>/dev/null | awk '{print int($1/1024/1024)}')
else
    RAM_MB=4096
fi

# Thread sayisini hesapla:
# - Her thread ~50MB RAM kullanir (Django + model cagrilari)
# - CPU core basina 2-4 thread (I/O bound isler icin)
# - Minimum 4, maximum 32 thread
THREADS_BY_CPU=$((CPU_CORES * 2))
THREADS_BY_RAM=$((RAM_MB / 50))

# Ikisinden kucugunu al
if [ $THREADS_BY_CPU -lt $THREADS_BY_RAM ]; then
    THREADS=$THREADS_BY_CPU
else
    THREADS=$THREADS_BY_RAM
fi

# Sinirlar
if [ $THREADS -lt 4 ]; then
    THREADS=4
fi
if [ $THREADS -gt 32 ]; then
    THREADS=32
fi

echo "========================================="
echo "EMOG - Waitress Server"
echo "========================================="
echo "CPU Cores: $CPU_CORES"
echo "RAM: ${RAM_MB}MB"
echo "Threads: $THREADS"
echo "Host: 0.0.0.0"
echo "Port: 8002"
echo "========================================="

# Python yolu: once sanal ortam, yoksa sistem python'u.
if [ -x ".venv/bin/python" ]; then
    PY=".venv/bin/python"
elif [ -x ".venv/Scripts/python.exe" ]; then
    PY=".venv/Scripts/python.exe"
else
    PY="python"
fi

# Bagimliliklari baslangicta bir kez kontrol et/eksikse kur (disk siniri var).
"$PY" setup_dependencies.py || true

# Eski sunucu process'lerini otomatik kapat (yarnin eski kodla calismasin).
# Once port 8002'yi dinleyenleri, sonra eski waitress/emog process'lerini oldur.
echo "Eski process'ler kontrol ediliyor..."
if command -v fuser >/dev/null 2>&1; then
    fuser -k 8002/tcp 2>/dev/null || true
elif command -v lsof >/dev/null 2>&1; then
    OLD_PIDS=$(lsof -ti tcp:8002 2>/dev/null || true)
    if [ -n "$OLD_PIDS" ]; then
        echo "$OLD_PIDS" | xargs -r kill -9 2>/dev/null || true
    fi
fi
# Waitress+emog proseslerini tamamen kapat (parent+child).
# Windows/Git Bash'ta pgrep yok; PowerShell kullan.
if command -v powershell.exe >/dev/null 2>&1; then
    powershell.exe -NoProfile -Command 'Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like "*emog_project.wsgi*" -and $_.ProcessId -ne $PID } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }' >/dev/null 2>&1 || true
elif command -v pgrep >/dev/null 2>&1; then
    OLD_PIDS=$(pgrep -f "waitress-serve.*emog_project.wsgi:application" 2>/dev/null || true)
    if [ -n "$OLD_PIDS" ]; then
        echo "$OLD_PIDS" | xargs -r kill -9 2>/dev/null || true
    fi
fi
sleep 2
echo "Eski process'ler kapatildi."

# .env dosyasi Django tarafindan python-dotenv ile yuklenir, burada gerek yok.
# (waitress-serve komutu PATH'te olmayabilir; "python -m waitress" kullaniliyor.)

# nginx /static/ → staticfiles/ alias kullanıyor; dosyaları her acilista topla.
"$PY" -X utf8 manage.py collectstatic --noinput >/dev/null 2>&1 || true

# Waitress'i baslat
exec "$PY" -m waitress --host=0.0.0.0 --port=8002 --threads=$THREADS emog_project.wsgi:application