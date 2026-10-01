import pytest
from django.contrib.auth import get_user_model

from taletomo.canon.models import Character, Item, Location
from taletomo.consistency.checker import ContinuityChecker, FindingCategory, FindingSeverity
from taletomo.context.retrieval import ContextAssembler
from taletomo.exporting.services import ExportService
from taletomo.generation.models import GenerationJob
from taletomo.generation.pipeline import GenerationPipeline
from taletomo.planning.models import Chapter, ChapterPlan, Project, ScenePlan
from taletomo.planning.services import PlanningService
from taletomo.planning.wizard import WorldbuildingWizardService
from taletomo.providers.adapters import FakeProviderAdapter
from taletomo.providers.models import ProviderConfig, ProviderType

User = get_user_model()


@pytest.fixture
def audit_fixture(db):
    user = User.objects.create_user(username="auditor", password="password123")
    cfg = ProviderConfig.objects.create(
        user=user,
        provider_type=ProviderType.FAKE,
        encrypted_api_key="audit-key",
    )
    project = PlanningService.create_project_with_scaffold(
        owner=user,
        title="Chronicles of the Broken Spire",
        premise="A saga of ruined relics and fractured magic.",
        target_chapters=10,
    )
    ch1 = project.chapters.get(chapter_number=1)
    ch2 = project.chapters.get(chapter_number=2)
    ch3 = project.chapters.get(chapter_number=3)

    return project, ch1, ch2, ch3, user, cfg


@pytest.mark.django_db
def test_temporal_destroyed_item_prior_chapter_allowed(audit_fixture):
    """Verify that an item destroyed in Chapter 3 is allowed in Chapter 1 and 2, but blocked in Chapter 4."""
    project, ch1, ch2, _, user, _ = audit_fixture

    # Item destroyed in Chapter 3
    blade = Item.objects.create(
        project=project,
        name="Solar Fang",
        is_destroyed=True,
        destroyed_at_chapter=3,
    )

    # In Chapter 1 (prior to destruction), wielding it is allowed!
    prose_ch1 = "Alaric drew the Solar Fang and slashed at the darkness."
    findings_ch1 = ContinuityChecker.run_deterministic_checks(ch1, prose_ch1)
    item_blockers_ch1 = [
        f for f in findings_ch1 if f.category == FindingCategory.TIMELINE and "Solar Fang" in f.claim
    ]
    assert len(item_blockers_ch1) == 0

    # In Chapter 4 (after destruction), wielding it is strictly blocked!
    ch4 = project.chapters.get(chapter_number=4)
    prose_ch4 = "Alaric drew the Solar Fang and raised it high."
    findings_ch4 = ContinuityChecker.run_deterministic_checks(ch4, prose_ch4)
    item_blockers_ch4 = [
        f for f in findings_ch4 if f.category == FindingCategory.TIMELINE and "Solar Fang" in f.claim
    ]
    assert len(item_blockers_ch4) == 1
    assert item_blockers_ch4[0].severity == FindingSeverity.BLOCKER


@pytest.mark.django_db
def test_backup_and_branch_include_item_parity(audit_fixture):
    """Verify full Item parity across JSON backup export/restore and timeline branching."""
    project, _, _, _, user, _ = audit_fixture

    hero = Character.objects.create(project=project, name="Kaelen")
    sanctum = Location.objects.create(project=project, name="Obsidian Sanctum", coord_x=10.0, coord_y=20.0)

    amulet = Item.objects.create(
        project=project,
        name="Amulet of Dawn",
        description="A crystalline talisman glowing with solar power.",
        current_holder=hero,
        current_location=sanctum,
        is_destroyed=False,
    )

    # 1. Test Backup Export and Restore
    backup = ExportService.export_json_backup(project)
    assert "items" in backup["canon"]
    assert len(backup["canon"]["items"]) == 1
    it_entry = backup["canon"]["items"][0]
    assert it_entry["name"] == "Amulet of Dawn"
    assert it_entry["current_holder_name"] == "Kaelen"
    assert it_entry["current_location_name"] == "Obsidian Sanctum"

    restored = ExportService.restore_from_json(owner=user, backup_data=backup)
    assert restored.items.count() == 1
    restored_item = restored.items.first()
    assert restored_item.name == "Amulet of Dawn"
    assert restored_item.current_holder.name == "Kaelen"
    assert restored_item.current_location.name == "Obsidian Sanctum"

    # 2. Test Timeline Branching
    branched = PlanningService.branch_project(
        source_project=project,
        from_chapter=2,
        branch_name="Solar Divergence",
    )
    assert branched.items.count() == 1
    branched_item = branched.items.first()
    assert branched_item.name == "Amulet of Dawn"
    assert branched_item.current_holder.name == "Kaelen"
    assert branched_item.current_holder.project == branched
    assert branched_item.current_location.name == "Obsidian Sanctum"
    assert branched_item.current_location.project == branched


