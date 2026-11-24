"""
Admin site bootstrap.

`unfold`, kendi `UnfoldAdminSite` örneğini `admin.site` yerine koyar.
Bu uygulama config'i en sonda çalışarak admin site'ı
`agent_app.security.RestrictedAdminSite` ile değiştirir ve daha önce
kaydedilmiş tüm ModelAdmin'leri yeni site'a taşır.

INSTALLED_APPS içinde `django.contrib.admin` ve `unfold` SONRASINDA gelmelidir.
"""
from django.apps import AppConfig


class AdminBootstrapConfig(AppConfig):
    name = "emog_admin"
    label = "emog_admin"
    verbose_name = "Admin Bootstrap"

    def ready(self):
        from django.contrib import admin
        from django.contrib.admin import sites as admin_sites

        from agent_app.security import RestrictedAdminSite

        if isinstance(admin.site, RestrictedAdminSite):
            return

        old_site = admin.site
        new_site = RestrictedAdminSite()

        # Kayıtlı model admin'lerini ve admin aksiyonlarını yeni site'a taşı
        new_site._registry.update(getattr(old_site, "_registry", {}))
        new_site._actions.update(getattr(old_site, "_actions", {}))
        new_site._global_actions.update(getattr(old_site, "_global_actions", {}))

        admin.site = new_site
        admin_sites.site = new_site
