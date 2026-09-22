import pytest
from django.contrib.auth import get_user_model
from taletomo.canon.models import StorySnapshot
from taletomo.canon.services import CanonService, StaleHeadError
from taletomo.generation.models import DraftArtifact, DraftStatus, GenerationJob, JobStatus
from taletomo.generation.pipeline import GenerationPipeline
from taletomo.planning.models import Chapter
from taletomo.planning.services import PlanningService
from taletomo.providers.adapters import FakeProviderAdapter
from taletomo.providers.models import ProviderConfig, ProviderType

User = get_user_model()


@pytest.mark.django_db
def test_full_vertical_slice_generation_and_two_phase_commit():
    user = User.objects.create(username="vertical_slice_author")
    project = PlanningService.create_project_with_scaffold(
        owner=user,
        title="The Alchemist's Shadow",
        premise="An exiled scholar uncovers a conspiracy in the Brass Empire.",
        target_chapters=50,
    )

    assert project.active_branch_head == "rev_1"
    assert project.volumes.count() >= 1
    assert project.chapters.count() >= 5  # Rolling horizon

    ch1 = Chapter.objects.get(project=project, chapter_number=1)
    assert hasattr(ch1, "plan")

    # 1. Create durable job
    job = GenerationJob.objects.create(
        project=project,
        user=user,
        job_type="chapter_draft",
        idempotency_key="test-job-ch1-v1",
        target_chapter_id=ch1.id,
    )

    fake_config = ProviderConfig.objects.create(
        user=user,
        name="Test Fake",
        provider_type=ProviderType.FAKE,
        is_default=True,
    )
    fake_adapter = FakeProviderAdapter(fake_config)

    # 2. Execute pipeline
    draft = GenerationPipeline.execute_chapter_generation(
        job=job, worker_id="test-worker", custom_adapter=fake_adapter
    )

    job.refresh_from_db()
    assert job.status == JobStatus.READY
    assert job.confirmed_tokens > 0
    assert job.draft_artifact == draft

    # 3. Check Draft Artifact properties (noncanonical until accepted)
    assert draft.version_number == 1
    assert draft.status == DraftStatus.UNDER_REVIEW
    assert draft.word_count > 0
    assert draft.context_manifest is not None

    ch1.refresh_from_db()
    assert ch1.status == Chapter.Status.REVIEW
    assert ch1.active_draft_id == draft.id

    # 4. Phase 1: Author Approves Draft Prose
    draft.status = DraftStatus.ACCEPTED
    draft.save()
    ch1.status = Chapter.Status.APPROVED
    ch1.save()

    # 5. Phase 2: Author Reviews and Commits Canonical Story State
    events = [
        {"summary": "Alaric escaped through the aqueduct.", "event_type": "plot_progress"}
    ]
    facts = [
        {"subject": "Alaric", "predicate": "location", "value": "Old Aqueduct", "scope": "world_truth"}
    ]

    snapshot = CanonService.commit_chapter_canon(
        project=project,
        chapter=ch1,
        expected_head="rev_1",
        events=events,
        facts=facts,
        actor=user,
    )

    project.refresh_from_db()
    ch1.refresh_from_db()

    assert project.active_branch_head == "rev_2"
    assert ch1.status == Chapter.Status.LOCKED
    assert snapshot.revision == "rev_2"
    assert project.story_events.count() == 1
    assert project.canon_facts.filter(subject="Alaric").exists()

    # 6. Verify StaleHeadError rejection when committing against old head
    with pytest.raises(StaleHeadError):
        CanonService.commit_chapter_canon(
            project=project,
            chapter=ch1,
            expected_head="rev_1",  # Old head! Current is rev_2
            events=[],
            facts=[],
            actor=user,
        )
