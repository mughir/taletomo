import json
import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from taletomo.canon.models import (
    Character,
    CharacterRelationship,
    CharacterRole,
)
from taletomo.context.retrieval import ContextAssembler
from taletomo.exporting.services import ExportService
from taletomo.generation.copilot import ProseCoPilotService
from taletomo.planning.models import Chapter, ChapterPlan, Project, ScenePlan, SeriesBible
from taletomo.planning.services import PlanningService

User = get_user_model()


class MockResponse:
    def __init__(self, content):
        self.content = content


class MockAdapter:
    def __init__(self, canned_response="Refined test prose."):
        self.canned_response = canned_response
        self.last_prompt = ""

    def generate_text(self, prompt, **kwargs):
        self.last_prompt = prompt
        return MockResponse(self.canned_response)


@pytest.mark.django_db
def test_character_model_and_relationship_creation():
    user = User.objects.create_user(username="charuser1", password="password")
    project = Project.objects.create(owner=user, title="Cast Test", slug="cast-test")

    char_a = Character.objects.create(
        project=project,
        name="Seraphina Nightshade",
        role=CharacterRole.PROTAGONIST,
        appearance="Silver hair, violet eyes, dark velvet travelling cloak.",
        dialogue_style="Eloquent, sharp cadence, uses celestial metaphors.",
        wounds_status="Right shoulder grazed by poisoned bolt.",
        goals="Recover the Sun Tear gemstone.",
        internal_need="Must overcome her fear of betrayal.",
        traits=["Pragmatic", "Fierce", "Perceptive"],
    )

    char_b = Character.objects.create(
        project=project,
        name="Malakar Stone",
        role=CharacterRole.ANTAGONIST,
        appearance="Imposing warrior with obsidian plate armor.",
        dialogue_style="Gravelly, blunt, speaks in threats.",
        goals="Subjugate the western marches.",
    )

    assert char_a.appearance.startswith("Silver hair")
    assert char_a.dialogue_style.startswith("Eloquent")
    assert char_a.is_alive is True

    # Create character relationship
    rel = CharacterRelationship.objects.create(
        project=project,
        source_character=char_a,
        target_character=char_b,
        relationship_type="Mortal Nemesis",
        description="Fought during the Siege of Aethelgard; Malakar betrayed Seraphina's mentor.",
        dynamic_status=CharacterRelationship.DynamicStatus.HOSTILE,
    )
    assert rel.relationship_type == "Mortal Nemesis"
    assert rel.dynamic_status == "hostile"
    assert str(rel) == "Seraphina Nightshade -> Mortal Nemesis -> Malakar Stone"


@pytest.mark.django_db
def test_context_assembler_injects_appearance_voice_and_relationships():
    user = User.objects.create_user(username="contextuser", password="password")
    project = Project.objects.create(owner=user, title="Context Project", slug="context-project")
    SeriesBible.objects.create(project=project, world_setting="A floating sky archipelago.")

    char1 = Character.objects.create(
        project=project,
        name="Kaelen",
        role=CharacterRole.PROTAGONIST,
        appearance="Tall wind-runner with sky-blue tattoos.",
        dialogue_style="Fast-talking, breezy, maritime slang.",
        goals="Navigate the storm gate.",
    )
    char2 = Character.objects.create(
        project=project,
        name="Lyra",
        role=CharacterRole.ALLY,
        appearance="Scholar in brass spectacles with grease stains.",
        dialogue_style="Analytical, precise, talks in probabilities.",
        goals="Fix the airship engine.",
    )
    CharacterRelationship.objects.create(
        project=project,
        source_character=char1,
        target_character=char2,
        relationship_type="Flight Navigator & Engineer",
        description="Longtime crewmates who bicker constantly.",
        dynamic_status=CharacterRelationship.DynamicStatus.FRIENDLY,
    )

    ch = Chapter.objects.create(
        project=project,
        chapter_number=1,
        title="Into the Storm Gate",
        status=Chapter.Status.PLANNED,
    )
    ChapterPlan.objects.create(
        chapter=ch,
        objectives=["Kaelen and Lyra pilot the airship through the barrier."],
        required_beats=["Lyra detects engine anomaly; Kaelen maneuvers."],
        prohibited_outcomes=["Do not crash."],
        continuity_requirements=[],
        target_words=2000,
    )

    pkg = ContextAssembler.assemble_chapter_context(ch, model_context_limit=128000)

    # Verify appearance and voice are injected
    assert "Appearance: Tall wind-runner with sky-blue tattoos." in pkg.user_prompt
    assert "Voice/Speech: Fast-talking, breezy, maritime slang." in pkg.user_prompt
    assert "Appearance: Scholar in brass spectacles with grease stains." in pkg.user_prompt
    assert "Voice/Speech: Analytical, precise, talks in probabilities." in pkg.user_prompt

    # Verify relationship is injected
    assert "Relationship: Kaelen is Flight Navigator & Engineer of Lyra" in pkg.user_prompt


