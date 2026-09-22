from django.apps import AppConfig


class GenerationConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "taletomo.generation"
    verbose_name = "TaleTomo Generation Pipeline"
