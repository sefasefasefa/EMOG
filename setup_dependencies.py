#!/usr/bin/env python3
"""
EMOG Dependency Installer
EMOG başlatılmadan önce gerekli görüntü/video araçlarını kontrol eder,
eksikse otomatik olarak kurar (ffmpeg, ffprobe, tesseract + Python paketleri).
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, Tuple


def run(cmd: List[str], **kwargs) -> subprocess.CompletedProcess:
    print("  $ " + " ".join(cmd))
    try:
        return subprocess.run(cmd, capture_output=True, text=True, **kwargs)
    except FileNotFoundError as exc:
        print("  ERROR: command not found: %s" % exc)
        class FakeResult:
            returncode = 127
            stdout = ""
            stderr = str(exc)
        return FakeResult()  # type: ignore[return-value]


def check_python_package(import_name: str) -> bool:
    try:
        __import__(import_name)
        return True
    except ImportError:
        return False


MIN_FREE_GB = 2.0


def enough_disk_space() -> bool:
    """Otomatik kurulum disk'i doldurmadan once durdur."""
    try:
        free = shutil.disk_usage(Path(__file__).resolve().parent).free
        return free >= MIN_FREE_GB * (1024 ** 3)
    except Exception:
        return True


def install_python_package(name: str, use_uv: bool) -> bool:
    if not enough_disk_space():
        print("  [SKIP] %s: disk alani yetersiz (<%.1f GB), otomatik kurulum atlandi"
              % (name, MIN_FREE_GB))
        return False
    print("Installing Python package: %s ..." % name)
    if use_uv and shutil.which("uv"):
        # Aktif Python'a kur (onceki --system global Python'a kuruyordu,
        # dogrulama ise .venv'de yapildigi için "import failed" oluyordu).
        cmd = ["uv", "pip", "install", "--python", sys.executable, name]
    else:
        cmd = [sys.executable, "-m", "pip", "install", name]
    result = run(cmd)
    if result.stdout.strip():
        print(result.stdout.strip()[-500:])
    if result.returncode != 0 and result.stderr.strip():
        print(result.stderr.strip()[-500:])
    return result.returncode == 0


def check_system_binary(name: str) -> bool:
    return shutil.which(name) is not None


def system() -> str:
    import platform
    return platform.system().lower()


def winget_available() -> bool:
    return shutil.which("winget") is not None


def apt_available() -> bool:
    return shutil.which("apt-get") is not None


def brew_available() -> bool:
    return shutil.which("brew") is not None


def add_to_path_windows(extra_paths: List[str]) -> None:
    """Append extra Windows install dirs to the current process PATH so tools
    installed by winget in this session can be found immediately."""
    current = os.environ.get("Path", "")
    user_path = os.environ.get("PATH", "")
    combined = current or user_path
    for p in extra_paths:
        if p and os.path.isdir(p) and p.lower() not in combined.lower():
            combined += ";" + p
    os.environ["Path"] = combined
    os.environ["PATH"] = combined


def install_system_tools() -> List[Tuple[str, str, bool]]:
    """Return list of (tool, description, installed_ok) for system binaries."""
    tools = [
        ("ffmpeg", "FFmpeg (video processing)"),
        ("ffprobe", "FFprobe (video metadata)"),
        ("tesseract", "Tesseract OCR (read text in images)"),
    ]
    results: List[Tuple[str, str, bool]] = []
    sys_platform = system()

    # First pass: report what's already present
    missing = []
    for tool, desc in tools:
        ok = check_system_binary(tool)
        results.append((tool, desc, ok))
        print("  [%s] %s - %s" % ("OK" if ok else "MISSING", tool, desc))
        if not ok:
            missing.append((tool, desc))

    if not missing:
        return results

    # Second pass: try to auto-install missing ones per platform
    print("\nAuto-installing missing system tools (%s)..." % sys_platform)
    if sys_platform == "windows":
        if not winget_available():
            print("  winget not found. Install App Installer or install tools manually.")
            return results
        winget_map = {
            "ffmpeg": "Gyan.FFmpeg",
            "ffprobe": "Gyan.FFmpeg",  # ships with the FFmpeg package
            "tesseract": "UB-Mannheim.TesseractOCR",
        }
        for tool, desc in missing:
            pkg = winget_map.get(tool)
            if not pkg:
                continue
            print("  winget install %s ..." % pkg)
            run(["winget", "install", "--id", pkg, "-e",
                 "--accept-source-agreements", "--accept-package-agreements"])
        # Refresh PATH so just-installed tools are usable in this session
        add_to_path_windows([
            os.path.join(os.environ.get("LOCALAPPDATA", ""),
                         "Microsoft", "WinGet", "Links"),
            r"C:\Program Files\Tesseract-OCR",
            os.path.join(os.environ.get("USERPROFILE", ""), ".local", "bin"),
        ])

    # Re-check after install attempt
    final: List[Tuple[str, str, bool]] = []
    for tool, desc in tools:
        ok = check_system_binary(tool)
        final.append((tool, desc, ok))
        print("  [%s] %s - %s" % ("OK" if ok else "STILL MISSING", tool, desc))
    return final


