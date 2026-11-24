"""
2FA (TOTP) yönetimi.

Kullanım:
    python manage.py admin_totp enroll     -> cihaz oluşturur, QR + secret gösterir
    python manage.py admin_totp show       -> mevcut cihazları listeler
    python manage.py admin_totp verify     -> üretilen anlık kodu doğrular (kontrol)
    python manage.py admin_totp reset      -> cihazları siler (YENİDEN KAYIT gerekir)
"""
import hashlib
import hmac
import struct
import sys
import time

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django_otp.plugins.otp_totp.models import TOTPDevice


def current_totp(device):
    """Cihazın şu an geçerli olan TOTP kodunu üretir (saf stdlib)."""
    counter = int(time.time() // device.step) + (device.t0 // device.step)
    key = device.bin_key
    msg = struct.pack(">Q", counter)
    digest = hmac.new(key, msg, hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(code % (10 ** device.digits)).zfill(device.digits)


class Command(BaseCommand):
    help = "Admin 2FA (TOTP) cihaz yönetimi"

    def add_arguments(self, parser):
        parser.add_argument(
            "action",
            choices=["enroll", "show", "verify", "reset"],
            help="Yapılacak işlem",
        )
        parser.add_argument("--email", default=None, help="Kullanıcı e-postası")

    def handle(self, *args, **options):
        from django.conf import settings

        email = (options["email"] or getattr(settings, "SUPERUSER_EMAIL", "")).strip()
        User = get_user_model()

        try:
            user = User.objects.get(email__iexact=email)
        except User.DoesNotExist:
            raise CommandError(f"Kullanıcı bulunamadı: {email}")
        except User.MultipleObjectsReturned:
            raise CommandError(f"Birden fazla kullanıcı var: {email}")

        action = options["action"]

        if action == "show":
            self._show(user)
        elif action == "enroll":
            self._enroll(user)
        elif action == "verify":
            self._verify(user)
        elif action == "reset":
            deleted, _ = TOTPDevice.objects.filter(user=user).delete()
            self.stdout.write(self.style.SUCCESS(f"{deleted} cihaz silindi."))

    def _show(self, user):
        devices = TOTPDevice.objects.filter(user=user)
        if not devices:
            self.stdout.write(self.style.WARNING("Kayıtlı cihaz yok."))
            return
        for device in devices:
            self.stdout.write(
                f"- id={device.pk} name={device.name} confirmed={device.confirmed} t0={device.t0}"
            )

    def _enroll(self, user):
        TOTPDevice.objects.filter(user=user).delete()
        device = TOTPDevice.objects.create(
            user=user, name="EMOG Admin", confirmed=True, t0=0
        )
        config_url = device.config_url
        secret = ""
        if "secret=" in config_url:
            secret = config_url.split("secret=")[1].split("&")[0]
        self.stdout.write(self.style.SUCCESS("2FA cihazi olusturuldu."))
        self.stdout.write(f"Kurulum anahtari (secret): {secret}")
        self.stdout.write(f"Kurulum adresi (otpauth URI): {config_url}")
        self.stdout.write(f"Anlik gecerli kod: {current_totp(device)}")
        self._print_qr(device)

    def _verify(self, user):
        devices = list(TOTPDevice.objects.filter(user=user, confirmed=True))
        if not devices:
            self.stdout.write(self.style.WARNING("Doğrulanacak cihaz yok."))
            return
        code = current_totp(devices[0])
        self.stdout.write(f"Anlik gecerli kod: {code}")
        token = input("Kodu gir (dogrulamak icin): ").strip()
        if any(d.verify_token(token) for d in devices):
            self.stdout.write(self.style.SUCCESS("Kod dogrulandi."))
        else:
            self.stdout.write(self.style.ERROR("Kod gecersiz."))

    def _print_qr(self, device):
        try:
            import qrcode
        except ImportError:
            self.stdout.write(self.style.WARNING("qrcode kurulu değil, QR atlandı."))
            return
        try:
            img = qrcode.make(device.config_url)
            import os

            path = os.path.join(os.getcwd(), "admin-2fa-qr.png")
            img.save(path)
            self.stdout.write(self.style.SUCCESS(f"QR kaydedildi: {path}"))
        except Exception as exc:
            self.stdout.write(self.style.WARNING(f"QR üretilemedi: {exc}"))