@pytest.mark.django_db
def test_context_assembler_includes_items_in_state(audit_fixture):
    """Verify that ContextAssembler includes Item records in canonical state prompt."""
    project, ch1, _, _, user, cfg = audit_fixture
    adapter = FakeProviderAdapter(cfg)

    loc = Location.objects.create(project=project, name="High Citadel")
    char = Character.objects.create(project=project, name="Valeria")
    Item.objects.create(
        project=project,
        name="Moon-Forged Greatsword",
        description="A massive silver broadsword etched with celestial glyphs.",
        current_holder=char,
        current_location=loc,
        is_destroyed=False,
    )

    pkg = ContextAssembler.assemble_chapter_context(
        chapter=ch1,
        model_context_limit=128000,
        requested_output_tokens=2000,
        adapter=adapter,
    )

    assert "Item: Moon-Forged Greatsword" in pkg.user_prompt
    assert "Held by: Valeria" in pkg.user_prompt


@pytest.mark.django_db
def test_wizard_finalize_creates_locations(audit_fixture):
    """Verify that WorldbuildingWizardService.finalize_wizard instantiates locations from payload."""
    _, _, _, _, user, cfg = audit_fixture
    adapter = FakeProviderAdapter(cfg)

    wizard_payload = {
        "project": {
            "title": "Citadel of Whispers",
            "premise": "Spies in a sunken kingdom.",
            "target_chapters": 30,
            "genre": "Fantasy / Mystery",
        },
        "bible": {
            "world_setting": "A labyrinthine metropolis built into limestone sea caverns.",
            "magic_tech_rules": ["Echo crystals record ambient conversations."],
        },
        "characters": [
            {"name": "Iris Veil", "role": "protagonist", "archetype": "Master Thief"}
        ],
        "locations": [
            {
                "name": "The Sunken Vault",
                "description": "Submerged archives guarded by tidal gates.",
                "region": "The Under-Reefs",
                "coord_x": 45.5,
                "coord_y": 80.0,
            }
        ],
    }

    proj = WorldbuildingWizardService.finalize_wizard(
        user=user,
        wizard_data=wizard_payload,
        adapter=adapter,
    )

    assert proj.locations.count() == 1
    loc = proj.locations.first()
    assert loc.name == "The Sunken Vault"
    assert loc.region == "The Under-Reefs"
    assert loc.coord_x == 45.5
    assert loc.coord_y == 80.0


@pytest.mark.django_db
def test_pipeline_multi_scene_checks_composite_prose(audit_fixture):
    """Verify that multi-scene chapter generation audits the entire composite prose, detecting violations in scene 1."""
    project, ch1, _, _, user, cfg = audit_fixture

    # Protagonist with shattered wrist
    alaric = Character.objects.create(
        project=project,
        name="Alaric Vance",
        wounds_status="shattered left wrist",
    )

    plan = ch1.plan
    ScenePlan.objects.create(
        chapter_plan=plan,
        scene_order=1,
        objective="Escaping through the high wall using both hands",
    )
    ScenePlan.objects.create(
        chapter_plan=plan,
        scene_order=2,
        objective="Resting in a quiet alley",
    )

    # Custom adapter where scene 1 contains the injury violation and scene 2 is completely clean
    class MultiSceneViolationAdapter(FakeProviderAdapter):
        def __init__(self, cfg):
            super().__init__(cfg)
            self.scene_count = 0

        def generate_text(self, prompt, **kwargs):
            self.scene_count += 1
            resp = super().generate_text("task: copilot", **kwargs)
            if self.scene_count == 1:
                return resp.model_copy(
                    update={
                        "content": "Alaric gripped the icy ledge with both hands, hoisting his weight up the sheer parapet."
                    }
                )
            else:
                return resp.model_copy(
                    update={
                        "content": "He sat quietly in the dark alleyway, breathing slowly until dawn."
                    }
                )

    adapter = MultiSceneViolationAdapter(cfg)
    job = GenerationJob.objects.create(
        user=user,
        project=project,
        target_chapter_id=ch1.id,
    )

    draft = GenerationPipeline.execute_chapter_generation(
        job=job,
        worker_id="audit-worker",
        custom_adapter=adapter,
    )

    assert draft is not None
    # Verify that the violation from scene 1 was detected because composite_prose was audited!
    findings = ch1.continuity_findings.filter(category=FindingCategory.INJURY)
    assert findings.count() >= 1
    f = findings.first()
    assert f.severity == FindingSeverity.BLOCKER
    assert "both hands" in f.claim.lower()