def ensure_python_packages(use_uv: bool) -> List[Tuple[str, bool]]:
    packages = [
        ("faster-whisper", "faster_whisper"),
        ("scenedetect[opencv]", "scenedetect"),
        ("ultralytics", "ultralytics"),
        ("opencv-python", "cv2"),
        ("Pillow", "PIL"),
        ("moviepy", "moviepy"),
        ("pytesseract", "pytesseract"),
        ("openai-whisper", "whisper"),
    ]
    results: List[Tuple[str, bool]] = []
    for pkg, import_name in packages:
        if check_python_package(import_name):
            print("  [OK] %s installed" % pkg)
            results.append((pkg, True))
        else:
            print("  [MISSING] %s auto-installing..." % pkg)
            ok = install_python_package(pkg, use_uv)
            results.append((pkg, ok))
            if not ok:
                print("  [ERROR] %s could not be installed, manual install required." % pkg)
    return results


def verify(use_uv: bool) -> bool:
    print("\n=== Verification ===")
    all_ok = True

    print("\nSystem binaries:")
    for tool in ("ffmpeg", "ffprobe", "tesseract"):
        if check_system_binary(tool):
            print("  [OK] %s found" % tool)
        else:
            print("  [MISSING] %s not found (open a new terminal after install)" % tool)
            all_ok = False

    print("\nPython packages:")
    mods = ["faster_whisper", "scenedetect", "ultralytics", "cv2",
            "PIL", "moviepy", "pytesseract", "whisper"]
    for m in mods:
        if check_python_package(m):
            print("  [OK] %s" % m)
        else:
            print("  [HATA] %s: import failed" % m)
            all_ok = False

    return all_ok


def ensure_emog_dependencies() -> bool:
    """Public entry point: check and install EMOG dependencies.
    Returns True if all critical dependencies are available."""
    print("\n" + "=" * 60)
    print("EMOG Dependency Check")
    print("=" * 60)

    use_uv = shutil.which("uv") is not None
    print("Package manager: %s" % ("uv" if use_uv else "pip"))

    print("\n[1/3] Checking Python packages...")
    pkg_results = ensure_python_packages(use_uv)

    print("\n[2/3] Checking/installing system tools...")
    tool_results = install_system_tools()

    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)

    missing_pkgs = [p for p, ok in pkg_results if not ok]
    missing_tools = [t for t, _, ok in tool_results if not ok]

    if missing_pkgs:
        print("\nPython packages NOT installed:")
        for p in missing_pkgs:
            print("  - %s" % p)
    if missing_tools:
        print("\nSystem tools NOT installed (manual install required):")
        for t in missing_tools:
            print("  - %s" % t)
        print("\nManual install commands:")
        if system() == "windows":
            print("  winget install Gyan.FFmpeg")
            print("  winget install UB-Mannheim.TesseractOCR")
        elif system() == "linux":
            print("  sudo apt update && sudo apt install ffmpeg tesseract-ocr")
        elif system() == "darwin":
            print("  brew install ffmpeg tesseract")

    all_ok = verify(use_uv)

    print("\nEMOG dependency check complete.")
    print("=" * 60)

    return all_ok


def main() -> int:
    ok = ensure_emog_dependencies()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())