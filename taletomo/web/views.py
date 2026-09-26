import difflib
import json
import uuid
from decimal import Decimal
from django.contrib import messages
from django.contrib.auth import get_user_model, login as auth_login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.views import LoginView
from django.core.paginator import Paginator
from django.db import IntegrityError, connection, transaction
from django.db.models import Max
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.text import slugify
from django.views.decorators.http import require_http_methods, require_POST
from taletomo.canon.models import (
    CanonFact,
    Character,
    CharacterRole,
    Faction,
    Location,
    PlotThread,
    ProposedCanonItem,
    StoryEvent,
    TimelineEvent,
    TruthScope,
    WorldRule,
)
from taletomo.canon.extraction import CanonExtractionError, CanonExtractionService
from taletomo.canon.services import CanonService, StaleHeadError
from taletomo.consistency.checker import ContinuityChecker
from taletomo.consistency.models import ContinuityFinding, FindingSeverity, FindingStatus
from taletomo.exporting.services import ExportService
from taletomo.generation.models import DraftArtifact, DraftStatus, GenerationJob, JobStatus
from taletomo.generation.tasks import generate_chapter_task
from taletomo.planning.models import (
    Arc,
    Chapter,
    ChapterPlan,
    Project,
    ProjectLengthPreset,
    ScenePlan,
    SeriesBible,
    SeriesSpine,
    Volume,
)
from taletomo.planning.services import PlanningService
from taletomo.providers.adapters import ProviderGateway
from taletomo.providers.models import BudgetReservation, ProviderConfig, ProviderType
from taletomo.providers.security import SSRFSecurityError, validate_endpoint_url
from taletomo.taxonomy.models import StyleField, StyleTerm

User = get_user_model()


def _style_term_lists(user):
    """Dictionary terms grouped per style axis, for the creation form pickers.

    Multi-value axes (genre, subgenre, tone) render as chip pickers fed by
    ``style_terms_json``; single-value axes use datalist suggestions.
    """
    terms = StyleTerm.visible_to(user)
    by_field = {}
    for term in terms:
        by_field.setdefault(term.field, []).append(term)
    terms_json = {
        field: [{"name": t.name, "definition": t.definition} for t in by_field.get(field, [])]
        for field, _label in StyleField.choices
    }
    return {
        "genre_terms": by_field.get(StyleField.GENRE, []),
        "subgenre_terms": by_field.get(StyleField.SUBGENRE, []),
        "tone_terms": by_field.get(StyleField.TONE, []),
        "pov_terms": by_field.get(StyleField.POV, []),
        "tense_terms": by_field.get(StyleField.TENSE, []),
        "pacing_terms": by_field.get(StyleField.PACING, []),
        "protagonist_terms": by_field.get(StyleField.PROTAGONIST, []),
        "style_terms_json": terms_json,
    }


class TaleTomoLoginView(LoginView):
    template_name = "registration/login.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["setup_available"] = not User.objects.exists()
        return context


@require_http_methods(["GET", "POST"])
def first_user_setup(request):
    """Create and sign in the first account; permanently close setup afterward."""
    if User.objects.exists():
        messages.info(request, "An account already exists. Please sign in.")
        return redirect("login")

    form = UserCreationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            # Serialize simultaneous first-run submissions on the PostgreSQL
            # Compose deployment so only one account can claim initial setup.
            if connection.vendor == "postgresql":
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_advisory_xact_lock(%s)", [704050041])
            if User.objects.exists():
                messages.info(request, "An account was just created. Please sign in.")
                return redirect("login")
            user = form.save()

        auth_login(request, user)
        messages.success(request, "Your account is ready. Welcome to TaleTomo!")
        return redirect("taletomo:home")

    return render(request, "registration/first_user_setup.html", {"form": form})


@login_required
def home(request):
    recent_projects = Project.objects.filter(owner=request.user, status="active").order_by("-updated_at")[:6]
    recent_jobs = GenerationJob.objects.filter(user=request.user).order_by("-created_at")[:5]
    total_projects = Project.objects.filter(owner=request.user).count()

    context = {
        "projects": recent_projects,
        "recent_jobs": recent_jobs,
        "total_projects": total_projects,
    }
    return render(request, "taletomo/home.html", context)


@login_required
def project_list(request):
    projects = Project.objects.filter(owner=request.user).exclude(status="deleted").order_by("-updated_at")
    return render(request, "taletomo/project_list.html", {"projects": projects})


