from django.db import models
from django.conf import settings
from django.utils.translation import gettext_lazy as _
from simple_history.models import HistoricalRecords
import uuid


class AuditLog(models.Model):
    """Tüm sistem eylemleri için merkezi denetim kaydı."""
    
    class Action(models.TextChoices):
        CREATE = "create", _("Oluşturma")
        UPDATE = "update", _("Güncelleme")
        DELETE = "delete", _("Silme")
        LOGIN = "login", _("Giriş")
        LOGOUT = "logout", _("Çıkış")
        LOGIN_FAILED = "login_failed", _("Başarısız Giriş")
        PASSWORD_CHANGE = "password_change", _("Şifre Değişikliği")
        ROLE_CHANGE = "role_change", _("Rol Değişikliği")
        PERMISSION_CHANGE = "permission_change", _("İzin Değişikliği")
        EXPORT = "export", _("Dışa Aktarma")
        IMPORT = "import", _("İçe Aktarma")
        API_CALL = "api_call", _("API Çağrısı")
        SOFT_DELETE = "soft_delete", _("Yumuşak Silme")
        RESTORE = "restore", _("Geri Yükleme")
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    
    # Kim
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="audit_logs",
        verbose_name=_("kullanıcı")
    )
    user_email = models.EmailField(_("kullanıcı e-postası"), blank=True)  # User silinirse bile kalsın
    
    # Ne
    action = models.CharField(_("eylem"), max_length=30, choices=Action.choices, db_index=True)
    model_name = models.CharField(_("model adı"), max_length=100, db_index=True)
    object_id = models.CharField(_("nesne ID"), max_length=100, db_index=True)
    object_repr = models.CharField(_("nesne gösterimi"), max_length=200, blank=True)
    
    # Ne değişti
    changes = models.JSONField(_("değişiklikler"), default=dict, blank=True)
    # Örn: {"field": {"old": "eski", "new": "yeni"}}
    
    # Nereden
    ip_address = models.GenericIPAddressField(_("IP adresi"), null=True, blank=True)
    user_agent = models.TextField(_("kullanıcı ajanı"), blank=True)
    referer = models.URLField(_("referrer"), max_length=500, blank=True)
    
    # API detayları
    request_method = models.CharField(_("HTTP metodu"), max_length=10, blank=True)
    request_path = models.CharField(_("yol"), max_length=500, blank=True)
    request_body = models.JSONField(_("istek gövdesi"), default=dict, blank=True)
    response_status = models.PositiveSmallIntegerField(_("yanıt kodu"), null=True, blank=True)
    response_time_ms = models.PositiveIntegerField(_("yanıt süresi (ms)"), null=True, blank=True)
    is_api = models.BooleanField(_("API isteği"), default=False, db_index=True)

    # Ziyaretçi / cihaz bilgisi
    session_key = models.CharField(_("oturum anahtarı"), max_length=64, blank=True, db_index=True)
    country = models.CharField(_("ülke"), max_length=8, blank=True, db_index=True)
    language = models.CharField(_("dil"), max_length=16, blank=True, db_index=True)
    device = models.CharField(_("cihaz"), max_length=32, blank=True, db_index=True)
    screen_width = models.PositiveIntegerField(_("ekran genişliği"), null=True, blank=True)
    
    # AI / Otomasyon
    ai_model = models.CharField(_("AI modeli"), max_length=50, blank=True)
    ai_prompt_tokens = models.PositiveIntegerField(_("prompt token"), null=True, blank=True)
    ai_completion_tokens = models.PositiveIntegerField(_("completion token"), null=True, blank=True)
    ai_cost_usd = models.DecimalField(_("maliyet (USD)"), max_digits=10, decimal_places=6, null=True, blank=True)
    
    # Meta
    created_at = models.DateTimeField(_("oluşturulma"), auto_now_add=True, db_index=True)
    trace_id = models.UUIDField(_("trace ID"), default=uuid.uuid4, editable=False, db_index=True)
    span_id = models.UUIDField(_("span ID"), default=uuid.uuid4, editable=False)
    
    class Meta:
        verbose_name = _("denetim kaydı")
        verbose_name_plural = _("denetim kayıtları")
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "-created_at"]),
            models.Index(fields=["model_name", "object_id"]),
            models.Index(fields=["action", "-created_at"]),
            models.Index(fields=["trace_id"]),
            models.Index(fields=["ip_address"]),
            models.Index(fields=["is_api", "-created_at"]),
            models.Index(fields=["session_key", "-created_at"]),
        ]
    
    def __str__(self):
        user = self.user_email or (self.user.get_full_name() if self.user else "Sistem")
        return f"{self.created_at:%Y-%m-%d %H:%M} - {user} - {self.get_action_display()} - {self.model_name}"
    
    @property
    def is_security_event(self):
        """Güvenlik olayı mı?"""
        return self.action in [
            self.Action.LOGIN_FAILED,
            self.Action.ROLE_CHANGE,
            self.Action.PERMISSION_CHANGE,
            self.Action.DELETE,
        ]


