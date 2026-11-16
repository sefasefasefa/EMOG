"""NEURAL CORE veri modeli.

Modeller `agent_app` uygulamasına aittir; `agent_app.models` sonunda
import edilerek Django tarafından keşfedilir.
"""
import uuid

from django.db import models

from agent_app.models import User


class NeuralRun(models.Model):
    """Tek bir bilişsel çalıştırma (plan + keşif + araştırma + değerlendirme)."""

    MODE_CHOICES = [
        ("full", "Tam döngü"),
        ("solve", "Problem çöz"),
        ("explore", "Keşif"),
        ("research", "Araştırma"),
        ("learn", "Öğren"),
    ]
    STATUS_CHOICES = [
        ("pending", "Bekliyor"),
        ("running", "Çalışıyor"),
        ("completed", "Tamamlandı"),
        ("failed", "Hata"),
        ("cancelled", "İptal"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="neural_runs",
        null=True, blank=True,
    )
    goal = models.TextField()
    mode = models.CharField(max_length=16, choices=MODE_CHOICES, default="full")
    status = models.CharField(
        max_length=16, choices=STATUS_CHOICES, default="pending", db_index=True
    )
    provider = models.CharField(max_length=32, blank=True)

    plan = models.JSONField(default=list, blank=True)
    alternatives = models.JSONField(default=list, blank=True)
    research = models.JSONField(default=dict, blank=True)
    result = models.JSONField(default=dict, blank=True)
    evaluation = models.JSONField(default=dict, blank=True)
    error = models.TextField(blank=True)

    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        app_label = "agent_app"
        db_table = "neural_runs"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "-created_at"]),
            models.Index(fields=["status", "-created_at"]),
        ]

    def __str__(self):
        return f"NeuralRun {str(self.id)[:8]} ({self.status})"


class NeuralDiscovery(models.Model):
    """Bir hedef için üretilen alternatif yaklaşımlar."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="neural_discoveries",
        null=True, blank=True,
    )
    run = models.ForeignKey(
        NeuralRun, on_delete=models.CASCADE, related_name="discoveries",
        null=True, blank=True,
    )
    goal = models.TextField()
    candidates = models.JSONField(default=list, blank=True)
    selected = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = "agent_app"
        db_table = "neural_discoveries"
        ordering = ["-created_at"]

    def __str__(self):
        return f"Discovery {str(self.id)[:8]}"


class NeuralExperience(models.Model):
    """Deneyimden öğrenme kaydı: sonuç, puan ve çıkarılan ders."""

    OUTCOME_CHOICES = [
        ("success", "Başarılı"),
        ("partial", "Kısmi"),
        ("failure", "Başarısız"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="neural_experiences",
        null=True, blank=True,
    )
    run = models.ForeignKey(
        NeuralRun, on_delete=models.SET_NULL, related_name="experiences",
        null=True, blank=True,
    )
    goal = models.TextField()
    outcome = models.CharField(
        max_length=16, choices=OUTCOME_CHOICES, default="partial", db_index=True
    )
    score = models.PositiveSmallIntegerField(default=0)
    lesson = models.TextField(blank=True)
    context = models.JSONField(default=dict, blank=True)
    shared = models.BooleanField(default=False, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        app_label = "agent_app"
        db_table = "neural_experiences"
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["user", "-created_at"])]

    def __str__(self):
        return f"Experience {self.outcome} {self.score}"


class NeuralPreference(models.Model):
    """Kullanıcıya özel öğrenilmiş tercih (dil, ton, teknoloji...)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="neural_preferences"
    )
    key = models.CharField(max_length=120)
    value = models.JSONField(default=dict, blank=True)
    confidence = models.FloatField(default=1.0)
    source = models.CharField(max_length=32, default="user")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        app_label = "agent_app"
        db_table = "neural_preferences"
        unique_together = [("user", "key")]
        ordering = ["key"]

    def __str__(self):
        return f"{self.key}={self.value}"


class NeuralProposal(models.Model):
    """Kendini geliştirme önerisi (yalnızca güvenli politika anahtarları)."""

    STATUS_CHOICES = [
        ("pending", "Bekliyor"),
        ("applied", "Uygulandı"),
        ("rejected", "Reddedildi"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="neural_proposals"
    )
    title = models.CharField(max_length=300)
    rationale = models.TextField(blank=True)
    changes = models.JSONField(default=dict, blank=True)
    risk = models.CharField(max_length=16, default="low")
    status = models.CharField(
        max_length=16, choices=STATUS_CHOICES, default="pending", db_index=True
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    applied_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        app_label = "agent_app"
        db_table = "neural_proposals"
        ordering = ["-created_at"]

    def __str__(self):
        return self.title


class NeuralToolApproval(models.Model):
    """Yazma/komut çalıştırma için kullanıcı onayı bekleyen araç talebi."""

    STATUS_CHOICES = [
        ("pending", "Onay bekliyor"),
        ("executing", "Çalışıyor"),
        ("approved", "Onaylandı"),
        ("rejected", "Reddedildi"),
        ("failed", "Hata"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="neural_tool_approvals"
    )
    run = models.ForeignKey(
        NeuralRun, on_delete=models.SET_NULL, related_name="tool_approvals",
        null=True, blank=True,
    )
    tool = models.CharField(max_length=32)
    args = models.JSONField(default=dict, blank=True)
    status = models.CharField(
        max_length=16, choices=STATUS_CHOICES, default="pending", db_index=True
    )
    output = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    decided_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        app_label = "agent_app"
        db_table = "neural_tool_approvals"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.tool} ({self.status})"


class NeuralGoal(models.Model):
    """Otonom araştırma/öğrenme hedefi; `neural_cycle` ile zamanlanır."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="neural_goals"
    )
    title = models.TextField()
    mode = models.CharField(max_length=16, default="full")
    interval_minutes = models.PositiveIntegerField(default=60)
    enabled = models.BooleanField(default=True, db_index=True)
    last_run_at = models.DateTimeField(null=True, blank=True)
    next_run_at = models.DateTimeField(null=True, blank=True, db_index=True)
    last_result = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = "agent_app"
        db_table = "neural_goals"
        ordering = ["next_run_at", "-created_at"]

    def __str__(self):
        return self.title[:80]
