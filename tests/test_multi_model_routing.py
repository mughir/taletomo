from decimal import Decimal
import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from taletomo.canon.extraction import CanonExtractionService
from taletomo.consistency.checker import ContinuityChecker
from taletomo.generation.models import DraftArtifact, GenerationJob, JobStatus
from taletomo.generation.pipeline import GenerationPipeline
from taletomo.planning.models import Chapter
from taletomo.planning.services import PlanningService
from taletomo.providers.adapters import FakeProviderAdapter, ProviderGateway
from taletomo.providers.models import BudgetReservation, ProviderConfig, ProviderType

User = get_user_model()


@pytest.mark.django_db
def test_provider_task_routing_resolution():
    cfg = ProviderConfig(
        name="Test Multi-Model",
        provider_type=ProviderType.FAKE,
        default_drafting_model="fallback-draft",
        default_planning_model="fallback-plan",
        default_critique_model="fallback-critique",
        default_extraction_model="fallback-extract",
        default_copilot_model="fallback-copilot",
        task_routing={
            "drafting": "claude-3-5-sonnet",
            "extraction": "gpt-4o-mini",
            "copilot": "gpt-4o",
        },
    )

    # Explicitly routed tasks
    assert cfg.get_model_for_task("drafting") == "claude-3-5-sonnet"
    assert cfg.get_model_for_task("extraction") == "gpt-4o-mini"
    assert cfg.get_model_for_task("copilot") == "gpt-4o"

    # Unrouted tasks fall back to their specific defaults
    assert cfg.get_model_for_task("planning") == "fallback-plan"
    assert cfg.get_model_for_task("critique") == "fallback-critique"

    # Unknown task falls back to drafting model
    assert cfg.get_model_for_task("unknown_task") == "fallback-draft"


@pytest.mark.django_db
def test_pipeline_dispatches_configured_drafting_model():
    owner = User.objects.create_user(username="author_routing")
    project = PlanningService.create_project_with_scaffold(
        owner=owner, title="Routing Test", premise="Testing multi-model routing", target_chapters=2
    )
    chapter = project.chapters.get(chapter_number=1)

    cfg = ProviderConfig.objects.create(
        user=owner,
        name="Routed Provider",
        provider_type=ProviderType.FAKE,
        task_routing={"drafting": "custom-creative-model-v2"},
        default_drafting_model="custom-creative-model-v2",
        is_default=True,
    )

    adapter = FakeProviderAdapter(cfg)
    job = GenerationJob.objects.create(
        project=project,
        target_chapter_id=chapter.id,
        user=owner,
        job_type="chapter_draft",
        idempotency_key="job-routing-1",
    )

    draft = GenerationPipeline.execute_chapter_generation(
        job=job,
        custom_adapter=adapter,
    )

    assert draft is not None
    assert draft.model_name == "custom-creative-model-v2"
    job.refresh_from_db()
    assert job.status == JobStatus.READY


@pytest.mark.django_db
def test_budget_limits_block_excessive_operations():
    owner = User.objects.create_user(username="author_budget")
    cfg = ProviderConfig.objects.create(
        user=owner,
        name="Budgeted Provider",
        provider_type=ProviderType.FAKE,
        daily_cost_limit_usd=Decimal("5.0000"),
        monthly_cost_limit_usd=Decimal("20.0000"),
        is_default=True,
    )

    # Record past spending of $4.90 today
    now = timezone.now()
    res = BudgetReservation.objects.create(
        user=owner,
        project_id=cfg.id,
        job_id=cfg.id,
        reserved_tokens=1000,
        reserved_cost_usd=Decimal("4.9000"),
    )
    res.reconcile(tokens_used=1000, cost_usd=Decimal("4.9000"))

    # An estimated cost of $0.15 would bring daily total to $5.05 > $5.00
    allowed, reason = cfg.check_budget_limits(estimated_cost_usd=Decimal("0.1500"), user=owner)
    assert not allowed
    assert "Daily cost limit of $5.00 reached" in reason

    # Within budget passes
    allowed, reason = cfg.check_budget_limits(estimated_cost_usd=Decimal("0.0500"), user=owner)
    assert allowed
    assert reason == ""


@pytest.mark.django_db
def test_pipeline_halts_when_budget_cap_exceeded():
    owner = User.objects.create_user(username="author_blocked")
    project = PlanningService.create_project_with_scaffold(
        owner=owner, title="Budget Blocked", premise="Testing budget halt", target_chapters=2
    )
    chapter = project.chapters.get(chapter_number=1)

    cfg = ProviderConfig.objects.create(
        user=owner,
        name="Strict Budget Provider",
        provider_type=ProviderType.FAKE,
        daily_cost_limit_usd=Decimal("1.0000"),
        model_profiles={
            "mock-drafting-v1": {
                "pricing_input_per_m": "100.0",
                "pricing_output_per_m": "200.0",
            }
        },
        is_default=True,
    )

    # Pre-exhaust the budget
    res = BudgetReservation.objects.create(
        user=owner,
        project_id=project.id,
        job_id=chapter.id,
        reserved_tokens=5000,
        reserved_cost_usd=Decimal("1.0000"),
    )
    res.reconcile(tokens_used=5000, cost_usd=Decimal("1.0000"))

    adapter = FakeProviderAdapter(cfg)
    job = GenerationJob.objects.create(
        project=project,
        target_chapter_id=chapter.id,
        user=owner,
        job_type="chapter_draft",
        idempotency_key="job-budget-blocked",
    )

    result = GenerationPipeline.execute_chapter_generation(
        job=job,
        custom_adapter=adapter,
    )

    assert result is None
    job.refresh_from_db()
    assert job.status == JobStatus.FAILED
    assert "Spending limit reached" in job.stage
    assert "Daily cost limit" in job.error_message


@pytest.mark.django_db
def test_settings_providers_saves_matrix_and_limits(client):
    owner = User.objects.create_user(username="matrix_author")
    client.force_login(owner)

    url = reverse("taletomo:settings_providers")
    response = client.post(
        url,
        {
            "name": "Production Multi-Model",
            "provider_type": ProviderType.OPENAI_COMPATIBLE,
            "endpoint_url": "https://api.openai.com/v1",
            "api_key": "sk-secret-test-key",
            "drafting_model": "claude-3-5-sonnet",
            "planning_model": "o1-preview",
            "extraction_model": "gpt-4o-mini",
            "critique_model": "gemini-1.5-flash",
            "copilot_model": "gpt-4o",
            "daily_cost_limit_usd": "25.00",
            "monthly_cost_limit_usd": "75.00",
        },
    )

    assert response.status_code == 302
    cfg = ProviderConfig.objects.get(user=owner, name="Production Multi-Model")
    assert cfg.default_drafting_model == "claude-3-5-sonnet"
    assert cfg.default_planning_model == "o1-preview"
    assert cfg.default_extraction_model == "gpt-4o-mini"
    assert cfg.default_critique_model == "gemini-1.5-flash"
    assert cfg.default_copilot_model == "gpt-4o"
    assert cfg.daily_cost_limit_usd == Decimal("25.0000")
    assert cfg.monthly_cost_limit_usd == Decimal("75.0000")
    assert cfg.get_model_for_task("extraction") == "gpt-4o-mini"
    assert cfg.get_model_for_task("critique") == "gemini-1.5-flash"
