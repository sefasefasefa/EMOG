"""Refresh token bakım görevi: süresi dolmuş kayıtları temizler."""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from agent_app.models import RefreshToken


class Command(BaseCommand):
    help = "Süresi dolmuş ve iptal edilmiş refresh token kayıtlarını temizler"

    def add_arguments(self, parser):
        parser.add_argument(
            "--days", type=int, default=7,
            help="Kaç günden eski kayıtlar silinsin (varsayılan 7)",
        )
        parser.add_argument(
            "--dry-run", action="store_true", help="Sadece say, silme"
        )

    def handle(self, *args, **options):
        cutoff = timezone.now() - timedelta(days=options["days"])

        expired = RefreshToken.objects.filter(expires_at__lt=cutoff)
        expired_count = expired.count()

        revoked_old = RefreshToken.objects.filter(
            is_revoked=True, revoked_at__lt=cutoff
        )
        revoked_count = revoked_old.count()

        total = expired_count + revoked_count

        if options["dry_run"]:
            self.stdout.write(
                f"[dry-run] {expired_count} süresi dolmuş, {revoked_count} eski iptal kaydı silinecek"
            )
            return

        deleted, _ = expired.delete()
        deleted2, _ = revoked_old.delete()

        self.stdout.write(
            self.style.SUCCESS(
                f"{deleted} süresi dolmuş, {deleted2} iptal edilmiş token temizlendi"
            )
        )

        active = RefreshToken.objects.filter(is_revoked=False).count()
        self.stdout.write(f"Aktif token sayısı: {active}")