@login_required
def project_new(request):
    if request.method == "POST":
        title = request.POST.get("title", "").strip() or "Untitled Novel"
        premise = request.POST.get("premise", "").strip() or "A mysterious journey begins."
        try:
            target_chapters = int(request.POST.get("target_chapters", 100))
        except (TypeError, ValueError):
            target_chapters = None
        if target_chapters is None or not 1 <= target_chapters <= 4000:
            messages.error(request, "Target chapter count must be between 1 and 4,000.")
            return render(
                request,
                "taletomo/project_new.html",
                {"presets": ProjectLengthPreset.choices, **_style_term_lists(request.user)},
            )

        length_preset = request.POST.get("length_preset", ProjectLengthPreset.STANDARD)
        genre = request.POST.get("genre", "Fantasy").strip() or "Fantasy"
        subgenre = request.POST.get("subgenre", "").strip()
        tone = request.POST.get("tone", "Epic, Mysterious").strip() or "Epic, Mysterious"
        pov = request.POST.get("pov", "Third Person Limited").strip() or "Third Person Limited"
        tense = request.POST.get("tense", "Past Tense").strip() or "Past Tense"
        pacing = request.POST.get("pacing", "Balanced").strip() or "Balanced"
        protagonist_type = request.POST.get("protagonist_type", "").strip()

        words_map = {
            ProjectLengthPreset.SHORT: 1200,
            ProjectLengthPreset.STANDARD: 2200,
            ProjectLengthPreset.LONG: 3200,
            ProjectLengthPreset.CUSTOM: 2500,
        }
        if length_preset == ProjectLengthPreset.CUSTOM:
            try:
                target_words = int(request.POST.get("custom_target_words", ""))
            except (TypeError, ValueError):
                target_words = None
            if target_words is None or not 500 <= target_words <= 10000:
                messages.error(request, "Custom chapter length must be between 500 and 10,000 words.")
                return render(
                    request,
                    "taletomo/project_new.html",
                    {"presets": ProjectLengthPreset.choices, **_style_term_lists(request.user)},
                )
        else:
            target_words = words_map.get(length_preset, 2200)

        project = PlanningService.create_project_with_scaffold(
            owner=request.user,
            title=title,
            premise=premise,
            target_chapters=target_chapters,
            genre=genre,
            subgenre=subgenre,
            tone=tone,
            pov=pov,
            tense=tense,
            pacing=pacing,
            protagonist_type=protagonist_type,
            length_preset=length_preset,
            target_words_per_chapter=target_words,
        )

        messages.success(request, f"Project '{project.title}' initialized successfully!")
        return redirect("taletomo:project_overview", project_id=project.id)

    return render(
        request,
        "taletomo/project_new.html",
        {"presets": ProjectLengthPreset.choices, **_style_term_lists(request.user)},
    )


@login_required
def style_dictionary(request):
    """Browse, add, and remove style-dictionary terms (definitions + examples)."""
    if request.method == "POST":
        action = request.POST.get("action", "add")
        if action == "delete":
            try:
                term = StyleTerm.objects.get(id=uuid.UUID(str(request.POST.get("term_id", ""))), created_by=request.user)
                name = term.name
                term.delete()
                messages.success(request, f"Removed '{name}' from the style dictionary.")
            except (StyleTerm.DoesNotExist, ValueError):
                messages.error(request, "Only terms you added yourself can be removed.")
            return redirect("taletomo:style_dictionary")

        field = request.POST.get("field", StyleField.GENRE)
        if field not in StyleField.values:
            field = StyleField.GENRE
        name = request.POST.get("name", "").strip()
        definition = request.POST.get("definition", "").strip()
        example = request.POST.get("example", "").strip()
        if not name or not definition:
            messages.error(request, "A dictionary term needs at least a name and a definition.")
            return redirect("taletomo:style_dictionary")
        if StyleTerm.objects.filter(field=field, slug=slugify(name)[:120]).exists():
            messages.error(request, f"'{name}' already exists in {StyleField(field).label}. Pick it from the suggestions instead.")
            return redirect("taletomo:style_dictionary")
        StyleTerm.objects.create(
            field=field,
            name=name[:100],
            definition=definition,
            example=example,
            created_by=request.user,
        )
        messages.success(
            request,
            f"'{name}' added. Its definition and example will now guide chapter generation.",
        )
        return redirect("taletomo:style_dictionary")

    visible = StyleTerm.visible_to(request.user)
    by_field = {}
    for term in visible:
        by_field.setdefault(term.field, []).append(term)
    groups = [(field_value, label, by_field.get(field_value, [])) for field_value, label in StyleField.choices]

    return render(
        request,
        "taletomo/settings_style_dictionary.html",
        {"groups": groups, "fields": StyleField.choices},
    )


@login_required
def project_overview(request, project_id):
    project = get_object_or_404(Project, id=project_id, owner=request.user)
    chapters_count = project.chapters.count()
    approved_count = project.chapters.filter(status__in=[Chapter.Status.APPROVED, Chapter.Status.LOCKED]).count()
    open_findings = project.continuity_findings.filter(status=FindingStatus.OPEN).count()

    context = {
        "project": project,
        "chapters_count": chapters_count,
        "approved_count": approved_count,
        "open_findings": open_findings,
        "bible": getattr(project, "bible", None),
        "spine": getattr(project, "spine", None),
    }
    return render(request, "taletomo/project_overview.html", context)


@login_required
def project_bible(request, project_id):
    project = get_object_or_404(Project, id=project_id, owner=request.user)
    bible = getattr(project, "bible", None)

    if request.method == "POST":
        if not bible:
            bible = SeriesBible.objects.create(project=project)
        bible.pitch = request.POST.get("pitch", "")
        bible.hook = request.POST.get("hook", "")
        bible.central_conflict = request.POST.get("central_conflict", "")
        bible.reader_promise = request.POST.get("reader_promise", "")
        bible.world_setting = request.POST.get("world_setting", "")
        bible.prose_style_guide = request.POST.get("prose_style_guide", "")
        bible.save()
        messages.success(request, "Tomo's Memory (Series Bible) updated.")
        return redirect("taletomo:project_bible", project_id=project.id)

    return render(request, "taletomo/project_bible.html", {"project": project, "bible": bible})


