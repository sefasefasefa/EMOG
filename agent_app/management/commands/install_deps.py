#!/usr/bin/env python3
"""Otomatik bagimlik yukleyici: python manage.py install_deps"""
from django.core.management.base import BaseCommand
import subprocess
import sys
import shutil


def run(cmd):
    print("  $ %s" % " ".join(cmd) if isinstance(cmd, list) else "  $ %s" % cmd)
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.stdout.strip():
        print(result.stdout.strip()[:500])
    if result.returncode != 0 and result.stderr.strip():
        print(result.stderr.strip()[:500])
    return result.returncode == 0


class Command(BaseCommand):
    help = "EGOG icin butun goruntu/video analiz araclarini otomatik yukler"

    def handle(self, *args, **options):
        print("=" * 60)
        print("EGOG Bagimlilik Yukleyici")
        print("=" * 60)

        # Python paketleri
        packages = [
            ("pytesseract", "pytesseract"),
            ("Pillow", "PIL"),
            ("opencv-python", "cv2"),
            ("ultralytics", "ultralytics"),
            ("transformers", "transformers"),
            ("paddleocr", "paddleocr"),
        ]
        print("\n[1/3] Python paketleri kontrol ediliyor...")
        for pkg, imp in packages:
            try:
                __import__(imp)
                print("  [OK] %s" % pkg)
            except ImportError:
                print("  [YUKLENIYOR] %s ..." % pkg)
                run([sys.executable, "-m", "pip", "install", pkg])

        # Sistem araclari
        tools = [
            ("ffmpeg", "FFmpeg"),
            ("ffprobe", "FFprobe"),
            ("tesseract", "Tesseract OCR"),
            ("magick", "ImageMagick"),
        ]
        print("\n[2/3] Sistem araclari kontrol ediliyor...")
        missing = []
        for tool, desc in tools:
            if shutil.which(tool):
                print("  [OK] %s (%s)" % (tool, desc))
            else:
                print("  [EKSIK] %s (%s)" % (tool, desc))
                missing.append(tool)

        print("\n" + "=" * 60)
        if missing:
            print("Sistem araclari eksik. Manuel yukleyin:")
            for m in missing:
                print("  winget install %s" % m)
        else:
            print("Tum sistem araclari yuklu!")
        print("=" * 60)
