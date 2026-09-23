import json
import pytest
from django.contrib.auth import get_user_model
from taletomo.canon.models import CanonFact
from taletomo.exporting.services import ExportService
from taletomo.generation.models import DraftArtifact
from taletomo.planning.models import Chapter
from taletomo.planning.services import PlanningService

User = get_user_model()


@pytest.mark.django_db
def test_export_markdown_and_json_restore_parity():
    user = User.objects.create(username="backup_author")
    project = PlanningService.create_project_with_scaffold(
        owner=user,
        title="The Glass Empress",
        premise="A clockwork court of mirrors and assassins.",
        target_chapters=12,
    )

    ch1 = Chapter.objects.get(project=project, chapter_number=1)
    draft = DraftArtifact.objects.create(
        chapter=ch1,
        version_number=1,
        prose_content="The gears turned in the throne of glass with a crystal chime.",
        word_count=12,
        model_name="test-model",
        status="accepted",
    )
    ch1.active_draft_id = draft.id
    ch1.save()

    CanonFact.objects.create(
        project=project,
        subject="Glass Throne",
        predicate="composed_of",
        value="Enchanted Quartz",
        canonical_status=CanonFact.Status.CONFIRMED,
    )

    # 1. Test Markdown export
    md_output = ExportService.export_markdown(project)
    assert "# The Glass Empress" in md_output
    assert "The gears turned in the throne of glass" in md_output

    # 2. Test JSON backup export
    backup_json = ExportService.export_json_backup(project)
    assert backup_json["format_version"] == "1.0"
    assert "manifest" in backup_json
    assert "checksum_sha256" in backup_json["manifest"]
    assert backup_json["manifest"]["total_chapters"] == project.chapters.count()

    # Verify zero secrets in backup
    raw_str = json.dumps(backup_json)
    assert "sk-" not in raw_str
    assert "api_key" not in raw_str

    # 3. Test Restore into New Project
    restored = ExportService.restore_from_json(owner=user, backup_data=backup_json)
    assert restored.id != project.id
    assert restored.title == "The Glass Empress"
    assert restored.target_chapters == 12
    assert restored.chapters.count() == project.chapters.count()
    assert restored.slug == project.slug
    assert restored.tolerance_percent == project.tolerance_percent
    assert restored.prose_language == project.prose_language

    restored_ch1 = Chapter.objects.get(project=restored, chapter_number=1)
    assert restored_ch1.drafts.count() == 1
    assert "gears turned" in restored_ch1.drafts.first().prose_content
    assert restored.canon_facts.filter(subject="Glass Throne").exists()


@pytest.mark.django_db
def test_restore_rejects_non_dict_payload():
    user = User.objects.create(username="invalid_backup_user")
    with pytest.raises(ValueError, match="Backup data must be a JSON object"):
        ExportService.restore_from_json(owner=user, backup_data=["not", "a", "dict"])


@pytest.mark.django_db
def test_restore_handles_unmapped_story_event_chapter():
    user = User.objects.create(username="event_author")
    project = PlanningService.create_project_with_scaffold(
        owner=user,
        title="Eventful Journey",
        premise="Testing events.",
        target_chapters=3,
    )
    backup_json = ExportService.export_json_backup(project)
    # Tamper with an event to reference an unmapped chapter number 999
    backup_json["canon"]["events"].append({
        "chapter_number": 999,
        "event_type": "lore_drop",
        "summary": "Ancient knowledge revealed",
        "payload": {},
    })
    # Recompute checksum
    import hashlib
    raw_payload = json.dumps({k: v for k, v in backup_json.items() if k != "manifest"}, sort_keys=True)
    backup_json["manifest"]["checksum_sha256"] = hashlib.sha256(raw_payload.encode("utf-8")).hexdigest()

    restored = ExportService.restore_from_json(owner=user, backup_data=backup_json)
    assert restored.story_events.filter(event_type="lore_drop").exists()

