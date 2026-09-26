from django.db import migrations
from django.utils.text import slugify

from taletomo.taxonomy.seed_data import BUILTIN_TERMS


def seed_builtin_terms(apps, schema_editor):
    StyleTerm = apps.get_model("taxonomy", "StyleTerm")
    for field, name, definition, example in BUILTIN_TERMS:
        StyleTerm.objects.update_or_create(
            field=field,
            slug=slugify(name)[:120],
            defaults={
                "name": name,
                "definition": definition,
                "example": example,
                "is_builtin": True,
                "created_by": None,
            },
        )


def remove_builtin_terms(apps, schema_editor):
    StyleTerm = apps.get_model("taxonomy", "StyleTerm")
    StyleTerm.objects.filter(is_builtin=True).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("taxonomy", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed_builtin_terms, remove_builtin_terms),
    ]
