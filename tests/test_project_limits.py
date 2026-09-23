import pytest
from django.contrib.auth import get_user_model
from taletomo.planning.services import PlanningService

User = get_user_model()


@pytest.mark.django_db
@pytest.mark.parametrize("chapter_count", [0, 4001])
def test_project_creation_rejects_chapter_counts_outside_product_cap(chapter_count):
    owner = User.objects.create_user(username=f"chapter_limit_{chapter_count}")

    with pytest.raises(ValueError, match="1 and 4,000"):
        PlanningService.create_project_with_scaffold(
            owner=owner,
            title=f"Invalid chapter count {chapter_count}",
            premise="Boundary validation",
            target_chapters=chapter_count,
        )


@pytest.mark.django_db
@pytest.mark.parametrize("word_count", [499, 10001])
def test_project_creation_rejects_chapter_word_targets_outside_product_bounds(word_count):
    owner = User.objects.create_user(username=f"word_limit_{word_count}")

    with pytest.raises(ValueError, match="500 and 10,000"):
        PlanningService.create_project_with_scaffold(
            owner=owner,
            title=f"Invalid word target {word_count}",
            premise="Boundary validation",
            target_words_per_chapter=word_count,
        )
