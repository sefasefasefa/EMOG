"""
Tum projelerin guvenlik iskeletini denetler ve eksikleri tamamlar.

Kullanim:
    python manage.py project_scaffold            # denetle
    python manage.py project_scaffold --fix      # eksikleri ekle
    python manage.py project_scaffold --fix --path <proje>   # tek proje
"""
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Projelerde .gitignore / .env.example / SECURITY.md varligini denetler"

    def add_arguments(self, parser):
        parser.add_argument(
            "--fix", action="store_true",
            help="Eksik dosyalari olustur, .gitignore'a eksik kaliplari ekle",
        )
        parser.add_argument(
            "--path", default=None,
            help="Tek bir proje dizini (varsayilan: tum projeler)",
        )

    def handle(self, *args, **options):
        from agent_app.project_scaffold import (
            audit_project_security,
            ensure_project_security,
        )

        base = Path(options["path"] or (Path(settings.BASE_DIR) / "projects"))

        if not base.exists():
            self.stdout.write(self.style.WARNING(f"Dizin yok: {base}"))
            return

        targets = [base] if options["path"] else sorted(
            path for path in base.iterdir()
            if path.is_dir() and not path.name.startswith("_")
        )

        self.stdout.write("=" * 70)
        self.stdout.write("PROJE GUVENLIK ISKELETI DENETIMI")
        self.stdout.write("=" * 70)

        ok_count = 0
        problem_count = 0
        fixed_count = 0

        for project in targets:
            report = audit_project_security(project)

            if options["fix"]:
                result = ensure_project_security(project, force=True)
                if result["created"]:
                    fixed_count += len(result["created"])
                    self.stdout.write(
                        f"  [+] {project.name}: {', '.join(result['created'])} olusturuldu"
                    )
                report = audit_project_security(project)

            if report["ok"]:
                ok_count += 1
                self.stdout.write(self.style.SUCCESS(f"  [OK] {project.name}"))
            else:
                problem_count += 1
                self.stdout.write(self.style.ERROR(f"  [--] {project.name}"))
                for missing in report["missing"]:
                    self.stdout.write(self.style.WARNING(f"        eksik: {missing}"))
                for warning in report["warnings"]:
                    self.stdout.write(self.style.WARNING(f"        {warning}"))

        self.stdout.write("-" * 70)
        self.stdout.write(f"Uyumlu proje: {ok_count}")
        self.stdout.write(f"Sorunlu proje: {problem_count}")
        if options["fix"]:
            self.stdout.write(f"Olusturulan dosya: {fixed_count}")

        if problem_count:
            self.stdout.write(self.style.ERROR(
                "\nSONUC: Sorunlu projeler var. --fix ile duzeltin."
            ))
        else:
            self.stdout.write(self.style.SUCCESS(
                "\nSONUC: Tum projeler uyumlu."
            ))