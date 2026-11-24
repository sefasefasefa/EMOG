"""Zamanı gelen NEURAL CORE otonom hedeflerini çalıştırır."""
from django.core.management.base import BaseCommand

from agent_app.neural_core.kernel import run_goal_cycle


class Command(BaseCommand):
    help = "NEURAL CORE otonom hedeflerini çalıştırır"

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=None)
        parser.add_argument("--dry-run", action="store_true", dest="dry_run")

    def handle(self, *args, **options):
        report = run_goal_cycle(limit=options.get("limit"), dry_run=options.get("dry_run", False))
        self.stdout.write(
            self.style.SUCCESS(
                f"NEURAL CORE döngüsü: {report['ran']} çalıştı, "
                f"{report['failed']} hata, {report['skipped']} atlandı."
            )
        )