class SecurityEvent(models.Model):
    """Güvenlik odaklı olaylar için ayrı tablo (hızlı sorgu için)."""
    
    class Severity(models.TextChoices):
        LOW = "low", _("Düşük")
        MEDIUM = "medium", _("Orta")
        HIGH = "high", _("Yüksek")
        CRITICAL = "critical", _("Kritik")
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    
    event_type = models.CharField(_("olay türü"), max_length=50, db_index=True)
    severity = models.CharField(_("şiddet"), max_length=10, choices=Severity.choices, default=Severity.MEDIUM)
    
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="security_events"
    )
    ip_address = models.GenericIPAddressField(_("IP adresi"), db_index=True)
    
    title = models.CharField(_("başlık"), max_length=200)
    description = models.TextField(_("açıklama"), blank=True)
    details = models.JSONField(_("detaylar"), default=dict, blank=True)
    
    is_resolved = models.BooleanField(_("çözüldü"), default=False)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="resolved_security_events"
    )
    resolved_at = models.DateTimeField(_("çözülme zamanı"), null=True, blank=True)
    resolution_notes = models.TextField(_("çözüm notları"), blank=True)
    
    created_at = models.DateTimeField(_("oluşturulma"), auto_now_add=True, db_index=True)
    
    class Meta:
        verbose_name = _("güvenlik olayı")
        verbose_name_plural = _("güvenlik olayları")
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["severity", "-created_at"]),
            models.Index(fields=["is_resolved", "-created_at"]),
            models.Index(fields=["event_type", "-created_at"]),
        ]
    
    def __str__(self):
        return f"[{self.get_severity_display()}] {self.title} - {self.created_at:%Y-%m-%d %H:%M}"


class FeatureFlag(models.Model):
    """EMOG özellik açık/kapalı anahtarı."""

    key = models.SlugField(_("anahtar"), max_length=64, unique=True)
    label = models.CharField(_("ad"), max_length=120, blank=True)
    group = models.CharField(_("kategori"), max_length=32, blank=True, db_index=True)
    description = models.CharField(_("açıklama"), max_length=300, blank=True)

    enabled = models.BooleanField(_("aktif"), default=True, db_index=True)
    default_enabled = models.BooleanField(_("varsayılan"), default=True)

    critical = models.BooleanField(_("kritik"), default=False)
    requires_confirmation = models.BooleanField(_("onay gerekli"), default=False)

    disabled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="disabled_features",
        verbose_name=_("kapatan kullanıcı"),
    )
    disabled_at = models.DateTimeField(_("kapatma zamanı"), null=True, blank=True)
    updated_at = models.DateTimeField(_("güncelleme"), auto_now=True)

    class Meta:
        verbose_name = _("özellik anahtarı")
        verbose_name_plural = _("özellik anahtarları")
        ordering = ["group", "label"]

    def __str__(self):
        return f"{self.label or self.key} ({'açık' if self.enabled else 'kapalı'})"

    @property
    def status(self):
        if self.enabled == self.default_enabled:
            return "varsayılan"
        return "değiştirilmiş"


class VisitorSession(models.Model):
    """Kullanıcının tarayıcı oturumu: cihaz, ekran, dil, ülke, istek sayısı."""

    session_key = models.CharField(_("oturum anahtarı"), max_length=64, unique=True)

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="visitor_sessions",
        verbose_name=_("kullanıcı"),
    )
    app_user_id = models.CharField(_("uygulama kullanıcı id"), max_length=64, blank=True, db_index=True)

    ip_address = models.GenericIPAddressField(_("IP adresi"), null=True, blank=True, db_index=True)
    country = models.CharField(_("ülke"), max_length=8, blank=True, db_index=True)
    language = models.CharField(_("dil"), max_length=16, blank=True, db_index=True)
    device = models.CharField(_("cihaz"), max_length=32, blank=True, db_index=True)
    os = models.CharField(_("işletim sistemi"), max_length=64, blank=True)
    browser = models.CharField(_("tarayıcı"), max_length=64, blank=True)

    screen_width = models.PositiveIntegerField(_("ekran genişliği"), null=True, blank=True)
    screen_height = models.PositiveIntegerField(_("ekran yüksekliği"), null=True, blank=True)
    viewport_width = models.PositiveIntegerField(_("görünüm genişliği"), null=True, blank=True)
    pixel_ratio = models.FloatField(_("piksel oranı"), null=True, blank=True)
    touch = models.BooleanField(_("dokunmatik"), default=False)

    user_agent = models.TextField(_("kullanıcı ajanı"), blank=True)
    referer = models.URLField(_("referrer"), max_length=500, blank=True)

    request_count = models.PositiveIntegerField(_("istek sayısı"), default=0)
    api_count = models.PositiveIntegerField(_("API istek sayısı"), default=0)
    error_count = models.PositiveIntegerField(_("hata sayısı"), default=0)

    first_seen = models.DateTimeField(_("ilk görülme"), db_index=True)
    last_seen = models.DateTimeField(_("son görülme"), db_index=True)

    class Meta:
        verbose_name = _("ziyaretçi oturumu")
        verbose_name_plural = _("ziyaretçi oturumları")
        ordering = ["-last_seen"]
        indexes = [
            models.Index(fields=["-last_seen"]),
            models.Index(fields=["country", "-last_seen"]),
            models.Index(fields=["language", "-last_seen"]),
            models.Index(fields=["screen_width", "-last_seen"]),
            models.Index(fields=["user", "-last_seen"]),
        ]

    def __str__(self):
        who = self.user.get_username() if self.user else (self.app_user_id or "anonim")
        return f"{who} | {self.country or '-'} | {self.language or '-'} | {self.last_seen:%Y-%m-%d %H:%M}"

    @property
    def is_online(self):
        from django.utils import timezone

        return (timezone.now() - self.last_seen).total_seconds() < 300