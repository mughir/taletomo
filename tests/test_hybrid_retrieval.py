import pytest
from django.contrib.auth import get_user_model
from taletomo.canon.models import CanonFact, Character
from taletomo.context.retrieval import ContextAssembler
from taletomo.planning.models import Chapter, ChapterPlan
from taletomo.planning.services import PlanningService
from taletomo.providers.adapters import FakeProviderAdapter
from taletomo.providers.models import ProviderConfig, ProviderType

User = get_user_model()


def _make_project(user, title):
    return PlanningService.create_project_with_scaffold(
        owner=user, title=title, premise="Hybrid retrieval testing"
    )


@pytest.mark.django_db
def test_dense_similarity_ranks_semantic_match_ahead_of_irrelevant_facts():
    user = User.objects.create(username="author_hybrid")
    project = _make_project(user, "Hybrid Chronicle")
    chapter = project.chapters.get(chapter_number=1)
    plan = chapter.plan
    plan.objectives = ["Find the azure cure in the apothecary."]
    plan.save()

    # Query embedding: synthetic vector favoring dimension 0 and 1
    query_emb = [1.0 if i < 2 else 0.0 for i in range(32)]

    # Semantic match (no keyword overlap with "azure cure in apothecary", but high vector alignment)
    semantic_fact = CanonFact.objects.create(
        project=project,
        subject="Cobalt Elixir",
        predicate="restores",
        value="vitality",
        provenance="Chapter 0",
        canonical_status=CanonFact.Status.CONFIRMED,
        embedding=[0.9 if i < 2 else 0.05 for i in range(32)],
    )

    # Irrelevant fact (orthogonal embedding, no keyword overlap)
    irrelevant_fact = CanonFact.objects.create(
        project=project,
        subject="Stone Mountain",
        predicate="contains",
        value="granite rocks",
        provenance="Chapter 0",
        canonical_status=CanonFact.Status.CONFIRMED,
        embedding=[0.0 if i < 2 else 0.5 for i in range(32)],
    )

    package = ContextAssembler.assemble_chapter_context(
        chapter=chapter,
        query_embedding=query_emb,
    )

    assert "Cobalt Elixir" in package.user_prompt
    entries = {e["id"]: e for e in package.manifest.source_entries if e["category"] == "retrieval"}
    assert str(semantic_fact.id) in entries
    # The semantic match receives a positive score from the dense similarity
    assert entries[str(semantic_fact.id)]["score"] > entries.get(str(irrelevant_fact.id), {}).get("score", 0)


@pytest.mark.django_db
def test_anti_leakage_strictly_prevents_future_facts_even_with_perfect_embedding():
    user = User.objects.create(username="author_anti_leakage")
    project = _make_project(user, "Anti-Leakage Chronicle")
    chapter = project.chapters.get(chapter_number=2)
    plan = chapter.plan
    plan.objectives = ["Investigate the hidden vault."]
    plan.save()

    query_emb = [1.0] * 32

    # Future fact with perfect embedding match (from Chapter 5, while we are drafting Chapter 2)
    future_fact = CanonFact.objects.create(
        project=project,
        subject="The Emperor",
        predicate="was betrayed by",
        value="his chief advisor Lord Malakor",
        provenance="Chapter 5",
        canonical_status=CanonFact.Status.CONFIRMED,
        embedding=[1.0] * 32,
    )

    # Past fact from Chapter 1
    past_fact = CanonFact.objects.create(
        project=project,
        subject="Old Guard",
        predicate="patrols",
        value="the outer gatehouse",
        provenance="Chapter 1",
        canonical_status=CanonFact.Status.CONFIRMED,
        embedding=[0.2] * 32,
    )

    package = ContextAssembler.assemble_chapter_context(
        chapter=chapter,
        query_embedding=query_emb,
    )

    assert "Lord Malakor" not in package.user_prompt
    assert str(future_fact.id) not in [e["id"] for e in package.manifest.source_entries]
    assert "Old Guard" in package.user_prompt


@pytest.mark.django_db
def test_assemble_chapter_context_auto_extracts_query_embedding_via_adapter():
    user = User.objects.create(username="author_adapter_emb")
    project = _make_project(user, "Adapter Emb Chronicle")
    chapter = project.chapters.get(chapter_number=1)

    cfg = ProviderConfig.objects.create(
        user=user,
        name="Fake Provider",
        provider_type=ProviderType.FAKE,
        is_default=True,
    )
    adapter = FakeProviderAdapter(cfg)

    # Calling assemble_chapter_context with adapter computes embedding automatically
    package = ContextAssembler.assemble_chapter_context(
        chapter=chapter,
        adapter=adapter,
    )

    assert package is not None
    assert package.total_tokens > 0
    assert package.manifest is not None
