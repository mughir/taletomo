import pytest
from django.contrib.auth import get_user_model
from taletomo.canon.services import CanonService
from taletomo.consistency.models import ContinuityFinding, FindingCategory, FindingSeverity, FindingStatus
from taletomo.core.models import AuditLog
from taletomo.generation.models import DraftArtifact, DraftStatus
from taletomo.planning.models import Chapter, Project
from taletomo.planning.services import PlanningService
from taletomo.providers.adapters import ProviderGateway
from taletomo.providers.models import ProviderConfig, ProviderType

User = get_user_model()


@pytest.mark.django_db
def test_foreign_actor_cannot_commit_another_authors_chapter():
    owner = User.objects.create_user(username="canon_owner")
    stranger = User.objects.create_user(username="canon_stranger")
    project = PlanningService.create_project_with_scaffold(
        owner=owner, title="Private canon", premise="An author's private story"
    )
    chapter = project.chapters.get(chapter_number=1)
    draft = DraftArtifact.objects.create(
        chapter=chapter,
        version_number=1,
        prose_content="The heroine returns home.",
        word_count=4,
        status=DraftStatus.ACCEPTED,
    )
    chapter.active_draft_id = draft.id
    chapter.status = Chapter.Status.APPROVED
    chapter.save(update_fields=["active_draft_id", "status"])
    expected_head = project.active_branch_head

    with pytest.raises(PermissionError, match="owner"):
        CanonService.commit_chapter_canon(
            project=project,
            chapter=chapter,
            expected_head=expected_head,
            events=[{"summary": "Unauthorized event"}],
            facts=[],
            actor=stranger,
        )

    project.refresh_from_db()
    assert project.active_branch_head == expected_head
    assert not project.story_events.exists()


@pytest.mark.django_db
def test_blocker_override_requires_a_nonempty_rationale():
    owner = User.objects.create_user(username="override_owner")
    project = PlanningService.create_project_with_scaffold(
        owner=owner, title="Override policy", premise="An unresolved contradiction"
    )
    chapter = project.chapters.get(chapter_number=1)
    draft = DraftArtifact.objects.create(
        chapter=chapter,
        version_number=1,
        prose_content="The hero enters the hall.",
        word_count=5,
        status=DraftStatus.ACCEPTED,
    )
    chapter.active_draft_id = draft.id
    chapter.status = Chapter.Status.APPROVED
    chapter.save(update_fields=["active_draft_id", "status"])
    ContinuityFinding.objects.create(
        project=project,
        chapter=chapter,
        category=FindingCategory.IDENTITY,
        severity=FindingSeverity.BLOCKER,
        status=FindingStatus.OPEN,
        claim="Unresolved identity contradiction",
    )
    expected_head = project.active_branch_head

    with pytest.raises(ValueError, match="rationale"):
        CanonService.commit_chapter_canon(
            project=project,
            chapter=chapter,
            expected_head=expected_head,
            events=[{"summary": "Override without explanation"}],
            facts=[],
            actor=owner,
            override_blockers=True,
            override_rationale="",
        )

    project.refresh_from_db()
    assert project.active_branch_head == expected_head
    assert not project.story_events.exists()

    rationale = "The evidence shows the king is a recorded illusion."
    CanonService.commit_chapter_canon(
        project=project,
        chapter=chapter,
        expected_head=expected_head,
        events=[{"summary": "Authorized override"}],
        facts=[],
        actor=owner,
        override_blockers=True,
        override_rationale=rationale,
    )
    audit = AuditLog.objects.get(action="CANON_COMMIT", target_id=str(chapter.id))
    assert rationale in audit.reason


@pytest.mark.django_db
def test_provider_gateway_rejects_another_users_explicit_config():
    owner = User.objects.create_user(username="provider_config_owner")
    stranger = User.objects.create_user(username="provider_config_stranger")
    stranger_project = PlanningService.create_project_with_scaffold(
        owner=stranger, title="Other provider", premise="Must not use foreign credentials"
    )
    foreign_config = ProviderConfig.objects.create(
        user=owner,
        name="Owner-only provider",
        provider_type=ProviderType.FAKE,
        is_active=True,
    )

    with pytest.raises(PermissionError, match="does not belong"):
        ProviderGateway.get_adapter(
            config=foreign_config,
            user=stranger,
            project=stranger_project,
        )
