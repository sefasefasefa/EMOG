"""Özellik anahtarı yönetim komutları."""
from django.core.management.base import BaseCommand

from audit.feature_store import (
    disabled_keys,
    enabled_keys,
    is_enabled,
    reset_all,
    seed_features,
    set_enabled,
)


class Command(BaseCommand):
    help = "EMOG özellik anahtarlarını yönetir"

    def add_arguments(self, parser):
        parser.add_argument("action", choices=["seed", "list", "on", "off", "reset", "status"])
        parser.add_argument("key", nargs="?", default=None)
        parser.add_argument("--yes", action="store_true")

    def handle(self, *args, **options):
        action = options["action"]
        key = options["key"]

        if action == "seed":
            created = seed_features()
            self.stdout.write(self.style.SUCCESS(f"{created} özellik eklendi."))

        elif action == "list":
            for group_key, group_label in __import__(
                "audit.features", fromlist=["FEATURE_GROUPS"]
            ).FEATURE_GROUPS:
                self.stdout.write(f"\n{group_label}")
                for item in __import__("audit.features", fromlist=["FEATURES"]).FEATURES:
                    if item["group"] != group_key:
                        continue
                    mark = "ON " if is_enabled(item["key"]) else "OFF"
                    self.stdout.write(f"  [{mark}] {item['key']:28} {item['label']}")

        elif action == "on":
            if not key:
                self.stderr.write("Özellik anahtarı gerekli.")
                return
            set_enabled(key, True)
            self.stdout.write(self.style.SUCCESS(f"{key} açıldı."))

        elif action == "off":
            if not key:
                self.stderr.write("Özellik anahtarı gerekli.")
                return
            flag = set_enabled(key, False)
            if flag.critical and not options["yes"]:
                self.stdout.write(self.style.WARNING(f"{key} KRİTİK bir özellikti."))

        elif action == "reset":
            changed = reset_all()
            self.stdout.write(self.style.SUCCESS(f"{changed} özellik sıfırlandı."))

        elif action == "status":
            enabled = enabled_keys()
            disabled = disabled_keys()
            self.stdout.write(f"Açık: {len(enabled)}")
            self.stdout.write(f"Kapalı: {len(disabled)}")
            if disabled:
                self.stdout.write("Kapalı olanlar: " + ", ".join(sorted(disabled)))