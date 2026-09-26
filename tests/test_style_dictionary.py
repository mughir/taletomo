import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from taletomo.context.retrieval import ContextAssembler
from taletomo.planning.models import Project
from taletomo.planning.services import PlanningService
from taletomo.taxonomy.models import StyleField, StyleTerm
from taletomo.taxonomy.seed_data import BUILTIN_TERMS
User = get_user_model()


@pytest.mark.django_db
def test_builtin_dictionary_is_seeded_with_definitions_and_examples():
    assert len(BUILTIN_TERMS) >= 30
    for field, name, definition, example in BUILTIN_TERMS:
        term = StyleTerm.objects.get(field=field, name=name)
        assert term.is_builtin
        assert term.definition
        assert term.example
    # Every style axis has at least one builtin term.
    for field, _label in StyleField.choices:
        assert StyleTerm.objects.filter(field=field, is_builtin=True).exists()


@pytest.mark.django_db
def test_resolve_for_project_matches_comma_and_slash_tokens():
    user = User.objects.create_user(username="resolver_author")
    project = PlanningService.create_project_with_scaffold(
        owner=user,
        title="Resolver Chronicle",
        premise="Style resolution fixture",
        genre="Fantasy / Xianxia",
        subgenre="Court Intrigue",
        tone="Grim, Mysterious",
        pov="Third Person Limited",
        tense="Past Tense",
        pacing="Balanced",
    )

    resolved = StyleTerm.resolve_for_project(project)
    by_name = {term.name: term for term in resolved}

    assert {"Fantasy", "Xianxia", "Court Intrigue", "Grim", "Mysterious", "Third Person Limited", "Past Tense", "Balanced"} <= set(by_name)
    assert by_name["Xianxia"].field == StyleField.GENRE
    assert by_name["Court Intrigue"].field == StyleField.SUBGENRE
    assert by_name["Grim"].field == StyleField.TONE


@pytest.mark.django_db
def test_unmatched_custom_values_simply_do_not_contribute():
    user = User.objects.create_user(username="custom_style_author")
    project = PlanningService.create_project_with_scaffold(
        owner=user,
        title="Custom Style Chronicle",
        premise="Free-typed styles stay valid",
        genre="Weird Western",
    )

    resolved = StyleTerm.resolve_for_project(project)
    names = {term.name for term in resolved}
    assert "Weird Western" not in names  # no builtin match — no crash, no term
    # Other axes keep resolving against their defaults.
    assert {"Epic", "Mysterious", "Third Person Limited", "Past Tense", "Balanced"} <= names


@pytest.mark.django_db
def test_style_definitions_and_examples_reach_the_generation_prompt():
    user = User.objects.create_user(username="style_prompt_author")
    project = PlanningService.create_project_with_scaffold(
        owner=user,
        title="Xianxia Prompt Chronicle",
        premise="Style guidance in prompts",
        genre="Xianxia",
        tone="Grim",
    )
    chapter = project.chapters.get(chapter_number=1)

    xianxia = StyleTerm.objects.get(field=StyleField.GENRE, name="Xianxia")
    package = ContextAssembler.assemble_chapter_context(chapter)

    assert "Style Dictionary" in package.user_prompt
    assert xianxia.definition.split()[0] in package.user_prompt  # sanity
    assert "Chinese cultivation fantasy" in package.user_prompt
    assert "sect tournament" in package.user_prompt  # example text present
    manifest_ids = [entry["id"] for entry in package.manifest.source_entries]
    assert "style-dictionary" in manifest_ids
    style_entry = next(e for e in package.manifest.source_entries if e["id"] == "style-dictionary")
    assert style_entry["category"] == "constraints"
    assert style_entry["tokens"] > 0


