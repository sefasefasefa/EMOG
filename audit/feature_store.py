"""
Özellik anahtarı (feature flag) yönetimi.

Kullanım:
    from audit.features import is_enabled, feature_state
    if is_enabled("github"):
        ...
"""
from django.core.cache import cache

from audit.features import FEATURE_GROUPS, FEATURES, FEATURE_MAP

CACHE_KEY = "emog:feature_flags"
CACHE_TTL = 30


def seed_features():
    """Katalogdaki tüm özellikleri veritabanına yazar (var olanlara dokunmaz)."""
    from audit.models import FeatureFlag

    created = 0
    for feature in FEATURES:
        _, was_created = FeatureFlag.objects.get_or_create(
            key=feature["key"],
            defaults={
                "label": feature["label"],
                "group": feature["group"],
                "description": feature.get("description", ""),
                "enabled": feature.get("default", True),
                "default_enabled": feature.get("default", True),
                "critical": feature.get("critical", False),
                "requires_confirmation": feature.get("critical", False),
            },
        )
        if was_created:
            created += 1
    cache.delete(CACHE_KEY)
    return created


def _load():
    """Veritabanından aktif özellik anahtarlarını yükler (cache'li)."""
    cached = cache.get(CACHE_KEY)
    if cached is not None:
        return cached

    from audit.models import FeatureFlag

    try:
        rows = list(FeatureFlag.objects.values("key", "enabled", "default_enabled"))
    except Exception:
        # Migration henüz uygulanmadıysa varsayılanlara düş
        return {f["key"]: f.get("default", True) for f in FEATURES}

    if not rows:
        return {f["key"]: f.get("default", True) for f in FEATURES}

    states = {f["key"]: f.get("default", True) for f in FEATURES}
    for row in rows:
        states[row["key"]] = row["enabled"]

    cache.set(CACHE_KEY, states, CACHE_TTL)
    return states


def is_enabled(key):
    """Özellik açık mı?"""
    if key not in FEATURE_MAP:
        return True
    return bool(_load().get(key, FEATURE_MAP[key].get("default", True)))


def enabled_keys():
    return {key for key, value in _load().items() if value}


def disabled_keys():
    return {key for key, value in _load().items() if not value}


def set_enabled(key, enabled, user=None):
    """Özelliği açar/kapatır ve cache'i temizler."""
    from django.utils import timezone

    from audit.models import FeatureFlag
    from audit.signals import write_feature_audit

    if key not in FEATURE_MAP:
        raise KeyError(f"Bilinmeyen özellik: {key}")

    flag, created = FeatureFlag.objects.get_or_create(
        key=key,
        defaults={
            "label": FEATURE_MAP[key]["label"],
            "group": FEATURE_MAP[key]["group"],
            "description": FEATURE_MAP[key].get("description", ""),
            "default_enabled": FEATURE_MAP[key].get("default", True),
            "critical": FEATURE_MAP[key].get("critical", False),
            "requires_confirmation": FEATURE_MAP[key].get("critical", False),
        },
    )

    flag.enabled = bool(enabled)
    flag.disabled_by = None if enabled else user
    flag.disabled_at = None if enabled else timezone.now()
    flag.save(update_fields=["enabled", "disabled_by", "disabled_at", "updated_at"])

    cache.delete(CACHE_KEY)

    write_feature_audit(flag, user)
    return flag


def toggle(key, user=None):
    flag_value = is_enabled(key)
    return set_enabled(key, not flag_value, user=user)


def reset_all():
    """Tüm özellikleri varsayılan değerlerine döndürür."""
    from django.utils import timezone

    from audit.models import FeatureFlag

    changed = 0
    for feature in FEATURES:
        default = feature.get("default", True)
        flag = FeatureFlag.objects.filter(key=feature["key"]).first()
        if flag is None:
            FeatureFlag.objects.create(
                key=feature["key"],
                label=feature["label"],
                group=feature["group"],
                description=feature.get("description", ""),
                enabled=default,
                default_enabled=default,
                critical=feature.get("critical", False),
                requires_confirmation=feature.get("critical", False),
            )
            changed += 1
        elif flag.enabled != default:
            flag.enabled = default
            flag.disabled_by = None
            flag.disabled_at = None
            flag.save(update_fields=["enabled", "disabled_by", "disabled_at", "updated_at"])
            changed += 1

    cache.delete(CACHE_KEY)
    return changed


def grouped_states():
    """Panel için gruplandırılmış özellik durumları."""
    from audit.models import FeatureFlag

    try:
        rows = {row.key: row for row in FeatureFlag.objects.all()}
    except Exception:
        rows = {}

    states = _load()

    groups = []
    for group_key, group_label in FEATURE_GROUPS:
        items = []
        for feature in FEATURES:
            if feature["group"] != group_key:
                continue
            key = feature["key"]
            row = rows.get(key)
            items.append({
                "key": key,
                "label": feature["label"],
                "description": feature.get("description", ""),
                "enabled": bool(states.get(key, feature.get("default", True))),
                "default": feature.get("default", True),
                "critical": feature.get("critical", False),
                "changed": bool(row and row.enabled != feature.get("default", True)),
                "disabled_at": row.disabled_at if row and not row.enabled else None,
                "disabled_by": (
                    row.disabled_by.get_username()
                    if row and row.disabled_by and not row.enabled
                    else None
                ),
                "paths": feature.get("paths", []),
            })
        if items:
            groups.append({"key": group_key, "label": group_label, "items": items})

    total = sum(len(group["items"]) for group in groups)
    active = sum(1 for group in groups for item in group["items"] if item["enabled"])
    return {
        "groups": groups,
        "total": total,
        "active": active,
        "disabled": total - active,
    }


def blocked_paths():
    """Kapalı özelliklerin engelleyeceği URL önekleri."""
    states = _load()
    blocked = {}
    for feature in FEATURES:
        key = feature["key"]
        if states.get(key, feature.get("default", True)):
            continue
        for path in feature.get("paths", []):
            blocked.setdefault(path, []).append(key)
    return blocked


def reset_cache():
    cache.delete(CACHE_KEY)