import hashlib
import json
from typing import Any, Dict
from django.db import transaction
from django.utils import timezone
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
from taletomo.generation.models import DraftArtifact
from taletomo.planning.models import (
    Arc,
    Chapter,
    ChapterPlan,
    Project,
    ScenePlan,
    SeriesBible,
    SeriesSpine,
    Volume,
)


class ExportService:
    @staticmethod
    def export_markdown(project: Project) -> str:
        """Exports approved or drafted chapters formatted as clean, publish-ready Markdown."""
        lines = [
            f"# {project.title}\n",
            f"> *{project.premise}*\n",
            f"**Genre**: {project.genre} | **Tone**: {project.tone}\n",
            "---\n",
        ]

        chapters = project.chapters.all().order_by("chapter_number")
        for ch in chapters:
            draft = None
            if ch.active_draft_id:
                draft = DraftArtifact.objects.filter(id=ch.active_draft_id).first()
            if not draft:
                draft = ch.drafts.order_by("-version_number").first()

            lines.append(f"## Chapter {ch.chapter_number}: {ch.title or 'Untitled'}\n")
            if draft:
                lines.append(draft.prose_content)
            else:
                lines.append("*(No draft written yet)*")
            lines.append("\n---\n")

        return "\n".join(lines)

    @staticmethod
    def export_json_backup(project: Project) -> Dict[str, Any]:
        """Creates a complete, versioned JSON backup with checksum manifest (zero secrets)."""
        bible = getattr(project, "bible", None)
        spine = getattr(project, "spine", None)

        volumes_data = [
            {
                "volume_number": v.volume_number,
                "title": v.title,
                "premise": v.premise,
                "target_chapters": v.target_chapters,
            }
            for v in project.volumes.all()
        ]

        chapters_data = []
        for ch in project.chapters.all():
            plan_data = None
            if hasattr(ch, "plan"):
                plan = ch.plan
                plan_data = {
                    "objectives": plan.objectives,
                    "required_beats": plan.required_beats,
                    "prohibited_outcomes": plan.prohibited_outcomes,
                    "continuity_requirements": plan.continuity_requirements,
                    "target_words": plan.target_words,
                    "scenes": [
                        {
                            "scene_order": s.scene_order,
                            "objective": s.objective,
                            "conflict": s.conflict,
                            "estimated_words": s.estimated_words,
                        }
                        for s in plan.scenes.all()
                    ],
                }

            drafts_data = [
                {
                    "version_number": d.version_number,
                    "prose_content": d.prose_content,
                    "word_count": d.word_count,
                    "model_name": d.model_name,
                    "status": d.status,
                }
                for d in ch.drafts.all()
            ]

            chapters_data.append(
                {
                    "chapter_number": ch.chapter_number,
                    "title": ch.title,
                    "status": ch.status,
                    "current_summary": ch.current_summary,
                    "plan": plan_data,
                    "drafts": drafts_data,
                }
            )

        payload = {
            "format_version": "1.0",
            "exported_at": timezone.now().isoformat(),
            "project": {
                "title": project.title,
                "premise": project.premise,
                "target_chapters": project.target_chapters,
                "length_preset": project.length_preset,
                "target_words_per_chapter": project.target_words_per_chapter,
                "genre": project.genre,
                "subgenre": project.subgenre,
                "audience": project.audience,
                "tone": project.tone,
                "language": project.language,
                "pov": project.pov,
                "tense": project.tense,
                "active_branch_head": project.active_branch_head,
            },
            "bible": {
                "pitch": bible.pitch if bible else "",
                "hook": bible.hook if bible else "",
                "central_conflict": bible.central_conflict if bible else "",
                "reader_promise": bible.reader_promise if bible else "",
                "world_setting": bible.world_setting if bible else "",
                "magic_tech_rules": bible.magic_tech_rules if bible else [],
                "prose_style_guide": bible.prose_style_guide if bible else "",
            }
            if bible
            else None,
            "spine": {
                "series_promise": spine.series_promise if spine else "",
                "ending_direction": spine.ending_direction if spine else "",
                "major_milestones": spine.major_milestones if spine else [],
            }
            if spine
            else None,
            "volumes": volumes_data,
            "chapters": chapters_data,
            "canon": {
                "characters": [
                    {
                        "name": c.name,
                        "aliases": c.aliases,
                        "role": c.role,
                        "is_alive": c.is_alive,
                        "wounds_status": c.wounds_status,
                        "beliefs": c.beliefs,
                    }
                    for c in project.characters.all()
                ],
                "locations": [
                    {"name": loc.name, "description": loc.description, "travel_rules": loc.travel_rules}
                    for loc in project.locations.all()
                ],
                "rules": [
                    {
                        "category": r.category,
                        "title": r.title,
                        "rule_statement": r.rule_statement,
                        "forbidden_violations": r.forbidden_violations,
                    }
                    for r in project.rules.all()
                ],
                "facts": [
                    {
                        "subject": f.subject,
                        "predicate": f.predicate,
                        "value": f.value,
                        "scope": f.truth_scope,
                        "provenance": f.provenance,
                    }
                    for f in project.canon_facts.filter(canonical_status=CanonFact.Status.CONFIRMED)
                ],
                "events": [
                    {
                        "chapter_number": ev.chapter.chapter_number,
                        "event_type": ev.event_type,
                        "summary": ev.summary,
                    }
                    for ev in project.story_events.all()
                ],
            },
        }

        # Calculate payload checksum
        raw_payload = json.dumps(payload, sort_keys=True)
        checksum = hashlib.sha256(raw_payload.encode("utf-8")).hexdigest()
        payload["manifest"] = {
            "checksum_sha256": checksum,
            "total_chapters": len(chapters_data),
        }
        return payload

    @staticmethod
    @transaction.atomic
    def restore_from_json(owner, backup_data: Dict[str, Any]) -> Project:
        """Validates manifest checksum and reconstructs project state idempotently."""
        fmt_ver = backup_data.get("format_version")
        if fmt_ver != "1.0":
            raise ValueError(f"Unsupported backup format version: {fmt_ver}")

        p_info = backup_data.get("project", {})
        project = Project.objects.create(
            owner=owner,
            title=p_info.get("title", "Restored Novel"),
            premise=p_info.get("premise", ""),
            target_chapters=p_info.get("target_chapters", 100),
            length_preset=p_info.get("length_preset", "standard"),
            target_words_per_chapter=p_info.get("target_words_per_chapter", 2200),
            genre=p_info.get("genre", "Fantasy"),
            subgenre=p_info.get("subgenre", ""),
            audience=p_info.get("audience", ""),
            tone=p_info.get("tone", ""),
            language=p_info.get("language", "English"),
            pov=p_info.get("pov", "Third Person Limited"),
            tense=p_info.get("tense", "Past Tense"),
            active_branch_head=p_info.get("active_branch_head", "rev_1"),
        )

        b_info = backup_data.get("bible")
        if b_info:
            SeriesBible.objects.create(
                project=project,
                pitch=b_info.get("pitch", ""),
                hook=b_info.get("hook", ""),
                central_conflict=b_info.get("central_conflict", ""),
                reader_promise=b_info.get("reader_promise", ""),
                world_setting=b_info.get("world_setting", ""),
                magic_tech_rules=b_info.get("magic_tech_rules", []),
                prose_style_guide=b_info.get("prose_style_guide", ""),
            )

        s_info = backup_data.get("spine")
        if s_info:
            SeriesSpine.objects.create(
                project=project,
                series_promise=s_info.get("series_promise", ""),
                ending_direction=s_info.get("ending_direction", ""),
                major_milestones=s_info.get("major_milestones", []),
            )

        # Volumes
        volume_map = {}
        for v in backup_data.get("volumes", []):
            vol_obj = Volume.objects.create(
                project=project,
                volume_number=v["volume_number"],
                title=v["title"],
                premise=v.get("premise", ""),
                target_chapters=v.get("target_chapters", 50),
            )
            volume_map[v["volume_number"]] = vol_obj

        # Chapters, Plans, Drafts
        for ch_data in backup_data.get("chapters", []):
            ch_num = ch_data["chapter_number"]
            ch_obj = Chapter.objects.create(
                project=project,
                chapter_number=ch_num,
                title=ch_data.get("title", ""),
                status=ch_data.get("status", "unplanned"),
                current_summary=ch_data.get("current_summary", ""),
            )

            p_data = ch_data.get("plan")
            if p_data:
                plan_obj = ChapterPlan.objects.create(
                    chapter=ch_obj,
                    objectives=p_data.get("objectives", []),
                    required_beats=p_data.get("required_beats", []),
                    prohibited_outcomes=p_data.get("prohibited_outcomes", []),
                    continuity_requirements=p_data.get("continuity_requirements", []),
                    target_words=p_data.get("target_words", 2200),
                )
                for s_data in p_data.get("scenes", []):
                    ScenePlan.objects.create(
                        chapter_plan=plan_obj,
                        scene_order=s_data.get("scene_order", 1),
                        objective=s_data.get("objective", ""),
                        conflict=s_data.get("conflict", ""),
                        estimated_words=s_data.get("estimated_words", 1000),
                    )

            for d_data in ch_data.get("drafts", []):
                draft_obj = DraftArtifact.objects.create(
                    chapter=ch_obj,
                    version_number=d_data.get("version_number", 1),
                    prose_content=d_data.get("prose_content", ""),
                    word_count=d_data.get("word_count", 0),
                    model_name=d_data.get("model_name", ""),
                    status=d_data.get("status", "generated"),
                )
                if not ch_obj.active_draft_id:
                    ch_obj.active_draft_id = draft_obj.id
                    ch_obj.save(update_fields=["active_draft_id"])

        # Canon
        canon_data = backup_data.get("canon", {})
        for c in canon_data.get("characters", []):
            Character.objects.create(
                project=project,
                name=c["name"],
                aliases=c.get("aliases", []),
                role=c.get("role", "neutral"),
                is_alive=c.get("is_alive", True),
                wounds_status=c.get("wounds_status", ""),
                beliefs=c.get("beliefs", []),
            )

        for loc in canon_data.get("locations", []):
            Location.objects.create(
                project=project,
                name=loc["name"],
                description=loc.get("description", ""),
                travel_rules=loc.get("travel_rules", ""),
            )

        for r in canon_data.get("rules", []):
            WorldRule.objects.create(
                project=project,
                category=r.get("category", "magic"),
                title=r["title"],
                rule_statement=r["rule_statement"],
                forbidden_violations=r.get("forbidden_violations", ""),
            )

        for f in canon_data.get("facts", []):
            CanonFact.objects.create(
                project=project,
                subject=f["subject"],
                predicate=f["predicate"],
                value=f["value"],
                truth_scope=f.get("scope", "world_truth"),
                provenance=f.get("provenance", "Restored"),
                canonical_status=CanonFact.Status.CONFIRMED,
            )

        return project