@pytest.mark.django_db
def test_copilot_service_injects_character_voice_guidelines():
    user = User.objects.create_user(username="copilotuser", password="password")
    project = Project.objects.create(owner=user, title="Copilot Project", slug="copilot-project")
    Character.objects.create(
        project=project,
        name="Darius",
        dialogue_style="Aristocratic, haughty, refuses to use slang or contractions.",
    )

    ch = Chapter.objects.create(
        project=project,
        chapter_number=1,
        title="Chapter 1",
        pov_character_name="Darius",
    )

    adapter = MockAdapter(canned_response="'I shall not capitulate,' Darius replied.")
    res = ProseCoPilotService.polish_selection(
        user=user,
        project=project,
        chapter=ch,
        action="punch_up_dialogue",
        selected_text="I won't give up, said Darius.",
        custom_adapter=adapter,
    )
    assert res["suggested_text"] == "'I shall not capitulate,' Darius replied."
    assert "CHARACTER VOICE GUIDELINES:" in adapter.last_prompt
    assert "Darius (POV): Aristocratic, haughty, refuses to use slang or contractions." in adapter.last_prompt


@pytest.mark.django_db
def test_branching_clones_character_design_and_relationships():
    user = User.objects.create_user(username="branchcharuser", password="password")
    project = Project.objects.create(
        owner=user,
        title="Original Story",
        slug="orig-story",
        target_chapters=5,
    )
    c1 = Character.objects.create(
        project=project,
        name="Hero",
        appearance="Scarred veteran",
        dialogue_style="Quiet and stoic",
    )
    c2 = Character.objects.create(
        project=project,
        name="Villain",
        appearance="Robed sorcerer",
        dialogue_style="Grandiloquent",
    )
    CharacterRelationship.objects.create(
        project=project,
        source_character=c1,
        target_character=c2,
        relationship_type="Blood Oath",
        description="Bound by ancient pact.",
        dynamic_status=CharacterRelationship.DynamicStatus.COMPLEX,
    )

    # Branch story
    branched = PlanningService.branch_project(project, from_chapter=1, branch_name="Alternate Oath")
    assert branched.characters.count() == 2
    b_c1 = branched.characters.get(name="Hero")
    assert b_c1.appearance == "Scarred veteran"
    assert b_c1.dialogue_style == "Quiet and stoic"

    assert branched.character_relationships.count() == 1
    b_rel = branched.character_relationships.first()
    assert b_rel.source_character.name == "Hero"
    assert b_rel.target_character.name == "Villain"
    assert b_rel.relationship_type == "Blood Oath"
    assert b_rel.dynamic_status == "complex"


@pytest.mark.django_db
def test_export_and_restore_character_design_and_relationships():
    user = User.objects.create_user(username="exportcharuser", password="password")
    project = Project.objects.create(
        owner=user,
        title="Export Novel",
        slug="export-novel",
    )
    c1 = Character.objects.create(
        project=project,
        name="Aria",
        appearance="Golden hair",
        dialogue_style="Whimsical and melodic",
    )
    c2 = Character.objects.create(
        project=project,
        name="Theron",
        appearance="Dark armor",
        dialogue_style="Tactical and curt",
    )
    CharacterRelationship.objects.create(
        project=project,
        source_character=c1,
        target_character=c2,
        relationship_type="Chaperone & Charge",
        dynamic_status=CharacterRelationship.DynamicStatus.TENSE,
    )

    backup_json = ExportService.export_json_backup(project)
    assert "relationships" in backup_json["canon"]
    assert len(backup_json["canon"]["relationships"]) == 1
    rel_data = backup_json["canon"]["relationships"][0]
    assert rel_data["source_character_name"] == "Aria"
    assert rel_data["target_character_name"] == "Theron"

    # Restore in new project
    restored = ExportService.restore_from_json(owner=user, backup_data=backup_json)
    assert restored.characters.count() == 2
    r_aria = restored.characters.get(name="Aria")
    assert r_aria.appearance == "Golden hair"
    assert r_aria.dialogue_style == "Whimsical and melodic"

    assert restored.character_relationships.count() == 1
    r_rel = restored.character_relationships.first()
    assert r_rel.source_character.name == "Aria"
    assert r_rel.target_character.name == "Theron"
    assert r_rel.dynamic_status == "tense"


