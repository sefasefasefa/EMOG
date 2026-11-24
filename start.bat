@echo off
REM EMOG - Dynamic waitress starter (Windows)
REM Sistem kaynaklarina gore thread sayisini otomatik belirler

REM CPU cekirdek sayisini al
for /f "tokens=2 delims==" %%a in ('wmic cpu get NumberOfLogicalProcessors /value 2^>nul ^| findstr "="') do set CPU_CORES=%%a
if not defined CPU_CORES set CPU_CORES=4

REM RAM'i MB cinsinden al
for /f "tokens=2 delims==" %%a in ('wmic OS get TotalVisibleMemorySize /value 2^>nul ^| findstr "="') do set RAM_KB=%%a
if not defined RAM_KB set RAM_KB=4194304
set /a RAM_MB=%RAM_KB% / 1024

REM Thread sayisini hesapla
set /a THREADS_BY_CPU=%CPU_CORES% * 2
set /a THREADS_BY_RAM=%RAM_MB% / 50

REM Ikisinden kucugunu al
if %THREADS_BY_CPU% lss %THREADS_BY_RAM% (
    set THREADS=%THREADS_BY_CPU%
) else (
    set THREADS=%THREADS_BY_RAM%
)

REM Sinirlar
if %THREADS% lss 4 set THREADS=4
if %THREADS% gtr 32 set THREADS=32

echo =========================================
echo EMOG - Waitress Server
echo =========================================
echo CPU Cores: %CPU_CORES%
echo RAM: %RAM_MB%MB
echo Threads: %THREADS%
echo Host: 0.0.0.0
echo Port: 8002
echo =========================================

REM .env dosyasi Django tarafindan python-dotenv ile yuklenir, burada gerek yok.

REM Eski sunucu process'lerini otomatik kapat (eski kodla calismasin).
echo Eski process'ler kapatiliyor...
for /f "tokens=5" %%P in ('netstat -ano ^| findstr ":8002 " ^| findstr LISTENING') do (
    taskkill /PID %%P /F >nul 2>&1
)
for /f "tokens=1" %%P in ('wmic process where "CommandLine like '%%emog_project.wsgi:application%%'" get ProcessId /value 2^>nul ^| findstr "="') do (
    for /f "tokens=2 delims==" %%Q in ("%%P") do taskkill /PID %%Q /F >nul 2>&1
)
timeout /t 2 /nobreak >nul
echo Eski process'ler kapatildi.

REM Waitress'i baslat
uv run waitress-serve --host=0.0.0.0 --port=8002 --threads=%THREADS% emog_project.wsgi:application