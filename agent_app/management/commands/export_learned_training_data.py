import json
import os
from pathlib import Path

from django.core.management.base import BaseCommand
from django.conf import settings


class Command(BaseCommand):
    help = "Export learned 3D/video knowledge as JSONL training datasets (fine-tune pipeline scaffold)."

    def add_arguments(self, parser):
        parser.add_argument("--output-dir", default="", help="Output directory (default: MEDIA_ROOT/training_exports)")
        parser.add_argument("--limit", type=int, default=1000, help="Max examples per dataset")
        parser.add_argument("--user-id", default="", help="Optional user id to filter")

    def handle(self, *args, **options):
        from agent_app.learned_models import export_entity_training_examples, export_video_training_examples

        out_dir = options.get("output_dir") or ""
        if not out_dir:
            media_root = getattr(settings, "MEDIA_ROOT", None) or (Path(settings.BASE_DIR) / "media")
            out_dir = str(Path(media_root) / "training_exports")
        out_path = Path(out_dir)
        out_path.mkdir(parents=True, exist_ok=True)

        user = None
        user_id = (options.get("user_id") or "").strip()
        if user_id:
            try:
                from agent_app.models import User
                user = User.objects.filter(id=user_id).first() or User.objects.filter(pk=user_id).first()
            except Exception:
                user = None

        limit = int(options.get("limit") or 1000)

        entity_examples = export_entity_training_examples(user=user, limit=limit)
        video_examples = export_video_training_examples(user=user, limit=limit)

        entity_file = out_path / "entity_3d_training.jsonl"
        video_file = out_path / "video_training.jsonl"

        with entity_file.open("w", encoding="utf-8") as f:
            for ex in entity_examples:
                f.write(json.dumps(ex, ensure_ascii=False) + "\n")
        with video_file.open("w", encoding="utf-8") as f:
            for ex in video_examples:
                f.write(json.dumps(ex, ensure_ascii=False) + "\n")

        self.stdout.write(self.style.SUCCESS(f"Exported {len(entity_examples)} entity examples -> {entity_file}"))
        self.stdout.write(self.style.SUCCESS(f"Exported {len(video_examples)} video examples -> {video_file}"))