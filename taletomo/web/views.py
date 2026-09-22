import difflib
import json
import uuid
from decimal import Decimal
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.core.paginator import Paginator
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST
from taletomo.canon.models import (
    CanonFact,
    Character,
    Faction,
    Location,
    PlotThread,
    StoryEvent,
    TimelineEvent,
    WorldRule,
)
from taletomo.canon.services import CanonService, StaleHeadError
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
from taletomo.providers.models import ProviderConfig, ProviderType

User = get_user_model()


def _get_current_user(request):
    if request.user.is_authenticated:
        return request.user
    user, _ = User.objects.get_or_create(username="author", defaults={"email": "author@taletomo.local"})
    return user


def home(request):
    user = _get_current_user(request)
    recent_projects = Project.objects.filter(owner=user, status="active").order_by("-updated_at")[:6]
    recent_jobs = GenerationJob.objects.filter(user=user).order_by("-created_at")[:5]
    total_projects = Project.objects.filter(owner=user).count()

    context = {
        "projects": recent_projects,
        "recent_jobs": recent_jobs,
        "total_projects": total_projects,
    }
    return render(request, "taletomo/home.html", context)


def project_list(request):
    user = _get_current_user(request)
    projects = Project.objects.filter(owner=user).exclude(status="deleted").order_by("-updated_at")
    return render(request, "taletomo/project_list.html", {"projects": projects})


def project_new(request):
    user = _get_current_user(request)
    if request.method == "POST":
        title = request.POST.get("title", "").strip() or "Untitled Novel"
        premise = request.POST.get("premise", "").strip() or "A mysterious journey begins."
        target_chapters = int(request.POST.get("target_chapters", 100))
        length_preset = request.POST.get("length_preset", ProjectLengthPreset.STANDARD)
        genre = request.POST.get("genre", "Fantasy")
        tone = request.POST.get("tone", "Epic, Mysterious")
        pov = request.POST.get("pov", "Third Person Limited")
        tense = request.POST.get("tense", "Past Tense")

        words_map = {
            ProjectLengthPreset.SHORT: 1200,
            ProjectLengthPreset.STANDARD: 2200,
            ProjectLengthPreset.LONG: 3200,
            ProjectLengthPreset.CUSTOM: 2500,
        }
        target_words = words_map.get(length_preset, 2200)

        project = PlanningService.create_project_with_scaffold(
            owner=user,
            title=title,
            premise=premise,
            target_chapters=target_chapters,
            genre=genre,
            tone=tone,
            pov=pov,
            tense=tense,
            target_words_per_chapter=target_words,
        )

        messages.success(request, f"Project '{project.title}' initialized successfully!")
        return redirect("taletomo:project_overview", project_id=project.id)

    return render(request, "taletomo/project_new.html", {"presets": ProjectLengthPreset.choices})


def project_overview(request, project_id):
    project = get_object_or_404(Project, id=project_id)
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


def project_bible(request, project_id):
    project = get_object_or_404(Project, id=project_id)
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


def project_characters(request, project_id):
    project = get_object_or_404(Project, id=project_id)
    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        role = request.POST.get("role", "neutral")
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


def project_locations(request, project_id):
    project = get_object_or_404(Project, id=project_id)
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


def project_rules(request, project_id):
    project = get_object_or_404(Project, id=project_id)
    if request.method == "POST":
        title = request.POST.get("title", "").strip()
        category = request.POST.get("category", "magic")
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


def project_threads(request, project_id):
    project = get_object_or_404(Project, id=project_id)
    if request.method == "POST":
        title = request.POST.get("title", "").strip()
        cat = request.POST.get("category", "promise")
        ch_setup = int(request.POST.get("setup_chapter", 1))
        if title:
            PlotThread.objects.create(project=project, title=title, category=cat, setup_chapter=ch_setup)
            messages.success(request, f"Plot Thread '{title}' created.")
        return redirect("taletomo:project_threads", project_id=project.id)

    threads = project.plot_threads.all()
    return render(request, "taletomo/project_threads.html", {"project": project, "threads": threads})


def project_timeline(request, project_id):
    project = get_object_or_404(Project, id=project_id)
    events = project.timeline_events.all().order_by("real_order")
    return render(request, "taletomo/project_timeline.html", {"project": project, "events": events})


