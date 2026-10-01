from decimal import Decimal
import pytest
from django.contrib.auth import get_user_model

from taletomo.generation.models import DraftArtifact, GenerationJob, JobStatus
from taletomo.generation.pipeline import GenerationPipeline
from taletomo.planning.models import Chapter, ChapterPlan, ScenePlan
from taletomo.planning.services import PlanningService
from taletomo.providers.adapters import FakeProviderAdapter
from taletomo.providers.models import ProviderConfig, ProviderType

User = get_user_model()


@pytest.mark.django_db
def test_multi_scene_chapter_generates_sequentially():
    owner = User.objects.create_user(username="author_scenes")
    project = PlanningService.create_project_with_scaffold(
        owner=owner, title="Multi-Scene Tale", premise="Testing sequential scene generation", target_chapters=2
    )
    chapter = project.chapters.get(chapter_number=1)
    plan = chapter.plan

    # Clear scaffold scenes and create 3 customized scenes
    plan.scenes.all().delete()
    ScenePlan.objects.create(
        chapter_plan=plan,
        scene_order=1,
        objective="Inspect the alchemical vial in the study",
        conflict="A sudden midnight guard patrol arrives outside",
        characters=["Alaric"],
        setting="Alaric's Study",
        estimated_words=800,
    )
    ScenePlan.objects.create(
        chapter_plan=plan,
        scene_order=2,
        objective="Escape through the iron grate into the aqueduct",
        conflict="Left wrist injury makes descending the ladder agonizing",
        characters=["Alaric", "Captain Vance"],
        setting="Old Aqueduct",
        estimated_words=900,
    )
    ScenePlan.objects.create(
        chapter_plan=plan,
        scene_order=3,
        objective="Rendezvous at the canal safehouse",
        conflict="Watchdogs barking along the riverbank",
        characters=["Alaric"],
        setting="Canal Safehouse",
        estimated_words=1000,
    )

    cfg = ProviderConfig.objects.create(
        user=owner,
        name="Fake Provider",
        provider_type=ProviderType.FAKE,
        is_default=True,
    )
    adapter = FakeProviderAdapter(cfg)

    job = GenerationJob.objects.create(
        project=project,
        target_chapter_id=chapter.id,
        user=owner,
        job_type="chapter_draft",
        idempotency_key="job-scene-pipeline-1",
    )

    draft = GenerationPipeline.execute_chapter_generation(
        job=job,
        worker_id="worker-scenes-1",
        custom_adapter=adapter,
    )

    assert draft is not None
    assert draft.prompt_version == "v2-scene-pipeline"
    # Verify scenes are stitched together with scene breaks
    assert "* * *" in draft.prose_content
    # Check that content contains narratives from the scenes
    assert "Alaric" in draft.prose_content

    job.refresh_from_db()
    assert job.status == JobStatus.READY
    assert job.confirmed_tokens > 0


@pytest.mark.django_db
def test_scene_pipeline_respects_cooperative_cancellation():
    owner = User.objects.create_user(username="author_cancel_scene")
    project = PlanningService.create_project_with_scaffold(
        owner=owner, title="Cancel Tale", premise="Testing scene cancellation", target_chapters=2
    )
    chapter = project.chapters.get(chapter_number=1)

    cfg = ProviderConfig.objects.create(
        user=owner,
        name="Fake Provider",
        provider_type=ProviderType.FAKE,
        is_default=True,
    )

    job = GenerationJob.objects.create(
        project=project,
        target_chapter_id=chapter.id,
        user=owner,
        job_type="chapter_draft",
        idempotency_key="job-cancel-mid-scene",
    )

    # Cancel the job before generation starts
    job.status = JobStatus.CANCELLED
    job.save(update_fields=["status"])

    result = GenerationPipeline.execute_chapter_generation(
        job=job,
        worker_id="worker-cancel",
        custom_adapter=FakeProviderAdapter(cfg),
    )

    assert result is None
    assert DraftArtifact.objects.filter(chapter=chapter).count() == 0


@pytest.mark.django_db
def test_single_scene_chapter_uses_single_shot_v1():
    owner = User.objects.create_user(username="author_single_scene")
    project = PlanningService.create_project_with_scaffold(
        owner=owner, title="Single Scene Tale", premise="Testing single-shot fallback", target_chapters=2
    )
    chapter = project.chapters.get(chapter_number=1)
    plan = chapter.plan

    # Leave only 1 scene
    plan.scenes.all().delete()
    ScenePlan.objects.create(
        chapter_plan=plan,
        scene_order=1,
        objective="Single continuous chapter action",
        conflict="Continuous struggle",
        estimated_words=2200,
    )

    cfg = ProviderConfig.objects.create(
        user=owner,
        name="Fake Provider",
        provider_type=ProviderType.FAKE,
        is_default=True,
    )

    job = GenerationJob.objects.create(
        project=project,
        target_chapter_id=chapter.id,
        user=owner,
        job_type="chapter_draft",
        idempotency_key="job-single-scene-v1",
    )

    draft = GenerationPipeline.execute_chapter_generation(
        job=job,
        worker_id="worker-single-scene",
        custom_adapter=FakeProviderAdapter(cfg),
    )

    assert draft is not None
    assert draft.prompt_version == "v1"