@pytest.mark.django_db
def test_character_web_views_crud_and_scene_tracking():
    user = User.objects.create_user(username="webcharuser", password="password")
    client = Client()
    client.force_login(user)

    project = Project.objects.create(
        owner=user,
        title="Web Cast Novel",
        slug="web-cast-novel",
        target_chapters=5,
    )

    # 1. Add character via POST
    add_url = reverse("taletomo:project_characters", kwargs={"project_id": project.id})
    resp = client.post(
        add_url,
        {
            "name": "Eldrin",
            "role": "protagonist",
            "aliases": "El, Sunblade",
            "traits": "Noble, Resolute",
            "appearance": "Sunken cheeks, bright golden eyes.",
            "dialogue_style": "Solemn and measured.",
            "wounds_status": "Severe mana burn on torso.",
            "goals": "Protect the frontier realm.",
            "internal_need": "Accept that sacrifice cannot save everyone.",
            "is_alive": "1",
        },
    )
    assert resp.status_code == 302
    assert project.characters.count() == 1
    eldrin = project.characters.first()
    assert eldrin.name == "Eldrin"
    assert eldrin.appearance == "Sunken cheeks, bright golden eyes."
    assert "Sunblade" in eldrin.aliases
    assert "Resolute" in eldrin.traits

    # Add second character
    client.post(
        add_url,
        {
            "name": "Valeria",
            "role": "ally",
            "is_alive": "1",
        },
    )
    assert project.characters.count() == 2
    valeria = project.characters.get(name="Valeria")

    # 2. Add relationship
    rel_add_url = reverse("taletomo:project_character_relationship_add", kwargs={"project_id": project.id})
    resp_rel = client.post(
        rel_add_url,
        {
            "source_character_id": str(eldrin.id),
            "target_character_id": str(valeria.id),
            "relationship_type": "Commander & Lieutenant",
            "description": "Served together for five years.",
            "dynamic_status": "friendly",
        },
    )
    assert resp_rel.status_code == 302
    assert project.character_relationships.count() == 1

    # 3. Create chapter and scene with Eldrin
    ch = Chapter.objects.create(
        project=project,
        chapter_number=1,
        title="Chapter 1",
        pov_character_name="Eldrin",
    )
    plan = ChapterPlan.objects.create(chapter=ch, objectives=["Defend the bridge."])
    ScenePlan.objects.create(
        chapter_plan=plan,
        scene_order=1,
        objective="Hold bridge",
        characters=["Eldrin", "Valeria"],
    )

    # 4. View cast and check tracking metrics
    get_resp = client.get(add_url)
    assert get_resp.status_code == 200
    assert "Eldrin" in get_resp.content.decode()
    assert "Commander &amp; Lieutenant" in get_resp.content.decode()
    assert "Scene &amp; POV Tracker" in get_resp.content.decode()

    # 5. Edit character
    edit_url = reverse(
        "taletomo:project_character_edit",
        kwargs={"project_id": project.id, "character_id": eldrin.id},
    )
    edit_get = client.get(edit_url)
    assert edit_get.status_code == 200

    edit_post = client.post(
        edit_url,
        {
            "name": "Eldrin Sunblade",
            "role": "protagonist",
            "aliases": "El",
            "traits": "Noble, Fierce",
            "appearance": "Battle-scarred veteran.",
            "dialogue_style": "Deep, authoritative.",
            "wounds_status": "Healed.",
            "goals": "Ascend the throne.",
            "internal_need": "Trust his companions.",
            "is_alive": "1",
        },
    )
    assert edit_post.status_code == 302
    eldrin.refresh_from_db()
    assert eldrin.name == "Eldrin Sunblade"
    assert eldrin.wounds_status == "Healed."
    assert eldrin.dialogue_style == "Deep, authoritative."

    # 6. Delete relationship
    rel = project.character_relationships.first()
    del_rel_url = reverse(
        "taletomo:project_character_relationship_delete",
        kwargs={"project_id": project.id, "relationship_id": rel.id},
    )
    client.post(del_rel_url)
    assert project.character_relationships.count() == 0

    # 7. Delete character
    del_char_url = reverse(
        "taletomo:project_character_delete",
        kwargs={"project_id": project.id, "character_id": valeria.id},
    )
    client.post(del_char_url)
    assert project.characters.count() == 1