def project_outline(request, project_id):
    """Hierarchical outline supporting 1 to 4,000 chapters with bounded window pagination."""
    project = get_object_or_404(Project, id=project_id)

    # Direct chapter jump support (e.g. ?jump=812)
    jump_chapter = request.GET.get("jump")
    page_size = 25
    chapters_query = project.chapters.all().order_by("chapter_number")

    paginator = Paginator(chapters_query, page_size)
    page_number = request.GET.get("page", 1)

    if jump_chapter and jump_chapter.isdigit():
        target_num = int(jump_chapter)
        page_number = max(1, (target_num - 1) // page_size + 1)

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


def chapter_plan(request, project_id, chapter_id):
    project = get_object_or_404(Project, id=project_id)
    chapter = get_object_or_404(Chapter, id=chapter_id, project=project)
    plan, _ = ChapterPlan.objects.get_or_create(chapter=chapter)

    if request.method == "POST":
        objectives_raw = request.POST.get("objectives", "")
        beats_raw = request.POST.get("required_beats", "")
        prohibited_raw = request.POST.get("prohibited_outcomes", "")
        continuity_raw = request.POST.get("continuity_requirements", "")
        target_words = int(request.POST.get("target_words", project.target_words_per_chapter))

        plan.objectives = [line.strip() for line in objectives_raw.split("\n") if line.strip()]
        plan.required_beats = [line.strip() for line in beats_raw.split("\n") if line.strip()]
        plan.prohibited_outcomes = [line.strip() for line in prohibited_raw.split("\n") if line.strip()]
        plan.continuity_requirements = [line.strip() for line in continuity_raw.split("\n") if line.strip()]
        plan.target_words = target_words
        plan.status = ChapterPlan.Status.APPROVED
        plan.save()

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


def chapter_edit(request, project_id, chapter_id):
    """3-column authoring environment with Tomo assistant drawer and draft diffs."""
    project = get_object_or_404(Project, id=project_id)
    chapter = get_object_or_404(Chapter, id=chapter_id, project=project)

    # Nearby chapters for left navigator
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

    # Drafts
    drafts = chapter.drafts.all().order_by("-version_number")
    active_draft = None
    if chapter.active_draft_id:
        active_draft = drafts.filter(id=chapter.active_draft_id).first()
    if not active_draft and drafts.exists():
        active_draft = drafts.first()

    # Previous draft for diff comparison
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

    # Continuity findings for this chapter
    findings = chapter.continuity_findings.all().order_by("-severity")
    blockers_count = findings.filter(severity=FindingSeverity.BLOCKER, status=FindingStatus.OPEN).count()

    # Manual save
    if request.method == "POST":
        new_prose = request.POST.get("prose_content", "")
        if active_draft:
            # Create new version from manual edit
            new_v = DraftArtifact.objects.create(
                chapter=chapter,
                version_number=drafts.count() + 1,
                prose_content=new_prose,
                word_count=len(new_prose.split()),
                model_name="Manual Author Edit",
                parent_draft=active_draft,
                status=DraftStatus.UNDER_REVIEW,
            )
            chapter.active_draft_id = new_v.id
            chapter.current_word_count = new_v.word_count
            chapter.save(update_fields=["active_draft_id", "current_word_count"])
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
        "characters": project.characters.all()[:8],
        "rules": project.rules.all()[:6],
    }
    return render(request, "taletomo/chapter_edit.html", context)


@require_POST
def chapter_generate(request, project_id, chapter_id):
    """Enqueues a durable background job for chapter generation."""
    project = get_object_or_404(Project, id=project_id)
    chapter = get_object_or_404(Chapter, id=chapter_id, project=project)
    user = _get_current_user(request)

    idempotency_key = f"draft-{chapter.id}-{uuid.uuid4().hex[:12]}"
    job = GenerationJob.objects.create(
        project=project,
        user=user,
        job_type="chapter_draft",
        idempotency_key=idempotency_key,
        target_chapter_id=chapter.id,
        stage="Queued for generation",
    )

    # Launch Celery task
    generate_chapter_task.delay(str(job.id))

    messages.info(request, f"Generation job started for Chapter {chapter.chapter_number}.")
    return redirect("taletomo:job_detail", job_id=job.id)


@require_POST
def chapter_approve_draft(request, project_id, chapter_id):
    """Phase 1: Approves the draft prose artifact (distinct from canon commit)."""
    project = get_object_or_404(Project, id=project_id)
    chapter = get_object_or_404(Chapter, id=chapter_id, project=project)

    if not chapter.active_draft_id:
        messages.error(request, "No active draft to approve.")
        return redirect("taletomo:chapter_edit", project_id=project.id, chapter_id=chapter.id)

    draft = get_object_or_404(DraftArtifact, id=chapter.active_draft_id)
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


@require_POST
def chapter_commit_canon(request, project_id, chapter_id):
    """Phase 2: Promotes extracted claims into confirmed canonical reality in an atomic commit."""
    project = get_object_or_404(Project, id=project_id)
    chapter = get_object_or_404(Chapter, id=chapter_id, project=project)
    user = _get_current_user(request)

    expected_head = request.POST.get("expected_head", project.active_branch_head)
    summary_text = request.POST.get("summary", "").strip() or f"Events of Chapter {chapter.chapter_number}"

    # Extract default events
    events = [
        {
            "event_type": "chapter_conclusion",
            "summary": summary_text,
            "payload": {"chapter_number": chapter.chapter_number},
        }
    ]

    try:
        snapshot = CanonService.commit_chapter_canon(
            project=project,
            chapter=chapter,
            expected_head=expected_head,
            events=events,
            facts=[],
            actor=user,
        )
        chapter.current_summary = summary_text
        chapter.save(update_fields=["current_summary"])

        messages.success(
            request,
            f"Atomic canon commit successful! Advanced to {project.active_branch_head}. Chapter {chapter.chapter_number} is locked.",
        )
    except StaleHeadError as e:
        messages.error(request, f"Commit rejected due to stale branch head: {e}")

    return redirect("taletomo:chapter_edit", project_id=project.id, chapter_id=chapter.id)


