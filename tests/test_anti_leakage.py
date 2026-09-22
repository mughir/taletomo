import pytest
from django.contrib.auth import get_user_model
from taletomo.canon.models import CanonFact, TruthScope
from taletomo.context.retrieval import ContextAssembler
from taletomo.planning.models import Chapter, ChapterPlan, Project
from taletomo.planning.services import PlanningService

User = get_user_model()


@pytest.mark.django_db
def test_anti_leakage_future_chapter_facts():
    user = User.objects.create(username="test_author")
    project = PlanningService.create_project_with_scaffold(
        owner=user,
        title="Chronicles of Anti-Leakage",
        premise="Testing future fact isolation.",
        target_chapters=10,
    )

    # Add a past fact (Chapter 1)
    CanonFact.objects.create(
        project=project,
        subject="Alaric",
        predicate="discovered",
        value="Azure Vial",
        provenance="Chapter 1",
        truth_scope=TruthScope.WORLD_TRUTH,
        canonical_status=CanonFact.Status.CONFIRMED,
    )

    # Add a future fact (Chapter 5)
    CanonFact.objects.create(
        project=project,
        subject="Captain Vance",
        predicate="revealed_as",
        value="Grand Traitor",
        provenance="Chapter 5",
        truth_scope=TruthScope.WORLD_TRUTH,
        canonical_status=CanonFact.Status.CONFIRMED,
    )

    # Assemble context for Chapter 2
    ch2 = Chapter.objects.get(project=project, chapter_number=2)
    package = ContextAssembler.assemble_chapter_context(ch2)

    # Verify past fact IS included
    assert "Azure Vial" in package.user_prompt

    # Verify future fact is STRICTLY EXCLUDED (Anti-leakage invariant)
    assert "Grand Traitor" not in package.user_prompt
