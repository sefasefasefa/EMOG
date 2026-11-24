"""Yerel Görsel Analiz Modülü (local_vision.py)

Görseli PIL ile teknik olarak inceler, pytesseract ile varsa üzerindeki
yazıları okur, ultralytics (yolov8n.pt) ile nesneleri tespit eder ve
text-only modelin kolayca yorumlayabileceği net bir metin çıktısı üretir.

İlk çalışmada yolov8n.pt ağırlık dosyası bir kez indirilir,
sonrasında tamamen çevrimdışı çalışır.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

from PIL import Image, ImageStat

try:
    import pytesseract  # type: ignore
except ImportError:  # pragma: no cover
    pytesseract = None

try:
    from ultralytics import YOLO  # type: ignore
except ImportError:  # pragma: no cover
    YOLO = None

# Tesseract OCR binary (sistemde kurulu olmalı; pytesseract yalnızca Python sarmalayıcısıdır).
TESSERACT_BIN = shutil.which("tesseract")
if pytesseract is not None and TESSERACT_BIN:
    try:
        pytesseract.pytesseract.tesseract_cmd = TESSERACT_BIN  # type: ignore[attr-defined]
    except Exception:
        pass


_YOLO_MODEL: Optional[Any] = None
_YOLO_LOAD_FAILED = False


def _get_yolo_model() -> Optional[Any]:
    global _YOLO_MODEL, _YOLO_LOAD_FAILED
    if YOLO is None or _YOLO_LOAD_FAILED:
        return None
    if _YOLO_MODEL is None:
        try:
            _YOLO_MODEL = YOLO("yolov8n.pt")
        except Exception:
            _YOLO_LOAD_FAILED = True
            return None
    return _YOLO_MODEL


def analyze_image_local(
    image_path: str,
    ocr_lang: str = "eng+tur",
    conf_threshold: float = 0.25,
) -> Dict[str, Any]:
    """Yerel görsel analizi çalıştırır ve özet metin döner.

    Döndürülen sözlük:
      - meta: dosya, format, mod, genişlik, yükseklik, mean_rgb
      - detections: [{label, confidence, bbox: [x1,y1,x2,y2]}]
      - ocr_text: görseldeki okunan metin
      - summary_text: text-only model için hazır olan net özet
    """
    path = Path(image_path)
    if not path.exists():
        raise FileNotFoundError(f"Görsel bulunamadı: {image_path}")

    with Image.open(path) as img:
        rgb_img = img.convert("RGB")
        width, height = img.size
        mean_rgb = [round(x, 1) for x in ImageStat.Stat(rgb_img).mean]

        meta: Dict[str, Any] = {
            "file": path.name,
            "format": img.format or "UNKNOWN",
            "mode": img.mode,
            "width": width,
            "height": height,
            "mean_rgb": mean_rgb,
        }

        # ---- OCR ----
        ocr_text = ""
        ocr_available = pytesseract is not None and TESSERACT_BIN is not None
        if ocr_available:
            try:
                raw_text = pytesseract.image_to_string(rgb_img, lang=ocr_lang)
                ocr_text = " ".join(raw_text.split()).strip()
            except Exception:
                try:
                    raw_text = pytesseract.image_to_string(rgb_img, lang="eng")
                    ocr_text = " ".join(raw_text.split()).strip()
                except Exception:
                    ocr_text = ""

        # ---- Nesne tespiti (YOLOv8n) ----
        detections: List[Dict[str, Any]] = []
        model = _get_yolo_model()
        yolo_available = model is not None
        if model is not None:
            try:
                results = model.predict(source=rgb_img, conf=conf_threshold, verbose=False)
                for r in results:
                    names = r.names
                    for box in r.boxes:
                        cls_id = int(box.cls[0].item())
                        conf = round(float(box.conf[0].item()), 2)
                        x1, y1, x2, y2 = [int(v) for v in box.xyxy[0].tolist()]
                        detections.append({
                            "label": names.get(cls_id, str(cls_id)),
                            "confidence": conf,
                            "bbox": [x1, y1, x2, y2],
                        })
            except Exception:
                detections = []

    # ---- Özet metin ----
    summary_lines: List[str] = [
        f"Dosya: {meta['file']} | Format: {meta['format']} | Mod: {meta['mode']} | "
        f"Boyut: {meta['width']}x{meta['height']} | Ort. RGB: {meta['mean_rgb']}"
    ]

    # Araç durumu bilgisi
    tool_info = []
    if yolo_available:
        tool_info.append("YOLOv8n: aktif")
    else:
        tool_info.append("YOLOv8n: yok (ultralytics yok veya model indirilemedi)")
    if ocr_available:
        tool_info.append("Tesseract OCR: aktif")
    else:
        hint = " (kurulum: winget install UB-Mannheim.TesseractOCR)"
        tool_info.append(f"Tesseract OCR: yok (tesseract-ocr binary eksik{hint})")
    summary_lines.append("Araçlar: " + " | ".join(tool_info))

    if detections:
        det_str = ", ".join(
            f"{d['label']} ({d['confidence']}, kutu={d['bbox']})" for d in detections
        )
        summary_lines.append(f"Tespit edilen nesneler: {det_str}")
    else:
        summary_lines.append("Tespit edilen nesne: yok (veya eşik altında 0.25)")

    if ocr_text:
        summary_lines.append(f"Görüntudeki metin (OCR): {ocr_text}")
    else:
        summary_lines.append("Görüntudeki metin (OCR): bulunamadı")

    return {
        "meta": meta,
        "detections": detections,
        "ocr_text": ocr_text,
        "summary_text": "\n".join(summary_lines),
    }


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Kullanim: python local_vision.py <gorsel_yolu>")
        raise SystemExit(1)
    result = analyze_image_local(sys.argv[1])
    print(result["summary_text"])