@pytest.mark.django_db
def test_user_added_term_guides_owners_projects_but_not_others():
    author = User.objects.create_user(username="term_author")
    other = User.objects.create_user(username="term_other")

    client = Client()
    client.force_login(author)
    response = client.post(
        reverse("taletomo:style_dictionary"),
        {
            "field": StyleField.GENRE,
            "name": "Weird Western",
            "definition": "Frontier law meets hexfire; hexes are contracts and bullets are legal tender.",
            "example": "The sheriff's badge is a ward, and it just stopped working.",
        },
        follow=True,
    )
    assert response.status_code == 200
    term = StyleTerm.objects.get(field=StyleField.GENRE, name="Weird Western")
    assert not term.is_builtin
    assert term.created_by == author

    author_project = PlanningService.create_project_with_scaffold(
        owner=author, title="Hexfire Frontier", premise="Own term visible", genre="Weird Western"
    )
    other_project = PlanningService.create_project_with_scaffold(
        owner=other, title="Stranger's Frontier", premise="Other user isolation", genre="Weird Western"
    )

    own_chapter = author_project.chapters.get(chapter_number=1)
    own_package = ContextAssembler.assemble_chapter_context(own_chapter)
    assert "Frontier law meets hexfire" in own_package.user_prompt

    other_chapter = other_project.chapters.get(chapter_number=1)
    other_package = ContextAssembler.assemble_chapter_context(other_chapter)
    assert "Frontier law meets hexfire" not in other_package.user_prompt


@pytest.mark.django_db
def test_dictionary_page_add_duplicate_and_delete_rules():
    user = User.objects.create_user(username="dict_manager")
    client = Client()
    client.force_login(user)
    url = reverse("taletomo:style_dictionary")

    # Add
    response = client.post(
        url,
        {"field": StyleField.TONE, "name": "Hopepunk", "definition": "Hope as an act of defiance.", "example": ""},
        follow=True,
    )
    assert response.status_code == 200
    term = StyleTerm.objects.get(field=StyleField.TONE, name="Hopepunk")
    assert term.created_by == user

    # Duplicate (case-insensitive slug) is rejected
    client.post(url, {"field": StyleField.TONE, "name": "hopepunk", "definition": "Again."}, follow=True)
    assert StyleTerm.objects.filter(field=StyleField.TONE, name__iexact="hopepunk").count() == 1

    # Missing definition is rejected
    client.post(url, {"field": StyleField.TONE, "name": "No Definition"}, follow=True)
    assert not StyleTerm.objects.filter(name="No Definition").exists()

    # Builtin terms cannot be removed
    builtin = StyleTerm.objects.filter(is_builtin=True).first()
    client.post(url, {"action": "delete", "term_id": str(builtin.id)}, follow=True)
    assert StyleTerm.objects.filter(id=builtin.id).exists()

    # Own terms can be removed
    client.post(url, {"action": "delete", "term_id": str(term.id)}, follow=True)
    assert not StyleTerm.objects.filter(id=term.id).exists()

    # Another user's term is not removable
    stranger_term = StyleTerm.objects.create(
        field=StyleField.TONE, name="Stranger Tone", definition="d", created_by=None
    )
    StyleTerm.objects.filter(id=stranger_term.id).update(is_builtin=False)
    client.post(url, {"action": "delete", "term_id": str(stranger_term.id)}, follow=True)
    assert StyleTerm.objects.filter(id=stranger_term.id).exists()


@pytest.mark.django_db
def test_dictionary_page_lists_builtin_and_own_terms_only():
    owner = User.objects.create_user(username="dict_owner")
    outsider_term = StyleTerm.objects.create(
        field=StyleField.GENRE,
        name="Secret Genre",
        definition="Only the creator sees this.",
        created_by=User.objects.create_user(username="dict_someone_else"),
    )

    client = Client()
    client.force_login(owner)
    response = client.get(reverse("taletomo:style_dictionary"))
    content = response.content.decode()
    assert "Fantasy" in content  # builtin visible
    assert "Secret Genre" not in content  # someone else's term not listed


