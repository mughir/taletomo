import pytest
from unittest.mock import MagicMock
from taletomo.canon.models import Character, CharacterRole
from taletomo.consistency.checker import ContinuityChecker
from taletomo.consistency.models import FindingCategory, FindingSeverity
from taletomo.consistency.voice import DialogueExtractor, DialogueVoiceAuditor, DialogueTurn
from taletomo.generation.copilot import ProseCoPilotService
from taletomo.planning.models import Chapter, Project


@pytest.mark.django_db
def test_dialogue_extraction_and_speaker_attribution(db, django_user_model):
    user = django_user_model.objects.create_user(username="testvoiceauthor", password="password")
    project = Project.objects.create(owner=user, title="Voice Test Project")
    alaric = Character.objects.create(
        project=project,
        name="Lord Alaric",
        role=CharacterRole.PROTAGONIST,
        dialogue_style="Aristocratic, archaic, solemn. Never uses contractions or vulgar slang.",
    )
    lyra = Character.objects.create(
        project=project,
        name="Lyra",
        role=CharacterRole.ALLY,
        dialogue_style="Lively street urchin. Casual, quick-witted, speaks in dialect.",
    )

    prose = (
        '"We cannot tarry in this desolate crypt," said Alaric, his hand resting on the hilt.\n'
        'Lyra scoffed, stepping into the dim light. "Relax, your lordship, ain\'t nobody gonna jump us here."'
    )

    characters = [alaric, lyra]
    turns = DialogueExtractor.extract_dialogue(prose, characters)
    assert len(turns) == 2

    turn1 = turns[0]
    assert turn1.character == alaric
    assert "We cannot tarry" in turn1.quote

    turn2 = turns[1]
    assert turn2.character == lyra
    assert "ain't nobody gonna jump us" in turn2.quote


@pytest.mark.django_db
def test_dialogue_voice_auditor_flags_formality_clash(db, django_user_model):
    user = django_user_model.objects.create_user(username="testformality", password="password")
    project = Project.objects.create(owner=user, title="Formality Project", genre="High Fantasy")
    alaric = Character.objects.create(
        project=project,
        name="Lord Alaric",
        role=CharacterRole.PROTAGONIST,
        dialogue_style="Aristocratic, formal cadence. Never uses contractions.",
    )

    # Alaric using modern contractions and slang
    turn = DialogueTurn(
        character=alaric,
        speaker_name="Lord Alaric",
        quote="Yeah dude, I'm gonna head over to the tavern now.",
        context_sentence='said Lord Alaric',
        start_pos=0,
        end_pos=40,
    )

    issues = DialogueVoiceAuditor.audit_turn(turn, project_genre=project.genre)
    assert len(issues) >= 1
    issue_types = [i["type"] for i in issues]
    assert "formality_clash" in issue_types
    assert any("gonna" in i["claim"] or "yeah" in i["claim"] for i in issues)


@pytest.mark.django_db
def test_dialogue_voice_auditor_flags_anachronisms(db, django_user_model):
    user = django_user_model.objects.create_user(username="testanach", password="password")
    project = Project.objects.create(owner=user, title="Anach Project", genre="High Fantasy")
    wizard = Character.objects.create(
        project=project,
        name="Archmage Eldrin",
        dialogue_style="Scholarly, mysterious, mystical cadence.",
    )

    turn = DialogueTurn(
        character=wizard,
        speaker_name="Archmage Eldrin",
        quote="Give me a moment, I must check my phone for new messages.",
        context_sentence='said Archmage Eldrin',
        start_pos=0,
        end_pos=55,
    )

    issues = DialogueVoiceAuditor.audit_turn(turn, project_genre=project.genre)
    issue_types = [i["type"] for i in issues]
    assert "anachronism" in issue_types
    assert any("phone" in i["claim"] for i in issues)


@pytest.mark.django_db
def test_continuity_checker_deterministic_voice_findings(db, django_user_model):
    user = django_user_model.objects.create_user(username="testcontvoice", password="password")
    project = Project.objects.create(owner=user, title="Voice Finding Project", genre="Epic Fantasy")
    chapter = Chapter.objects.create(project=project, chapter_number=1, title="The Meeting")
    alaric = Character.objects.create(
        project=project,
        name="Alaric",
        dialogue_style="Noble, formal, strictly archaic diction. Never uses slang.",
    )

    prose = (
        'The storm rattled the stained glass windows.\n'
        '"Whatever bro, ain\'t no problem," said Alaric, shrugging his shoulders.'
    )

    findings = ContinuityChecker.run_deterministic_checks(chapter, prose)
    voice_findings = [f for f in findings if f.category == FindingCategory.VOICE]
    assert len(voice_findings) >= 1
    assert voice_findings[0].severity == FindingSeverity.WARNING
    assert "Alaric" in voice_findings[0].claim
    assert "dialogue style" in voice_findings[0].claim.lower()


@pytest.mark.django_db
def test_copilot_voice_align_action(db, django_user_model):
    user = django_user_model.objects.create_user(username="testcopilotvoice", password="password")
    project = Project.objects.create(owner=user, title="CoPilot Voice Project")
    chapter = Chapter.objects.create(project=project, chapter_number=1, title="The Council")
    alaric = Character.objects.create(
        project=project,
        name="Lord Alaric",
        dialogue_style="Archaic, noble, austere.",
        traits=["stern", "devout"],
    )

    mock_adapter = MagicMock()
    mock_resp = MagicMock()
    mock_resp.content = '"We shall yield neither quarter nor counsel to the faithless."'
    mock_resp.total_tokens = 50
    mock_adapter.generate_text.return_value = mock_resp

    result = ProseCoPilotService.polish_selection(
        user=user,
        project=project,
        chapter=chapter,
        selected_text='"Yeah we are not talking to them."',
        action="voice_align",
        target_character_name="Lord Alaric",
        custom_adapter=mock_adapter,
    )

    assert result["success"] is True
    assert result["action"] == "voice_align"
    assert "Lord Alaric" in result["explanation"]
    assert "yield neither quarter" in result["suggested_text"]
    assert mock_adapter.generate_text.called

    call_kwargs = mock_adapter.generate_text.call_args[1]
    prompt_text = call_kwargs["prompt"]
    assert "TARGET CHARACTER VOICE PROFILE" in prompt_text
    assert "Lord Alaric" in prompt_text
    assert "Archaic, noble, austere." in prompt_text
