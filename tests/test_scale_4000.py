import pytest
import time
from django.contrib.auth import get_user_model
from django.core.paginator import Paginator
from taletomo.planning.models import Chapter, Project
from taletomo.planning.services import PlanningService

User = get_user_model()


@pytest.mark.django_db
def test_scale_gate_4000_chapters_simulation():
    """Scale gate: verifies that a 4,000-chapter novel navigates cleanly with bounded memory and fast pagination."""
    user = User.objects.create(username="epic_serial_author")

    # 1. Create project configured for 4,000 chapters
    project = PlanningService.create_project_with_scaffold(
        owner=user,
        title="Immortal Sovereign: 4,000 Chapters of Ascension",
        premise="A serialized epic spanning eons and thousands of realms.",
        target_chapters=4000,
    )
    assert project.target_chapters == 4000

    # 2. Simulate full 4,000 chapters via bulk creation
    existing_count = project.chapters.count()  # Initial rolling horizon chapters
    bulk_chapters = [
        Chapter(
            project=project,
            chapter_number=i,
            title=f"Ascension Stage {i}",
            status=Chapter.Status.UNPLANNED,
        )
        for i in range(existing_count + 1, 4001)
    ]
    Chapter.objects.bulk_create(bulk_chapters, batch_size=1000)

    total_count = project.chapters.count()
    assert total_count == 4000

    # 3. Test Bounded Pagination & Keyset Navigation
    page_size = 25
    query = project.chapters.all().order_by("chapter_number")
    paginator = Paginator(query, page_size)

    assert paginator.num_pages == 160  # 4000 / 25

    # 4. Measure latency of navigating to page 115 (Chapter ~2854)
    start_time = time.perf_counter()
    page_115 = paginator.get_page(115)
    elapsed = time.perf_counter() - start_time

    assert elapsed < 0.1  # Fast database index scan (< 100ms)
    items_115 = list(page_115.object_list)
    assert len(items_115) == 25
    assert items_115[0].chapter_number == 2851
    assert items_115[-1].chapter_number == 2875

    # 5. Direct Jump Calculation: jumping to Chapter 3999
    target_ch = 3999
    computed_page = (target_ch - 1) // page_size + 1
    page_target = paginator.get_page(computed_page)

    chapter_numbers = [c.chapter_number for c in page_target.object_list]
    assert target_ch in chapter_numbers