@pytest.mark.django_db
def test_project_creation_form_offers_dictionary_choices_and_accepts_values():
    user = User.objects.create_user(username="form_author")
    client = Client()
    client.force_login(user)

    response = client.get(reverse("taletomo:project_new"))
    content = response.content.decode()
    # Multi axes render as chip pickers fed by a JSON term blob; single axes
    # keep datalist suggestions.
    assert 'id="style-terms-json"' in content
    assert 'data-field="genre"' in content
    assert 'data-field="tone"' in content
    assert 'data-field="subgenre"' in content
    # Protagonist picker merges archetypes and traits; tags picker present.
    assert 'data-terms-fields="protagonist,protagonist_trait"' in content
    assert 'data-field="tags"' in content
    assert "datalist" in content
    assert 'value="Slow Burn"' in content  # pacing datalist
    assert "Underdog" in content  # protagonist archetypes in the terms blob
    assert "Gender Bender" in content  # novel tags in the terms blob
    assert "Xianxia" in content  # genre suggestions in the terms blob

    response = client.post(
        reverse("taletomo:project_new"),
        {
            "title": "Dictionary Form Novel",
            "premise": "Created through dictionary choices",
            "genre": "Fantasy / Xianxia",
            "subgenre": "Court Intrigue, Progression Fantasy",
            "tone": "Grim, Mysterious",
            "pov": "First Person",
            "tense": "Present Tense",
            "pacing": "Slow Burn",
            "protagonist_type": "Trickster, Female Protagonist",
            "novel_tags": "Gender Bender, Reincarnation",
            "target_chapters": "50",
            "length_preset": "standard",
        },
        follow=True,
    )
    assert response.status_code == 200
    project = Project.objects.get(title="Dictionary Form Novel")
    assert project.genre == "Fantasy / Xianxia"
    assert project.subgenre == "Court Intrigue, Progression Fantasy"
    assert project.pacing == "Slow Burn"
    assert project.tense == "Present Tense"
    assert project.protagonist_type == "Trickster, Female Protagonist"
    assert project.novel_tags == "Gender Bender, Reincarnation"


@pytest.mark.django_db
def test_protagonist_axis_is_seeded_resolved_and_injected_into_prompts():
    user = User.objects.create_user(username="protagonist_author")
    project = PlanningService.create_project_with_scaffold(
        owner=user,
        title="Trickster Chronicle",
        premise="Protagonist axis fixture",
        protagonist_type="Trickster",
    )
    chapter = project.chapters.get(chapter_number=1)

    resolved = {term.name: term for term in StyleTerm.resolve_for_project(project)}
    assert "Trickster" in resolved
    assert resolved["Trickster"].field == StyleField.PROTAGONIST

    package = ContextAssembler.assemble_chapter_context(chapter)
    assert "Protagonist Type — Trickster" in package.user_prompt
    assert "plans nested inside plans" in package.user_prompt  # example text
    style_entry = next(
        e for e in package.manifest.source_entries if e["id"] == "style-dictionary"
    )
    assert style_entry["category"] == "constraints"


@pytest.mark.django_db
def test_protagonist_traits_and_novel_tags_mix_into_prompts():
    user = User.objects.create_user(username="tags_author")
    project = PlanningService.create_project_with_scaffold(
        owner=user,
        title="Gender Bender Chronicle",
        premise="Trait and tag mixing fixture",
        protagonist_type="Trickster, Female Protagonist",
        novel_tags="Gender Bender, Transmigration",
    )
    chapter = project.chapters.get(chapter_number=1)

    resolved = {term.name: term.field for term in StyleTerm.resolve_for_project(project)}
    # Archetype and gender trait resolve from the protagonist value; tropes
    # resolve from the tags value.
    assert resolved["Trickster"] == StyleField.PROTAGONIST
    assert resolved["Female Protagonist"] == StyleField.PROTAGONIST_TRAIT
    assert resolved["Gender Bender"] == StyleField.TAGS
    assert resolved["Transmigration"] == StyleField.TAGS

    package = ContextAssembler.assemble_chapter_context(chapter)
    assert "Protagonist Trait — Female Protagonist" in package.user_prompt
    assert "Novel Tags — Gender Bender" in package.user_prompt
    assert "body swap, transmigration, disguise, or transition" in package.user_prompt
    assert "the novel she abandoned at chapter twelve" in package.user_prompt  # example
