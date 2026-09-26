import pytest
from django.contrib.auth import get_user_model
from taletomo.canon.models import CanonFact, Character
from taletomo.context.retrieval import ContextAssembler
from taletomo.planning.models import Chapter, ChapterPlan
from taletomo.planning.services import PlanningService

User = get_user_model()


def _make_project(user, title):
    return PlanningService.create_project_with_scaffold(owner=user, title=title, premise="Ranking fixture")


@pytest.mark.django_db
def test_relevant_character_ranks_before_irrelevant_ones():
    user = User.objects.create(username="rank_author")
    project = _make_project(user, "Chronicles of Ranking")

    relevant = Character.objects.create(
        project=project,
        name="Raven Nightwhisper",
        role="protagonist",
        goals="Recover the obsidian crown from the sunken vault before the tide returns",
    )
    fillers = [
        Character.objects.create(
            project=project,
            name=f"Zylo the Uninvolved {index}",
            role="neutral",
            goals="Tends goats on the eastern hills far from any plot",
        )
        for index in range(10)
    ]

    chapter = project.chapters.get(chapter_number=1)
    plan = chapter.plan
    plan.objectives = ["Raven Nightwhisper must recover the obsidian crown from the sunken vault."]
    plan.save()

    package = ContextAssembler.assemble_chapter_context(chapter)

    assert "Raven Nightwhisper" in package.user_prompt
    # The relevant character's manifest entry is scored and precedes zero-score fillers.
    entries = {entry["id"]: entry for entry in package.manifest.source_entries}
    assert entries[str(relevant.id)]["score"] >= 3
    for filler in fillers:
        assert entries[str(filler.id)]["score"] == 0

    relevant_pos = package.user_prompt.index("Raven Nightwhisper")
    filler_pos = package.user_prompt.index("Zylo the Uninvolved 0")
    assert relevant_pos < filler_pos


@pytest.mark.django_db
def test_budget_pressure_keeps_relevant_entities_and_drops_irrelevant_ones():
    user = User.objects.create(username="budget_pressure_author")
    project = _make_project(user, "Chronicles of Tight Budgets")

    Character.objects.create(
        project=project,
        name="Raven Nightwhisper",
        role="protagonist",
        goals="Recover the obsidian crown from the sunken vault before the tide returns",
    )
    for index in range(40):
        Character.objects.create(
            project=project,
            name=f"Aaa Filler Cadre {index:02d}",
            role="neutral",
            goals=(
                "Patrols the outer market district and maintains guild ledgers with meticulous "
                f"bookkeeping habits and perfectly reliable attendance records number {index}"
            ),
        )

    chapter = project.chapters.get(chapter_number=1)
    plan = chapter.plan
    plan.objectives = ["Raven Nightwhisper must recover the obsidian crown from the sunken vault."]
    plan.save()

    package = ContextAssembler.assemble_chapter_context(
        chapter,
        model_context_limit=8000,
        requested_output_tokens=1000,
    )

    assert "Raven Nightwhisper" in package.user_prompt
    # 40 irrelevant candidates exceed the state budget: the alphabetically late
    # ones must be dropped while the relevant one survives.
    assert "Aaa Filler Cadre 39" not in package.user_prompt
    state_entries = [e for e in package.manifest.source_entries if e["category"] == "state"]
    assert state_entries
    ranked_scores = [e["score"] for e in state_entries]
    assert ranked_scores == sorted(ranked_scores, reverse=True)


@pytest.mark.django_db
def test_equal_score_facts_are_recency_tiebroken():
    user = User.objects.create(username="fact_recency_author")
    project = _make_project(user, "Chronicles of Fact Recency")

    CanonFact.objects.create(
        project=project,
        subject="Alaric",
        predicate="wields",
        value="iron blade",
        provenance="Chapter 1",
        canonical_status=CanonFact.Status.CONFIRMED,
    )
    CanonFact.objects.create(
        project=project,
        subject="Alaric",
        predicate="wields",
        value="steel blade",
        provenance="Chapter 2",
        canonical_status=CanonFact.Status.CONFIRMED,
    )

    chapter = project.chapters.get(chapter_number=3)
    plan, _ = ChapterPlan.objects.get_or_create(chapter=chapter)
    plan.objectives = ["Alaric wields his blade against the raiders."]
    plan.save()

    package = ContextAssembler.assemble_chapter_context(chapter)

    steel_pos = package.user_prompt.index("steel blade")
    iron_pos = package.user_prompt.index("iron blade")
    assert steel_pos < iron_pos

    entries = {e["id"]: e["score"] for e in package.manifest.source_entries if e["category"] == "retrieval"}
    assert len(entries) == 2
    # Equal relevance (same terms matched); only recency decided the order.
    assert len(set(entries.values())) == 1
    assert next(iter(entries.values())) > 0