def chapter_continuity(request, project_id, chapter_id):
    project = get_object_or_404(Project, id=project_id)
    chapter = get_object_or_404(Chapter, id=chapter_id, project=project)
    findings = chapter.continuity_findings.all().order_by("-severity", "created_at")

    context = {
        "project": project,
        "chapter": chapter,
        "findings": findings,
    }
    return render(request, "taletomo/chapter_continuity.html", context)


@require_POST
def finding_update(request, finding_id):
    finding = get_object_or_404(ContinuityFinding, id=finding_id)
    new_status = request.POST.get("status")
    rationale = request.POST.get("rationale", "")

    if new_status in FindingStatus.values:
        finding.status = new_status
        if rationale:
            finding.override_rationale = rationale
        finding.save(update_fields=["status", "override_rationale", "updated_at"])
        messages.success(request, f"Continuity finding updated to {finding.get_status_display()}.")

    return redirect("taletomo:chapter_continuity", project_id=finding.project.id, chapter_id=finding.chapter.id)


def project_versions(request, project_id):
    project = get_object_or_404(Project, id=project_id)
    drafts = DraftArtifact.objects.filter(chapter__project=project).order_by("-created_at")[:50]
    return render(request, "taletomo/project_versions.html", {"project": project, "drafts": drafts})


def compare_drafts(request, project_id, left_id, right_id):
    project = get_object_or_404(Project, id=project_id)
    left_draft = get_object_or_404(DraftArtifact, id=left_id)
    right_draft = get_object_or_404(DraftArtifact, id=right_id)

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


def project_export(request, project_id):
    project = get_object_or_404(Project, id=project_id)
    return render(request, "taletomo/project_export.html", {"project": project})


def project_export_markdown(request, project_id):
    project = get_object_or_404(Project, id=project_id)
    md_content = ExportService.export_markdown(project)
    response = HttpResponse(md_content, content_type="text/markdown; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{project.slug or "novel"}.md"'
    return response


def project_export_json(request, project_id):
    project = get_object_or_404(Project, id=project_id)
    json_data = ExportService.export_json_backup(project)
    response = HttpResponse(
        json.dumps(json_data, indent=2), content_type="application/json; charset=utf-8"
    )
    response["Content-Disposition"] = f'attachment; filename="{project.slug or "novel"}_backup.json"'
    return response


@require_POST
def project_restore(request):
    user = _get_current_user(request)
    upload_file = request.FILES.get("backup_file")
    if not upload_file:
        messages.error(request, "No JSON backup file provided.")
        return redirect("taletomo:project_list")

    try:
        content = json.loads(upload_file.read().decode("utf-8"))
        restored_project = ExportService.restore_from_json(owner=user, backup_data=content)
        messages.success(request, f"Successfully restored '{restored_project.title}' from backup!")
        return redirect("taletomo:project_overview", project_id=restored_project.id)
    except Exception as e:
        messages.error(request, f"Restore failed: {e}")
        return redirect("taletomo:project_list")


def job_list(request):
    user = _get_current_user(request)
    jobs = GenerationJob.objects.filter(user=user).order_by("-created_at")
    return render(request, "taletomo/job_list.html", {"jobs": jobs})


def job_detail(request, job_id):
    job = get_object_or_404(GenerationJob, id=job_id)
    return render(request, "taletomo/job_detail.html", {"job": job})


def job_status_api(request, job_id):
    """JSON API polled by Vue component for live job progress."""
    job = get_object_or_404(GenerationJob, id=job_id)
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


def settings_providers(request):
    user = _get_current_user(request)
    configs = ProviderConfig.objects.filter(user=user)

    if request.method == "POST":
        name = request.POST.get("name", "Custom Provider").strip()
        p_type = request.POST.get("provider_type", ProviderType.FAKE)
        endpoint = request.POST.get("endpoint_url", "").strip()
        api_key = request.POST.get("api_key", "").strip()
        model_name = request.POST.get("model_name", "gpt-4o").strip()

        cfg = ProviderConfig.objects.create(
            user=user,
            name=name,
            provider_type=p_type,
            endpoint_url=endpoint or "https://api.openai.com/v1",
            default_drafting_model=model_name,
            default_planning_model=model_name,
        )
        if api_key:
            cfg.set_api_key(api_key)
            cfg.save()
        messages.success(request, f"Provider '{name}' saved and encrypted.")
        return redirect("taletomo:settings_providers")

    return render(request, "taletomo/settings_providers.html", {"configs": configs, "types": ProviderType.choices})


@require_POST
def test_provider(request):
    cfg_id = request.POST.get("config_id")
    cfg = get_object_or_404(ProviderConfig, id=cfg_id)
    try:
        adapter = ProviderGateway.get_adapter(cfg)
        res = adapter.validate_credentials()
        return JsonResponse({"success": True, "result": res})
    except Exception as e:
        return JsonResponse({"success": False, "error": str(e)}, status=400)
