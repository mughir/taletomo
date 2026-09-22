from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from taletomo.canon.models import Character, Location, PlotThread, WorldRule
from taletomo.canon.services import CanonService
from taletomo.generation.models import GenerationJob
from taletomo.generation.pipeline import GenerationPipeline
from taletomo.planning.models import Chapter, ChapterPlan, Project, ScenePlan
from taletomo.planning.services import PlanningService
from taletomo.providers.adapters import FakeProviderAdapter
from taletomo.providers.models import ProviderConfig, ProviderType

User = get_user_model()


class Command(BaseCommand):
    help = "Seeds a complete realistic demonstration novel for TaleTomo."

    def handle(self, *args, **options):
        self.stdout.write("Seeding demo novel...")
        user, _ = User.objects.get_or_create(username="author", defaults={"email": "author@taletomo.local"})

        # Setup Default Fake Provider
        provider, _ = ProviderConfig.objects.get_or_create(
            name="Tomo Built-in AI (Deterministic)",
            defaults={
                "provider_type": ProviderType.FAKE,
                "is_active": True,
                "is_default": True,
                "default_drafting_model": "mock-drafting-v1",
            },
        )

        project = PlanningService.create_project_with_scaffold(
            owner=user,
            title="The Alchemist's Return",
            premise="A disgraced royal alchemist must navigate lethal court politics and forbidden arcana to clear his name.",
            target_chapters=100,
            genre="Fantasy / Xianxia / Court Intrigue",
            tone="Grim, Strategic, Mysterious",
            pov="Third Person Limited",
            tense="Past Tense",
            target_words_per_chapter=2500,
            bible_data={
                "pitch": "A fallen royal alchemist must rebuild his reputation in a court where alchemy is outlawed.",
                "hook": "The azure poison that assassinated the Grand Duke bears Alaric's private alchemical seal.",
                "central_conflict": "Alaric vs the Inquisitorial Synod and the shadowy Guild of Cinnabar.",
                "reader_promise": "Rigorous hard-magic alchemical deductions, high-stakes court intrigue, and explosive payoffs.",
                "world_setting": "The Grand Dominion of Altera during the Brass Reformation.",
                "magic_tech_rules": [
                    "Equivalent Alchemical Exchange: mass and elemental essence are strictly conserved.",
                    "Direct soul-transmutation is forbidden and immediately fatal to the practitioner.",
                ],
                "prose_style_guide": "Lyrical, sensory, and methodical action prose with emphasis on material textures and chemical reactions.",
            },
        )

        # Characters
        alaric = Character.objects.create(
            project=project,
            name="Alaric Vance",
            role="protagonist",
            wounds_status="Left hand shattered and bandaged in dark linen",
            goals="Find the counterfeit seal and identify who framed him for the Grand Duke's murder",
            traits=["Methodical", "Obsessive", "Reluctant"],
        )
        Character.objects.create(
            project=project,
            name="Captain Rayner",
            role="antagonist",
            goals="Capture Alaric dead or alive before dawn",
        )
        Character.objects.create(
            project=project,
            name="Lady Seraphina",
            role="ally",
            goals="Secure the succession of the Brass Throne",
        )

        # Locations
        Location.objects.create(
            project=project,
            name="The Old Aqueduct",
            description="Subterranean waterway choked with mineral blooms and runoff from the upper alchemy mills.",
            travel_rules="Passable on foot at low tide; flooded and lethal during the midnight sluice gates opening.",
        )
        Location.objects.create(
            project=project,
            name="The Brass Citadel",
            description="The imperial seat of Altera, towering over the smog and gaslamps of the lower wards.",
        )

        # World Rules
        WorldRule.objects.create(
            project=project,
            category="magic",
            title="Equivalent Exchange",
            rule_statement="Alchemical matter cannot be created from nothing; every transmutation requires a commensurate catalyst and sacrifice.",
            forbidden_violations="Spontaneous creation of gold or silver without catalyst",
        )

        # Plot Threads
        PlotThread.objects.create(
            project=project,
            title="The Counterfeit Azure Seal",
            category="mystery",
            setup_chapter=1,
            notes="Who stole Alaric's signet stamp from the Imperial Vault?",
        )

        # Generate & Commit Chapter 1
        ch1 = Chapter.objects.get(project=project, chapter_number=1)
        job = GenerationJob.objects.create(
            project=project,
            user=user,
            job_type="chapter_draft",
            idempotency_key="demo-seed-ch1",
            target_chapter_id=ch1.id,
        )

        fake_adapter = FakeProviderAdapter(provider)
        draft = GenerationPipeline.execute_chapter_generation(
            job=job, worker_id="seed-worker", custom_adapter=fake_adapter
        )

        # Commit Canon for Chapter 1
        draft.status = "accepted"
        draft.save()
        ch1.status = Chapter.Status.APPROVED
        ch1.save()

        CanonService.commit_chapter_canon(
            project=project,
            chapter=ch1,
            expected_head="rev_1",
            events=[
                {
                    "event_type": "escape",
                    "summary": "Alaric escaped the city guard raid through the Old Aqueduct.",
                }
            ],
            facts=[
                {
                    "subject": "Alaric",
                    "predicate": "current_location",
                    "value": "Old Aqueduct",
                    "scope": "world_truth",
                }
            ],
            actor=user,
        )

        ch1.current_summary = "Alaric narrowly evades Captain Rayner's raid on his laboratory, escaping into the subterranean aqueduct."
        ch1.save(update_fields=["current_summary"])

        self.stdout.write(
            self.style.SUCCESS(
                f"Successfully created demo novel: '{project.title}' (ID: {project.id}). "
                f"Chapter 1 generated and committed to {project.active_branch_head}!"
            )
        )
