import pytest
from django.contrib.auth import get_user_model
from taletomo.consistency.models import FindingCategory, FindingSeverity
from taletomo.generation.models import GenerationJob, JobStatus
from taletomo.generation.pipeline import GenerationPipeline
from taletomo.planning.services import PlanningService
from taletomo.providers.adapters import FakeProviderAdapter
from taletomo.providers.models import ProviderConfig, ProviderType

User = get_user_model()


@pytest.mark.django_db
def test_short_generated_chapter_is_explicitly_flagged_for_author_review():
    user = User.objects.create_user(username="short_chapter_author")
    project = PlanningService.create_project_with_scaffold(
        owner=user,
        title="An intentionally short fixture",
        premise="Test output length reporting",
        target_chapters=1,
        target_words_per_chapter=2200,
    )
    chapter = project.chapters.get(chapter_number=1)
    job = GenerationJob.objects.create(
        project=project,
        user=user,
        job_type="chapter_draft",
        idempotency_key="short-chapter-quality-check",
        target_chapter_id=chapter.id,
    )
    config = ProviderConfig.objects.create(
        user=user,
        name="Short output fixture",
        provider_type=ProviderType.FAKE,
    )

    draft = GenerationPipeline.execute_chapter_generation(
        job=job,
        worker_id="short-output-test-worker",
        custom_adapter=FakeProviderAdapter(config),
    )

    job.refresh_from_db()
    finding = chapter.continuity_findings.get(category=FindingCategory.LENGTH)
    assert finding.severity == FindingSeverity.WARNING
    assert draft.word_count < chapter.plan.target_words
    assert str(draft.word_count) in job.stage
    assert str(chapter.plan.target_words) in job.stage
    assert "author review" in job.stage.lower()
    assert job.status == JobStatus.READY
