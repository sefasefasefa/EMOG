"""Kişisel öğrenme: kullanıcıya özel tercihlerin kalıcı deposu."""
from . import config as neural_config
from .models import NeuralPreference


def set_value(user, key, value, source="user", confidence=1.0):
    """Tercihi oluştur veya güncelle."""
    key = (key or "").strip()[:120]
    if not key:
        raise ValueError("tercih anahtarı boş olamaz")
    cfg = neural_config.get_config()
    existing = NeuralPreference.objects.filter(user=user, key=key).first()
    if existing is None:
        limit = int(cfg.get("MAX_PREFERENCES", 200))
        if NeuralPreference.objects.filter(user=user).count() >= limit:
            raise ValueError("tercih sınırı aşıldı")
    try:
        confidence = max(0.0, min(1.0, float(confidence)))
    except (TypeError, ValueError):
        confidence = 1.0
    preference, _ = NeuralPreference.objects.update_or_create(
        user=user,
        key=key,
        defaults={
            "value": value,
            "source": (source or "user")[:32],
            "confidence": confidence,
        },
    )
    return preference


def get_value(user, key, default=None):
    preference = NeuralPreference.objects.filter(user=user, key=key).first()
    return preference.value if preference is not None else default


def all_values(user):
    return {item.key: item.value for item in NeuralPreference.objects.filter(user=user)}


def delete_value(user, key):
    return NeuralPreference.objects.filter(user=user, key=key).delete()[0]