@login_required
def project_characters(request, project_id):
    project = get_object_or_404(Project, id=project_id, owner=request.user)
    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        role = request.POST.get("role", CharacterRole.NEUTRAL)
        if role not in CharacterRole.values:
            role = CharacterRole.NEUTRAL
        wounds = request.POST.get("wounds_status", "").strip()
        goals = request.POST.get("goals", "").strip()
        if name:
            Character.objects.create(
                project=project,
                name=name,
                role=role,
                wounds_status=wounds,
                goals=goals,
            )
            messages.success(request, f"Character '{name}' added.")
        return redirect("taletomo:project_characters", project_id=project.id)

    characters = project.characters.all().order_by("name")
    return render(request, "taletomo/project_characters.html", {"project": project, "characters": characters})


@login_required
def project_locations(request, project_id):
    project = get_object_or_404(Project, id=project_id, owner=request.user)
    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        desc = request.POST.get("description", "").strip()
        rules = request.POST.get("travel_rules", "").strip()
        if name:
            Location.objects.create(project=project, name=name, description=desc, travel_rules=rules)
            messages.success(request, f"Location '{name}' added.")
        return redirect("taletomo:project_locations", project_id=project.id)

    locations = project.locations.all()
    return render(request, "taletomo/project_locations.html", {"project": project, "locations": locations})


@login_required
def project_factions(request, project_id):
    project = get_object_or_404(Project, id=project_id, owner=request.user)
    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        goals = request.POST.get("goals", "").strip()
        resources = request.POST.get("resources", "").strip()
        if name:
            Faction.objects.create(
                project=project,
                name=name,
                goals=goals,
                resources=resources,
            )
            messages.success(request, f"Faction '{name}' added.")
        return redirect("taletomo:project_factions", project_id=project.id)

    factions = project.factions.all()
    return render(request, "taletomo/project_factions.html", {"project": project, "factions": factions})


@login_required
def project_rules(request, project_id):
    project = get_object_or_404(Project, id=project_id, owner=request.user)
    if request.method == "POST":
        title = request.POST.get("title", "").strip()
        category = request.POST.get("category", WorldRule.Category.MAGIC)
        if category not in WorldRule.Category.values:
            category = WorldRule.Category.MAGIC
        statement = request.POST.get("rule_statement", "").strip()
        forbidden = request.POST.get("forbidden_violations", "").strip()
        if title and statement:
            WorldRule.objects.create(
                project=project,
                title=title,
                category=category,
                rule_statement=statement,
                forbidden_violations=forbidden,
            )
            messages.success(request, f"World Rule '{title}' registered.")
        return redirect("taletomo:project_rules", project_id=project.id)

    rules = project.rules.all()
    return render(request, "taletomo/project_rules.html", {"project": project, "rules": rules})


@login_required
def project_threads(request, project_id):
    project = get_object_or_404(Project, id=project_id, owner=request.user)
    if request.method == "POST":
        title = request.POST.get("title", "").strip()
        cat = request.POST.get("category", PlotThread.Category.PROMISE)
        if cat not in PlotThread.Category.values:
            cat = PlotThread.Category.PROMISE
        try:
            ch_setup = int(request.POST.get("setup_chapter", 1))
        except (TypeError, ValueError):
            ch_setup = 1
        if title:
            PlotThread.objects.create(project=project, title=title, category=cat, setup_chapter=ch_setup)
            messages.success(request, f"Plot Thread '{title}' created.")
        return redirect("taletomo:project_threads", project_id=project.id)

    threads = project.plot_threads.all()
    return render(request, "taletomo/project_threads.html", {"project": project, "threads": threads})


@login_required
def project_timeline(request, project_id):
    project = get_object_or_404(Project, id=project_id, owner=request.user)
    if request.method == "POST":
        title = request.POST.get("title", "").strip()
        story_time = request.POST.get("story_time", "").strip()
        try:
            order_idx = int(request.POST.get("real_order", project.timeline_events.count() + 1))
        except (TypeError, ValueError):
            order_idx = project.timeline_events.count() + 1
        desc = request.POST.get("description", "").strip()
        if title:
            TimelineEvent.objects.create(
                project=project,
                title=title,
                story_time_valid_from=story_time,
                real_order=order_idx,
                description=desc,
            )
            messages.success(request, f"Timeline event '{title}' added.")
        return redirect("taletomo:project_timeline", project_id=project.id)

    events = project.timeline_events.all().order_by("real_order")
    return render(request, "taletomo/project_timeline.html", {"project": project, "events": events})


