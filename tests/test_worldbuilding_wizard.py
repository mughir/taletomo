import json
import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from taletomo.canon.models import Character, Faction, WorldRule
from taletomo.planning.models import Project, SeriesBible, SeriesSpine
from taletomo.planning.wizard import WorldbuildingWizardService
from taletomo.providers.adapters import FakeProviderAdapter
from taletomo.providers.models import ProviderConfig, ProviderType

User = get_user_model()


@pytest.fixture
def wizard_user(db):
    user = User.objects.create_user(username="wizard_architect", password="password123")
    cfg = ProviderConfig.objects.create(
        user=user,
        name="Fake Provider",
        provider_type=ProviderType.FAKE,
        is_default=True,
    )
    return user, FakeProviderAdapter(cfg)


@pytest.mark.django_db
def test_wizard_proposals_service(wizard_user):
    user, adapter = wizard_user

    # 1. Bible proposals
    bible_res = WorldbuildingWizardService.propose_world_bible(
        premise="A rogue machinist uncovers a reverse-ticking clockwork relic.",
        genre="Steampunk",
        tone="Tactile, Tense",
        adapter=adapter,
    )
    assert "world_setting" in bible_res
    assert len(bible_res.get("magic_tech_rules", [])) >= 2

    # 2. Factions proposals
    factions_res = WorldbuildingWizardService.propose_factions(
        premise="A rogue machinist uncovers a reverse-ticking clockwork relic.",
        genre="Steampunk",
        world_setting=bible_res["world_setting"],
        adapter=adapter,
    )
    assert len(factions_res) >= 2
    assert "name" in factions_res[0]
    assert "goals" in factions_res[0]

    # 3. Characters proposals
    chars_res = WorldbuildingWizardService.propose_characters(
        premise="A rogue machinist uncovers a reverse-ticking clockwork relic.",
        genre="Steampunk",
        factions=factions_res,
        protagonist_type="Underdog Mechanist",
        adapter=adapter,
    )
    assert len(chars_res) >= 2
    assert any(c.get("role") == "Protagonist" for c in chars_res)
    assert any(c.get("role") == "Antagonist" for c in chars_res)

    # 4. Spine proposals
    spine_res = WorldbuildingWizardService.propose_spine_and_arcs(
        premise="A rogue machinist uncovers a reverse-ticking clockwork relic.",
        target_chapters=50,
        adapter=adapter,
    )
    assert "series_promise" in spine_res
    assert len(spine_res.get("major_milestones", [])) >= 4


@pytest.mark.django_db
def test_wizard_finalize_creates_complete_project(wizard_user):
    user, adapter = wizard_user

    wizard_data = {
        "project": {
            "title": "Aether & Gear",
            "premise": "An apprentice mechanist fights to free his colony.",
            "target_chapters": 24,
            "genre": "Steampunk",
            "tone": "Heroic, Gritty",
            "protagonist_type": "Underdog",
            "novel_tags": "System, Overpowered Protagonist",
            "target_words_per_chapter": 2500,
        },
        "bible": {
            "world_setting": "Vertical city-spires connected by cable-cars.",
            "central_conflict": "The Foundry Guild demands total submission.",
            "magic_tech_rules": [
                "Steam pressure must balance ambient ether heat.",
                "Overheating an ether core causes memory amnesia.",
            ],
        },
        "factions": [
            {
                "name": "Foundry Guild",
                "goals": "Absolute energy control.",
                "resources": "Iron guards and steam engines.",
                "alliances": ["Bankers"],
            },
            {
                "name": "Soot Rebels",
                "goals": "Liberate the lower vents.",
                "resources": "Scrap bombs and scouts.",
                "alliances": [],
            },
        ],
        "characters": [
            {
                "name": "Jaxen",
                "role": "Protagonist",
                "archetype": "Underdog",
                "psych_flaw": "Rash temper",
                "goals": "Free his brother",
                "aliases": ["The Torch"],
            },
            {
                "name": "Vane",
                "role": "Antagonist",
                "archetype": "Governor",
                "psych_flaw": "Megalomaniac",
                "goals": "Sovereign power",
                "aliases": [],
            },
        ],
        "spine": {
            "series_promise": "From the vents to the sovereign skies.",
            "ending_direction": "The spires are decentralized.",
            "major_milestones": [{"chapter": 12, "event": "Vents revolt"}],
        },
    }

    project = WorldbuildingWizardService.finalize_wizard(user, wizard_data, adapter=adapter)

    assert project.id is not None
    assert project.title == "Aether & Gear"
    assert project.owner == user
    assert project.target_chapters == 24
    assert project.target_words_per_chapter == 2500

    # Bible & Rules verified
    assert hasattr(project, "bible")
    assert project.rules.count() == 2
    assert project.rules.filter(rule_statement__contains="Steam pressure").exists()

    # Factions verified
    assert project.factions.count() == 2
    assert project.factions.filter(name="Foundry Guild").exists()

    # Characters verified
    from taletomo.canon.models import CharacterRole

    assert project.characters.count() == 2
    protagonist = project.characters.get(name="Jaxen")
    assert protagonist.role == CharacterRole.PROTAGONIST
    assert "Underdog" in protagonist.traits



@pytest.mark.django_db
def test_wizard_http_endpoints_and_api(client, wizard_user):
    user, _ = wizard_user
    wizard_url = reverse("taletomo:project_wizard")
    api_url = reverse("taletomo:project_wizard_api")

    # 1. Unauthenticated -> 302
    resp = client.get(wizard_url)
    assert resp.status_code == 302

    # 2. Authenticated GET wizard page
    client.force_login(user)
    resp = client.get(wizard_url)
    assert resp.status_code == 200
    assert b"Worldbuilding Wizard" in resp.content

    # 3. Test Wizard API AJAX calls
    api_resp = client.post(
        api_url,
        data=json.dumps({"action": "propose_bible", "premise": "Test wizard prompt", "genre": "SciFi"}),
        content_type="application/json",
    )
    assert api_resp.status_code == 200
    data = api_resp.json()
    assert data["status"] == "ok"
    assert "world_setting" in data["data"]

    # 4. Test Finalize POST
    full_payload = {
        "project": {
            "title": "Neon Horizons",
            "premise": "A hacker discovers the digital ghost of a forgotten god.",
            "target_chapters": 10,
        },
        "bible": {"world_setting": "Cybernetic megalopolis"},
        "factions": [{"name": "NetRunners", "goals": "Freedom", "resources": "Terminals"}],
        "characters": [{"name": "Cypher", "role": "Protagonist"}],
        "spine": {"series_promise": "A journey into the machine code."},
    }

    post_resp = client.post(wizard_url, {"wizard_payload": json.dumps(full_payload)})
    assert post_resp.status_code == 302

    new_project = Project.objects.get(title="Neon Horizons")
    assert new_project.owner == user
    assert new_project.factions.count() == 1
