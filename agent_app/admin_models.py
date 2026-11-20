"""
Admin paneli modelleri (özellik açma/kapatma, sistem ayarları ve admin işlem günlüğü).

Bu dosya agent_app.models içinden import edilir ki Django migration sistemi
modelleri görebilsin. Yeni bir veritabanı tablosu eklenmezse (tablolar migration
ile oluşturulur), mevcut dosya yapısı bozulmaz.
"""

from django.conf import settings
from django.db import models
from django.utils import timezone


class FeatureToggle(models.Model):
    """Uygulama düzeyinde özellik açma/kapatma anahtarları."""
    name = models.CharField(max_length=100, unique=True, help_text="Özellik anahtarı (örn. vision_llm, image_generation)")
    is_active = models.BooleanField(default=False, help_text="Özellik açık mı?")
    description = models.TextField(blank=True, help_text="Özellik açıklaması")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Feature Toggle"
        verbose_name_plural = "Feature Toggles"
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({'active' if self.is_active else 'inactive'})"


class SystemSetting(models.Model):
    """Genel sistem ayarları (anahtar-değer)."""
    key = models.CharField(max_length=100, unique=True, help_text="Ayar anahtarı (örn. maintenance_mode, default_provider)")
    value = models.TextField(blank=True, help_text="Ayar değeri")
    description = models.TextField(blank=True, help_text="Açıklama")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "System Setting"
        verbose_name_plural = "System Settings"
        ordering = ["key"]

    def __str__(self):
        return f"{self.key} = {self.value}"


class AdminAuditLog(models.Model):
    """Yönetici paneli işlem günlüğü."""
    admin_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="admin_audit_logs",
        help_text="İşlem yapan yönetici",
    )
    action = models.CharField(max_length=100, help_text="İşlem adı (örn. toggle_feature, set_setting, maintenance_on)")
    target = models.CharField(max_length=200, blank=True, help_text="Hedef (özellik adı veya ayar anahtarı)")
    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)
    ip = models.GenericIPAddressField(null=True, blank=True, help_text="İstek IP adresi")

    class Meta:
        verbose_name = "Admin Audit Log"
        verbose_name_plural = "Admin Audit Logs"
        ordering = ["-timestamp"]

    def __str__(self):
        user = self.admin_user.username if self.admin_user else "anonim"
        return f"{user} — {self.action} — {self.timestamp}"
