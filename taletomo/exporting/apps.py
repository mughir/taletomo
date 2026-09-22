from django.apps import AppConfig


class ExportingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "taletomo.exporting"
    verbose_name = "TaleTomo Exporting & Backup"
