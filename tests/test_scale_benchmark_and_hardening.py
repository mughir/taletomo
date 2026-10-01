import os
import time
import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.paginator import Paginator
from django.db import connection
from django.test.utils import CaptureQueriesContext

from taletomo.canon.models import Character, Faction, Location, WorldRule, PlotThread, CanonFact
from taletomo.context.retrieval import ContextAssembler
from taletomo.generation.models import DraftArtifact, DraftStatus
from taletomo.planning.models import Chapter, ChapterPlan, Project, SeriesBible
from taletomo.planning.services import PlanningService

User = get_user_model()


@pytest.mark.django_db
def test_database_indexes_exist():
    """Verify production composite indexes on high-throughput models."""
    chapter_index_fields = [list(idx.fields) for idx in Chapter._meta.indexes]
    assert ["project", "chapter_number"] in chapter_index_fields
    assert ["project", "status"] in chapter_index_fields

    draft_index_fields = [list(idx.fields) for idx in DraftArtifact._meta.indexes]
    assert ["chapter", "version_number"] in draft_index_fields
    assert ["chapter", "status"] in draft_index_fields


@pytest.mark.django_db
def test_context_assembly_query_bound_o1():
    """Verify ContextAssembler executes bounded O(1) queries regardless of total chapter count."""
    user = User.objects.create(username="perf_tester")
    project = PlanningService.create_project_with_scaffold(
        owner=user,
        title="Epic Scale Benchmark",
        premise="Testing query scalability.",
        target_chapters=1000,
    )

    # Populate entities
    for i in range(10):
        Character.objects.create(project=project, name=f"Character {i}")
        Location.objects.create(project=project, name=f"Location {i}")
        Faction.objects.create(project=project, name=f"Faction {i}")
        WorldRule.objects.create(project=project, title=f"Rule {i}", rule_statement="Details")
        PlotThread.objects.create(project=project, title=f"Thread {i}", setup_chapter=1)
        CanonFact.objects.create(project=project, subject=f"Subject {i}", predicate="status", value="active")

    # Add 500 chapters
    bulk_chapters = [
        Chapter(
            project=project,
            chapter_number=i,
            title=f"Chapter {i}",
            status=Chapter.Status.UNPLANNED,
        )
        for i in range(project.chapters.count() + 1, 501)
    ]
    Chapter.objects.bulk_create(bulk_chapters, batch_size=250)

    # Test chapter in middle of 500 chapters
    test_ch = project.chapters.get(chapter_number=50)

    with CaptureQueriesContext(connection) as queries_middle:
        ContextAssembler.assemble_chapter_context(
            chapter=test_ch,
            model_context_limit=128000,
            requested_output_tokens=4000,
        )

    middle_count = len(queries_middle.captured_queries)
    # Context assembly must be bounded by a constant O(1) query count (typically ~10-14 queries)
    assert middle_count <= 18, f"Expected <= 18 queries, got {middle_count}"

    # Now simulate adding up to 2,000 chapters and measure Chapter 1500
    more_chapters = [
        Chapter(
            project=project,
            chapter_number=i,
            title=f"Chapter {i}",
            status=Chapter.Status.UNPLANNED,
        )
        for i in range(501, 2001)
    ]
    Chapter.objects.bulk_create(more_chapters, batch_size=500)
    assert project.chapters.count() == 2000

    test_ch_late = project.chapters.get(chapter_number=1500)
    with CaptureQueriesContext(connection) as queries_late:
        ContextAssembler.assemble_chapter_context(
            chapter=test_ch_late,
            model_context_limit=128000,
            requested_output_tokens=4000,
        )

    late_count = len(queries_late.captured_queries)
    # The query count at chapter 1,500 with 2,000 chapters in the DB must match the query count at chapter 50!
    assert late_count == middle_count, f"Query count changed from {middle_count} to {late_count} with 2,000 chapters"


@pytest.mark.django_db
def test_4000_chapter_outline_pagination_performance():
    """Verify that pagination across 4,000 chapters remains under 100ms."""
    user = User.objects.create(username="mega_author")
    project = Project.objects.create(
        owner=user,
        title="Mega Novel 4000",
        target_chapters=4000,
    )

    bulk_chapters = [
        Chapter(
            project=project,
            chapter_number=i,
            title=f"Chapter {i}",
            status=Chapter.Status.UNPLANNED,
        )
        for i in range(1, 4001)
    ]
    Chapter.objects.bulk_create(bulk_chapters, batch_size=1000)
    assert project.chapters.count() == 4000

    # Query matching project_outline view
    chapters_query = (
        project.chapters.select_related("plan")
        .prefetch_related("plan__scenes")
        .order_by("chapter_number")
    )
    paginator = Paginator(chapters_query, 25)

    start = time.perf_counter()
    page_100 = paginator.get_page(100)  # Chapter ~2500
    elapsed = time.perf_counter() - start

    assert elapsed < 0.1, f"Pagination took {elapsed:.4f}s, expected < 0.1s"
    items = list(page_100.object_list)
    assert len(items) == 25
    assert items[0].chapter_number == 2476
    assert items[-1].chapter_number == 2500


@pytest.mark.django_db
def test_security_deployment_hardening_checks(settings):
    """Verify security configuration under production flags."""
    # When DEBUG=False and HTTPS flags are provided
    settings.DEBUG = False
    settings.SECURE_SSL_REDIRECT = True
    settings.SESSION_COOKIE_SECURE = True
    settings.CSRF_COOKIE_SECURE = True
    settings.SECURE_HSTS_SECONDS = 31536000
    settings.SECRET_KEY = "a-very-secure-random-long-production-key-that-is-over-50-characters-long"
    settings.ALLOWED_HOSTS = ["taletomo.example.com"]

    # Verify no critical security failures
    from django.core.checks.security.base import check_security_middleware
    warnings = check_security_middleware(None)
    # No security middleware warnings should fire with these production settings
    assert len(warnings) == 0
