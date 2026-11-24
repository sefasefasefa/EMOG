"""
Şablonlara güvenlik bilgisi taşıyan context processor.

`EMOG_IS_ADMIN`:
  - Django admin oturumu (auth.User) yetkiliyse True
  - Veya uygulama oturumu (auth_token cookie) yetkiliyse True
"""
from django.conf import settings


def emog_security(request):
    from agent_app.security import (
        admin_url_prefix,
        authorized_admin_email,
        authorized_admin_usernames,
        is_authorized_admin,
        is_authorized_identity,
    )

    is_admin = False
    source = ""

    django_user = getattr(request, "user", None)
    if django_user is not None and getattr(django_user, "is_authenticated", False):
        if is_authorized_admin(django_user):
            is_admin = True
            source = "django-session"

    if not is_admin:
        token = request.COOKIES.get("auth_token")
        if token:
            try:
                from agent_app.auth_views import get_user_from_token

                app_user = get_user_from_token(token)
            except Exception:
                app_user = None

            if app_user is not None and is_authorized_identity(
                getattr(app_user, "email", None), getattr(app_user, "username", None)
            ):
                is_admin = True
                source = "app-token"

    features = {}
    try:
        from audit.feature_store import enabled_keys

        features = enabled_keys()
    except Exception:
        features = {}

    return {
        "EMOG_IS_ADMIN": is_admin,
        "EMOG_ADMIN_SOURCE": source,
        "EMOG_ADMIN_URL": admin_url_prefix(),
        "EMOG_ADMIN_EMAIL": authorized_admin_email(),
        "EMOG_ADMIN_USERNAMES": ", ".join(sorted(authorized_admin_usernames())),
        "EMOG_FEATURES": features,
        "FEATURE_ON": lambda key: key in features,
    }