@login_required
def project_outline(request, project_id):
    """Hierarchical outline supporting 1 to 4,000 chapters with bounded window pagination."""
    project = get_object_or_404(Project, id=project_id, owner=request.user)

    jump_chapter = request.GET.get("jump")
    page_size = 25
    chapters_query = (
        project.chapters.select_related("plan")
        .prefetch_related("plan__scenes")
        .order_by("chapter_number")
    )

    paginator = Paginator(chapters_query, page_size)
    page_number = request.GET.get("page", 1)

    if jump_chapter and jump_chapter.isdigit():
        target_num = int(jump_chapter)
        page_number = max(1, min((target_num - 1) // page_size + 1, paginator.num_pages or 1))

    page_obj = paginator.get_page(page_number)
    volumes = project.volumes.all().prefetch_related("arcs")

    context = {
        "project": project,
        "volumes": volumes,
        "page_obj": page_obj,
        "paginator": paginator,
        "total_chapters": paginator.count,
    }
    return render(request, "taletomo/project_outline.html", context)


@login_required
def chapter_plan(request, project_id, chapter_id):
    project = get_object_or_404(Project, id=project_id, owner=request.user)
    chapter = get_object_or_404(Chapter, id=chapter_id, project=project)
    plan, _ = ChapterPlan.objects.get_or_create(chapter=chapter)

    if request.method == "POST":
        if chapter.status == Chapter.Status.LOCKED:
            messages.error(request, "Chapter is locked. Contracts for committed canon cannot be modified.")
            return redirect("taletomo:chapter_plan", project_id=project.id, chapter_id=chapter.id)

        objectives_raw = request.POST.get("objectives", "")
        beats_raw = request.POST.get("required_beats", "")
        prohibited_raw = request.POST.get("prohibited_outcomes", "")
        continuity_raw = request.POST.get("continuity_requirements", "")
        try:
            target_words = int(request.POST.get("target_words", project.target_words_per_chapter))
        except (TypeError, ValueError):
            target_words = project.target_words_per_chapter

        plan.objectives = [line.strip() for line in objectives_raw.split("\n") if line.strip()]
        plan.required_beats = [line.strip() for line in beats_raw.split("\n") if line.strip()]
        plan.prohibited_outcomes = [line.strip() for line in prohibited_raw.split("\n") if line.strip()]
        plan.continuity_requirements = [line.strip() for line in continuity_raw.split("\n") if line.strip()]
        plan.target_words = target_words

        validation_errors = PlanningService.validate_chapter_contract(plan)
        if validation_errors:
            for err in validation_errors:
                messages.error(request, err)
        else:
            plan.status = ChapterPlan.Status.APPROVED
            plan.save()

            if chapter.status in (Chapter.Status.UNPLANNED, Chapter.Status.PLANNED):
                chapter.status = Chapter.Status.PLANNED
                chapter.save(update_fields=["status"])

            messages.success(request, f"Chapter {chapter.chapter_number} contract updated and approved.")
            return redirect("taletomo:chapter_plan", project_id=project.id, chapter_id=chapter.id)

    scenes = plan.scenes.all()
    context = {
        "project": project,
        "chapter": chapter,
        "plan": plan,
        "scenes": scenes,
        "objectives_text": "\n".join(plan.objectives),
        "beats_text": "\n".join(plan.required_beats),
        "prohibited_text": "\n".join(plan.prohibited_outcomes),
        "continuity_text": "\n".join(plan.continuity_requirements),
    }
    return render(request, "taletomo/chapter_plan.html", context)


@login_required
def chapter_edit(request, project_id, chapter_id):
    """3-column authoring environment with Tomo assistant drawer and draft diffs."""
    project = get_object_or_404(Project, id=project_id, owner=request.user)
    chapter = get_object_or_404(Chapter, id=chapter_id, project=project)

    prev_chapter = (
        Chapter.objects.filter(project=project, chapter_number__lt=chapter.chapter_number)
        .order_by("-chapter_number")
        .first()
    )
    next_chapter = (
        Chapter.objects.filter(project=project, chapter_number__gt=chapter.chapter_number)
        .order_by("chapter_number")
        .first()
    )

    drafts = chapter.drafts.all().order_by("-version_number")
    active_draft = None
    if chapter.active_draft_id:
        active_draft = drafts.filter(id=chapter.active_draft_id).first()
    if not active_draft and drafts.exists():
        active_draft = drafts.first()

    prev_draft = None
    diff_html = ""
    if active_draft and active_draft.parent_draft:
        prev_draft = active_draft.parent_draft
    elif drafts.count() >= 2:
        prev_draft = drafts[1]

    if active_draft and prev_draft:
        diff_lines = difflib.unified_diff(
            prev_draft.prose_content.splitlines(),
            active_draft.prose_content.splitlines(),
            fromfile=f"Draft v{prev_draft.version_number}",
            tofile=f"Draft v{active_draft.version_number}",
            lineterm="",
        )
        diff_html = "\n".join(diff_lines)

    findings = chapter.continuity_findings.all().order_by("-severity")
    blockers_count = findings.filter(severity=FindingSeverity.BLOCKER, status=FindingStatus.OPEN).count()
    pending_canon_count = chapter.proposed_canon_items.filter(
        status=ProposedCanonItem.Status.PROPOSED
    ).count()

    if request.method == "POST":
        if chapter.status == Chapter.Status.LOCKED:
            messages.error(request, "Chapter is locked. Canonical chapters cannot be modified.")
            return redirect("taletomo:chapter_edit", project_id=project.id, chapter_id=chapter.id)

        new_prose = request.POST.get("prose_content", "")
        next_version = (chapter.drafts.aggregate(max_v=Max("version_number"))["max_v"] or 0) + 1
        new_v = DraftArtifact.objects.create(
            chapter=chapter,
            version_number=next_version,
            prose_content=new_prose,
            word_count=len(new_prose.split()),
            model_name="Manual Author Edit",
            parent_draft=active_draft,
            status=DraftStatus.UNDER_REVIEW,
        )
        chapter.active_draft_id = new_v.id
        chapter.current_word_count = new_v.word_count
        if chapter.status in (Chapter.Status.UNPLANNED, Chapter.Status.PLANNED, Chapter.Status.DRAFTING):
            chapter.status = Chapter.Status.REVIEW
            chapter.save(update_fields=["active_draft_id", "current_word_count", "status"])
        else:
            chapter.save(update_fields=["active_draft_id", "current_word_count"])

        # Manual edits bypass the generation pipeline, so run the free
        # deterministic checks on every saved version.
        ContinuityChecker.check_and_persist(
            chapter=chapter, prose=new_prose, draft_id=str(new_v.id)
        )

        messages.success(request, f"New version {new_v.version_number} saved.")
        return redirect("taletomo:chapter_edit", project_id=project.id, chapter_id=chapter.id)

    context = {
        "project": project,
        "chapter": chapter,
        "prev_chapter": prev_chapter,
        "next_chapter": next_chapter,
        "drafts": drafts,
        "active_draft": active_draft,
        "prev_draft": prev_draft,
        "diff_html": diff_html,
        "findings": findings,
        "blockers_count": blockers_count,
        "pending_canon_count": pending_canon_count,
        "characters": project.characters.all()[:8],
        "rules": project.rules.all()[:6],
    }
    return render(request, "taletomo/chapter_edit.html", context)


@login_required
@require_POST
def chapter_generate(request, project_id, chapter_id):
    """Enqueues a durable background job for chapter generation with deterministic idempotency."""
    project = get_object_or_404(Project, id=project_id, owner=request.user)
    chapter = get_object_or_404(Chapter, id=chapter_id, project=project)

    if chapter.status == Chapter.Status.LOCKED:
        messages.error(request, "Chapter is locked. Cannot generate new drafts for committed canon.")
        return redirect("taletomo:chapter_edit", project_id=project.id, chapter_id=chapter.id)

    next_version = (chapter.drafts.aggregate(max_v=Max("version_number"))["max_v"] or 0) + 1
    idempotency_key = f"draft-{chapter.id}-v{next_version}"
    active_statuses = [
        JobStatus.QUEUED,
        JobStatus.PREPARING_CONTEXT,
        JobStatus.SUBMITTED,
        JobStatus.GENERATING,
        JobStatus.CHECKING,
        JobStatus.EXTRACTING,
    ]

    # Reuse existing active job if already queued or generating
    active_job = GenerationJob.objects.filter(
        idempotency_key=idempotency_key,
        status__in=active_statuses,
    ).first()
    if active_job:
        messages.info(request, f"Generation job is already running for Chapter {chapter.chapter_number}.")
        return redirect("taletomo:job_detail", job_id=active_job.id)

    try:
        job = GenerationJob.objects.create(
            project=project,
            user=request.user,
            job_type="chapter_draft",
            idempotency_key=idempotency_key,
            target_chapter_id=chapter.id,
            stage="Queued for generation",
        )
    except IntegrityError:
        # Two concurrent submissions raced past the active-job check; the
        # unique idempotency key means one job already exists.
        active_job = GenerationJob.objects.filter(
            idempotency_key=idempotency_key, status__in=active_statuses
        ).first()
        if active_job:
            messages.info(request, f"Generation job is already running for Chapter {chapter.chapter_number}.")
            return redirect("taletomo:job_detail", job_id=active_job.id)
        # A terminal job already claimed this key (drafts were removed since);
        # disambiguate so the request still starts exactly one new job.
        job = GenerationJob.objects.create(
            project=project,
            user=request.user,
            job_type="chapter_draft",
            idempotency_key=f"{idempotency_key}-{uuid.uuid4().hex[:8]}",
            target_chapter_id=chapter.id,
            stage="Queued for generation",
        )

    # Launch Celery task on commit
    transaction.on_commit(lambda: generate_chapter_task.delay(str(job.id)))

    messages.info(request, f"Generation job started for Chapter {chapter.chapter_number}.")
    return redirect("taletomo:job_detail", job_id=job.id)


@login_required
@require_POST
def chapter_approve_draft(request, project_id, chapter_id):
    """Phase 1: Approves the draft prose artifact (distinct from canon commit)."""
    project = get_object_or_404(Project, id=project_id, owner=request.user)
    chapter = get_object_or_404(Chapter, id=chapter_id, project=project)

    if chapter.status == Chapter.Status.LOCKED:
        messages.error(request, "Chapter is locked. Committed canon cannot be re-approved.")
        return redirect("taletomo:chapter_edit", project_id=project.id, chapter_id=chapter.id)

    if not chapter.active_draft_id:
        messages.error(request, "No active draft to approve.")
        return redirect("taletomo:chapter_edit", project_id=project.id, chapter_id=chapter.id)

    draft = get_object_or_404(DraftArtifact, id=chapter.active_draft_id, chapter=chapter)
    draft.status = DraftStatus.ACCEPTED
    draft.save(update_fields=["status"])

    chapter.status = Chapter.Status.APPROVED
    chapter.current_word_count = draft.word_count
    chapter.save(update_fields=["status", "current_word_count"])

    messages.success(
        request,
        f"Chapter {chapter.chapter_number} draft prose approved! Now review and commit canonical story changes.",
    )
    return redirect("taletomo:chapter_edit", project_id=project.id, chapter_id=chapter.id)


@login_required
@require_POST
def chapter_commit_canon(request, project_id, chapter_id):
    """Phase 2: Promotes extracted claims into confirmed canonical reality in an atomic commit."""
    project = get_object_or_404(Project, id=project_id, owner=request.user)
    chapter = get_object_or_404(Chapter, id=chapter_id, project=project)

    expected_head = request.POST.get("expected_head", project.active_branch_head)
    summary_text = request.POST.get("summary", "").strip() or f"Events of Chapter {chapter.chapter_number}"
    override_blockers = request.POST.get("override_blockers") == "true"
    override_rationale = request.POST.get("override_rationale", "").strip()

    events = [
        {
            "event_type": "chapter_conclusion",
            "summary": summary_text,
            "payload": {"chapter_number": chapter.chapter_number},
        }
    ]
    facts = []
    thread_updates = []
    character_updates = []
    approved_items = list(
        chapter.proposed_canon_items.filter(status=ProposedCanonItem.Status.APPROVED)
    )
    for item in approved_items:
        payload = item.payload or {}
        if item.kind == ProposedCanonItem.Kind.FACT:
            facts.append(
                {
                    "subject": payload.get("subject", ""),
                    "predicate": payload.get("predicate", ""),
                    "value": payload.get("value", ""),
                    "scope": payload.get("scope", TruthScope.WORLD_TRUTH),
                }
            )
        elif item.kind == ProposedCanonItem.Kind.EVENT:
            events.append(
                {
                    "event_type": payload.get("event_type", "plot_progress"),
                    "summary": payload.get("summary", ""),
                }
            )
        elif item.kind == ProposedCanonItem.Kind.THREAD_UPDATE:
            thread_updates.append(payload)
        elif item.kind == ProposedCanonItem.Kind.CHARACTER_UPDATE:
            character_updates.append(payload)

    try:
        snapshot = CanonService.commit_chapter_canon(
            project=project,
            chapter=chapter,
            expected_head=expected_head,
            events=events,
            facts=facts,
            actor=request.user,
            override_blockers=override_blockers,
            override_rationale=override_rationale,
            thread_updates=thread_updates,
            character_updates=character_updates,
        )
        chapter.current_summary = summary_text
        chapter.save(update_fields=["current_summary"])
        if approved_items:
            ProposedCanonItem.objects.filter(id__in=[item.id for item in approved_items]).update(
                status=ProposedCanonItem.Status.CONSUMED
            )

        messages.success(
            request,
            f"Atomic canon commit successful! Advanced to {project.active_branch_head}. "
            f"Chapter {chapter.chapter_number} is locked "
            f"({len(facts)} facts, {len(events)} events, {len(thread_updates)} thread updates, "
            f"{len(character_updates)} character updates).",
        )
    except StaleHeadError as e:
        messages.error(request, f"Commit rejected due to stale branch head: {e}")
    except ValueError as e:
        messages.error(request, f"Commit rejected by domain policy: {e}")

    return redirect("taletomo:chapter_edit", project_id=project.id, chapter_id=chapter.id)


@login_required
def chapter_canon_review(request, project_id, chapter_id):
    """Human-in-the-loop review of canon proposals extracted from the active draft."""
    project = get_object_or_404(Project, id=project_id, owner=request.user)
    chapter = get_object_or_404(Chapter, id=chapter_id, project=project)

    items = chapter.proposed_canon_items.select_related("draft").order_by("kind", "-confidence", "created_at")
    pending_items = items.filter(status=ProposedCanonItem.Status.PROPOSED)
    reviewed_items = items.exclude(status=ProposedCanonItem.Status.PROPOSED)

    context = {
        "project": project,
        "chapter": chapter,
        "pending_items": pending_items,
        "reviewed_items": reviewed_items,
        "is_locked": chapter.status == Chapter.Status.LOCKED,
    }
    return render(request, "taletomo/chapter_canon_review.html", context)


@login_required
@require_POST
def proposed_canon_update(request, item_id):
    item = get_object_or_404(ProposedCanonItem, id=item_id, project__owner=request.user)
    action = request.POST.get("action", "")
    review_note = request.POST.get("review_note", "").strip()

    redirect_target = redirect(
        "taletomo:chapter_canon_review",
        project_id=item.project.id,
        chapter_id=item.chapter.id,
    )

    if item.chapter.status == Chapter.Status.LOCKED:
        messages.error(request, "Chapter is locked; proposals can no longer be reviewed.")
        return redirect_target
    if item.status != ProposedCanonItem.Status.PROPOSED:
        messages.error(request, "This proposal has already been reviewed.")
        return redirect_target
    if action not in ("approve", "reject"):
        messages.error(request, "Unknown review action.")
        return redirect_target

    item.status = (
        ProposedCanonItem.Status.APPROVED
        if action == "approve"
        else ProposedCanonItem.Status.REJECTED
    )
    if review_note:
        item.review_note = review_note
    item.save(update_fields=["status", "review_note", "updated_at"])
    messages.success(request, f"Proposal {item.get_status_display().lower()}.")
    return redirect_target


@login_required
@require_POST
def chapter_extract_canon(request, project_id, chapter_id):
    """Runs canon extraction on the active draft (e.g. after manual edits)."""
    project = get_object_or_404(Project, id=project_id, owner=request.user)
    chapter = get_object_or_404(Chapter, id=chapter_id, project=project)

    redirect_target = redirect(
        "taletomo:chapter_canon_review", project_id=project.id, chapter_id=chapter.id
    )
    if chapter.status == Chapter.Status.LOCKED:
        messages.error(request, "Chapter is locked; committed canon is immutable.")
        return redirect_target
    if not chapter.active_draft_id:
        messages.error(request, "No active draft to extract canon from.")
        return redirect_target

    draft = get_object_or_404(DraftArtifact, id=chapter.active_draft_id, chapter=chapter)
    try:
        adapter = ProviderGateway.get_adapter(user=request.user, project=project)
        proposals = CanonExtractionService.extract_from_draft(
            chapter=chapter, draft=draft, adapter=adapter
        )
        messages.success(request, f"Extracted {len(proposals)} canon proposals for review.")
    except CanonExtractionError as e:
        messages.error(request, f"Canon extraction failed: {e}")
    except Exception:
        messages.error(
            request,
            "Canon extraction failed: the provider could not be reached. Check your provider settings.",
        )
    return redirect_target


@login_required
def chapter_continuity(request, project_id, chapter_id):
    project = get_object_or_404(Project, id=project_id, owner=request.user)
    chapter = get_object_or_404(Chapter, id=chapter_id, project=project)
    findings = chapter.continuity_findings.all().order_by("-severity", "created_at")

    context = {
        "project": project,
        "chapter": chapter,
        "findings": findings,
    }
    return render(request, "taletomo/chapter_continuity.html", context)


@login_required
@require_POST
def finding_update(request, finding_id):
    finding = get_object_or_404(ContinuityFinding, id=finding_id, project__owner=request.user)
    new_status = request.POST.get("status")
    rationale = request.POST.get("rationale", "").strip()

    if new_status in FindingStatus.values:
        if finding.severity == FindingSeverity.BLOCKER and new_status in (FindingStatus.INTENTIONAL, FindingStatus.DISMISSED):
            if not rationale:
                messages.error(request, "A non-empty rationale is required to dismiss or mark a blocking finding as intentional.")
                return redirect("taletomo:chapter_continuity", project_id=finding.project.id, chapter_id=finding.chapter.id)
        finding.status = new_status
        if rationale:
            finding.override_rationale = rationale
        finding.save(update_fields=["status", "override_rationale", "updated_at"])
        messages.success(request, f"Continuity finding updated to {finding.get_status_display()}.")

    return redirect("taletomo:chapter_continuity", project_id=finding.project.id, chapter_id=finding.chapter.id)


@login_required
def project_versions(request, project_id):
    project = get_object_or_404(Project, id=project_id, owner=request.user)
    drafts = DraftArtifact.objects.filter(chapter__project=project).order_by("-created_at")[:50]
    return render(request, "taletomo/project_versions.html", {"project": project, "drafts": drafts})


@login_required
def compare_drafts(request, project_id, left_id, right_id):
    project = get_object_or_404(Project, id=project_id, owner=request.user)
    left_draft = get_object_or_404(DraftArtifact, id=left_id, chapter__project=project)
    right_draft = get_object_or_404(DraftArtifact, id=right_id, chapter__project=project)

    if left_draft.chapter_id != right_draft.chapter_id:
        raise Http404("Drafts must belong to the same chapter.")

    diff = difflib.unified_diff(
        left_draft.prose_content.splitlines(),
        right_draft.prose_content.splitlines(),
        fromfile=f"v{left_draft.version_number} ({left_draft.created_at.strftime('%Y-%m-%d %H:%M')})",
        tofile=f"v{right_draft.version_number} ({right_draft.created_at.strftime('%Y-%m-%d %H:%M')})",
        lineterm="",
    )

    return render(
        request,
        "taletomo/compare_drafts.html",
        {
            "project": project,
            "left_draft": left_draft,
            "right_draft": right_draft,
            "diff_text": "\n".join(diff),
        },
    )


@login_required
def project_export(request, project_id):
    project = get_object_or_404(Project, id=project_id, owner=request.user)
    return render(request, "taletomo/project_export.html", {"project": project})


@login_required
def project_export_markdown(request, project_id):
    project = get_object_or_404(Project, id=project_id, owner=request.user)
    md_content = ExportService.export_markdown(project)
    response = HttpResponse(md_content, content_type="text/markdown; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{project.slug or "novel"}.md"'
    return response


@login_required
def project_export_json(request, project_id):
    project = get_object_or_404(Project, id=project_id, owner=request.user)
    json_data = ExportService.export_json_backup(project)
    response = HttpResponse(
        json.dumps(json_data, indent=2), content_type="application/json; charset=utf-8"
    )
    response["Content-Disposition"] = f'attachment; filename="{project.slug or "novel"}_backup.json"'
    return response


@login_required
@require_POST
def project_restore(request):
    upload_file = request.FILES.get("backup_file")
    if not upload_file:
        messages.error(request, "No JSON backup file provided.")
        return redirect("taletomo:project_list")

    if upload_file.size > 25 * 1024 * 1024:
        messages.error(request, "Backup file exceeds maximum allowed size of 25MB.")
        return redirect("taletomo:project_list")

    try:
        content = json.loads(upload_file.read().decode("utf-8"))
        restored_project = ExportService.restore_from_json(owner=request.user, backup_data=content)
        messages.success(request, f"Successfully restored '{restored_project.title}' from backup!")
        return redirect("taletomo:project_overview", project_id=restored_project.id)
    except Exception as e:
        messages.error(request, f"Restore failed: {e}")
        return redirect("taletomo:project_list")


@login_required
def job_list(request):
    jobs = GenerationJob.objects.filter(user=request.user).order_by("-created_at")
    return render(request, "taletomo/job_list.html", {"jobs": jobs})


@login_required
def job_detail(request, job_id):
    job = get_object_or_404(GenerationJob, id=job_id, user=request.user)
    return render(request, "taletomo/job_detail.html", {"job": job})


@login_required
def job_status_api(request, job_id):
    """JSON API polled by Vue component for live job progress."""
    job = get_object_or_404(GenerationJob, id=job_id, user=request.user)
    return JsonResponse(
        {
            "id": str(job.id),
            "status": job.status,
            "stage": job.stage,
            "progress_pct": job.progress_pct,
            "result_url": job.result_url,
            "error_message": job.error_message,
            "confirmed_tokens": job.confirmed_tokens,
            "confirmed_cost": str(job.confirmed_cost),
        }
    )


@login_required
@require_POST
def job_cancel(request, job_id):
    job = get_object_or_404(GenerationJob, id=job_id, user=request.user)
    if job.status not in (JobStatus.READY, JobStatus.FAILED, JobStatus.CANCELLED):
        job.status = JobStatus.CANCELLED
        job.stage = "Cancelled by user"
        job.save(update_fields=["status", "stage", "updated_at"])
        for res in BudgetReservation.objects.filter(job_id=job.id, status=BudgetReservation.Status.RESERVED):
            res.release()
        messages.info(request, f"Job {job.id} has been cancelled.")
    return redirect("taletomo:job_detail", job_id=job.id)


@login_required
@require_POST
def job_retry(request, job_id):
    job = get_object_or_404(GenerationJob, id=job_id, user=request.user)
    if job.error_details.get("unknown_outcome"):
        messages.error(
            request,
            "Cannot automatically retry: provider billing status is unknown. Reconcile with the provider before starting another billable attempt.",
        )
        return redirect("taletomo:job_detail", job_id=job.id)
    if job.status in (JobStatus.FAILED, JobStatus.CANCELLED, JobStatus.STALE):
        job.status = JobStatus.QUEUED
        job.stage = "Queued for retry"
        job.progress_pct = 0
        job.error_message = ""
        job.save(update_fields=["status", "stage", "progress_pct", "error_message", "updated_at"])
        transaction.on_commit(lambda: generate_chapter_task.delay(str(job.id)))
        messages.info(request, f"Job {job.id} has been restarted.")
    return redirect("taletomo:job_detail", job_id=job.id)


@login_required
def settings_providers(request):
    configs = ProviderConfig.objects.filter(user=request.user)

    if request.method == "POST":
        name = request.POST.get("name", "Custom Provider").strip()
        p_type = request.POST.get("provider_type", ProviderType.FAKE)
        endpoint = request.POST.get("endpoint_url", "").strip()
        api_key = request.POST.get("api_key", "").strip()
        model_name = request.POST.get("model_name", "gpt-4o").strip()

        endpoint_val = endpoint or "https://api.openai.com/v1"
        try:
            validate_endpoint_url(endpoint_val)
        except SSRFSecurityError as err:
            messages.error(request, f"Invalid provider endpoint URL: {err}")
            return redirect("taletomo:settings_providers")

        is_first = not ProviderConfig.objects.filter(user=request.user).exists()
        cfg = ProviderConfig.objects.create(
            user=request.user,
            name=name,
            provider_type=p_type,
            endpoint_url=endpoint_val,
            default_drafting_model=model_name,
            default_planning_model=model_name,
            is_default=is_first,
            is_active=True,
        )
        if api_key:
            cfg.set_api_key(api_key)
            cfg.save()
        messages.success(request, f"Provider '{name}' saved and encrypted.")
        return redirect("taletomo:settings_providers")

    return render(request, "taletomo/settings_providers.html", {"configs": configs, "types": ProviderType.choices})


@login_required
@require_POST
def test_provider(request):
    cfg_id = request.POST.get("config_id")
    try:
        cfg_uuid = uuid.UUID(str(cfg_id))
    except (ValueError, TypeError):
        return JsonResponse({"success": False, "error": "Invalid config_id"}, status=400)
    cfg = get_object_or_404(ProviderConfig, id=cfg_uuid, user=request.user)
    try:
        adapter = ProviderGateway.get_adapter(cfg, user=request.user)
        res = adapter.validate_credentials()
        return JsonResponse({"success": True, "result": res})
    except Exception as e:
        return JsonResponse({"success": False, "error": str(e)}, status=400)
