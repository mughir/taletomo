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
                "arcs": [
                    {
                        "arc_number": arc.arc_number,
                        "title": arc.title,
                        "conflict_goal": arc.conflict_goal,
                        "start_chapter": arc.start_chapter,
                        "end_chapter": arc.end_chapter,
                        "target_state_transition": arc.target_state_transition,
                    }
                    for arc in v.arcs.all().order_by("arc_number")
                ],
            }
            for v in project.volumes.all().order_by("volume_number")
        ]

        chapters_data = []
        for ch in project.chapters.all().order_by("chapter_number"):
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
                        for s in plan.scenes.all().order_by("scene_order")
                    ],
                }

            active_v = (
                ch.drafts.filter(id=ch.active_draft_id).values_list("version_number", flat=True).first()
                if ch.active_draft_id
                else None
            )

            drafts_data = [
                {
                    "version_number": d.version_number,
                    "prose_content": d.prose_content,
                    "word_count": d.word_count,
                    "model_name": d.model_name,
                    "status": d.status,
                    "parent_version": d.parent_draft.version_number if d.parent_draft else None,
                }
                for d in ch.drafts.all().order_by("version_number")
            ]

            chapters_data.append(
                {
                    "chapter_number": ch.chapter_number,
                    "title": ch.title,
                    "status": ch.status,
                    "current_summary": ch.current_summary,
                    "volume_number": ch.volume.volume_number if ch.volume else None,
                    "arc_number": ch.arc.arc_number if ch.arc else None,
                    "active_version_number": active_v,
                    "plan": plan_data,
                    "drafts": drafts_data,
                }
            )

        payload = {
            "format_version": "1.0",
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
                "factions": [
                    {
                        "name": fac.name,
                        "goals": fac.goals,
                        "resources": fac.resources,
                        "members": fac.members,
                        "alliances": fac.alliances,
                    }
                    for fac in project.factions.all()
                ],
                "timeline": [
                    {
                        "title": t.title,
                        "description": t.description,
                        "story_time_valid_from": t.story_time_valid_from,
                        "story_time_valid_until": t.story_time_valid_until,
                        "real_order": t.real_order,
                    }
                    for t in project.timeline_events.all().order_by("real_order")
                ],
                "threads": [
                    {
                        "title": th.title,
                        "category": th.category,
                        "status": th.status,
                        "setup_chapter": th.setup_chapter,
                        "payoff_chapter": th.payoff_chapter,
                        "resolution_chapter": th.payoff_chapter,
                        "notes": th.notes,
                    }
                    for th in project.plot_threads.all()
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
                        "chapter_number": ev.chapter.chapter_number if ev.chapter else 1,
                        "event_type": ev.event_type,
                        "summary": ev.summary,
                        "payload": ev.payload,
                    }
                    for ev in project.story_events.all()
                ],
            },
        }

        # Calculate payload checksum strictly over payload without manifest
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
        """Validates manifest checksum and reconstructs complete project state idempotently."""
        fmt_ver = backup_data.get("format_version")
        if fmt_ver != "1.0":
            raise ValueError(f"Unsupported backup format version: {fmt_ver}")

        manifest = backup_data.get("manifest", {})
        expected_checksum = manifest.get("checksum_sha256")
        if not expected_checksum:
            raise ValueError("Invalid backup: missing integrity manifest checksum")

        # Validate checksum
        payload_to_verify = {k: v for k, v in backup_data.items() if k != "manifest"}
        raw_payload = json.dumps(payload_to_verify, sort_keys=True)
        computed_checksum = hashlib.sha256(raw_payload.encode("utf-8")).hexdigest()

        if computed_checksum != expected_checksum:
            raise ValueError(
                f"Backup checksum verification failed: data has been tampered with or corrupted. "
                f"Expected {expected_checksum}, calculated {computed_checksum}"
            )

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

        # Volumes & Arcs
        volume_map = {}
        arc_map = {}
        for v in backup_data.get("volumes", []):
            vol_obj = Volume.objects.create(
                project=project,
                volume_number=v["volume_number"],
                title=v["title"],
                premise=v.get("premise", ""),
                target_chapters=v.get("target_chapters", 50),
            )
            volume_map[v["volume_number"]] = vol_obj
            for arc_data in v.get("arcs", []):
                arc_obj = Arc.objects.create(
                    volume=vol_obj,
                    arc_number=arc_data["arc_number"],
                    title=arc_data.get("title", f"Arc {arc_data['arc_number']}"),
                    conflict_goal=arc_data.get("conflict_goal", ""),
                    start_chapter=arc_data.get("start_chapter", 1),
                    end_chapter=arc_data.get("end_chapter", 10),
                    target_state_transition=arc_data.get("target_state_transition", ""),
                )
                arc_map[(v["volume_number"], arc_data["arc_number"])] = arc_obj

        # Chapters, Plans, Drafts
        chapter_map = {}
        for ch_data in backup_data.get("chapters", []):
            ch_num = ch_data["chapter_number"]
            vol_num = ch_data.get("volume_number")
            arc_num = ch_data.get("arc_number")

            ch_vol = volume_map.get(vol_num)
            ch_arc = arc_map.get((vol_num, arc_num)) if (vol_num and arc_num) else None

            ch_obj = Chapter.objects.create(
                project=project,
                chapter_number=ch_num,
                title=ch_data.get("title", ""),
                status=ch_data.get("status", "unplanned"),
                current_summary=ch_data.get("current_summary", ""),
                volume=ch_vol,
                arc=ch_arc,
            )
            chapter_map[ch_num] = ch_obj

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

            # Reconstruct drafts and versions
            draft_objs = {}
            active_version_num = ch_data.get("active_version_number")
            raw_drafts = ch_data.get("drafts", [])

            for d_data in raw_drafts:
                v_num = d_data.get("version_number", 1)
                draft_obj = DraftArtifact.objects.create(
                    chapter=ch_obj,
                    version_number=v_num,
                    prose_content=d_data.get("prose_content", ""),
                    word_count=d_data.get("word_count", 0),
                    model_name=d_data.get("model_name", ""),
                    status=d_data.get("status", "generated"),
                )
                draft_objs[v_num] = draft_obj

            # Link parent drafts
            for d_data in raw_drafts:
                v_num = d_data.get("version_number", 1)
                parent_v = d_data.get("parent_version")
                if parent_v and parent_v in draft_objs and v_num in draft_objs:
                    draft_objs[v_num].parent_draft = draft_objs[parent_v]
                    draft_objs[v_num].save(update_fields=["parent_draft"])

            # Set active draft
            if active_version_num and active_version_num in draft_objs:
                ch_obj.active_draft_id = draft_objs[active_version_num].id
                ch_obj.save(update_fields=["active_draft_id"])
            elif draft_objs:
                first_v = min(draft_objs.keys())
                ch_obj.active_draft_id = draft_objs[first_v].id
                ch_obj.save(update_fields=["active_draft_id"])

        # Canon Entities
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

        for fac in canon_data.get("factions", []):
            Faction.objects.create(
                project=project,
                name=fac["name"],
                goals=fac.get("goals", ""),
                resources=fac.get("resources", ""),
                members=fac.get("members", []),
                alliances=fac.get("alliances", []),
            )

        for t in canon_data.get("timeline", []):
            TimelineEvent.objects.create(
                project=project,
                title=t["title"],
                description=t.get("description", ""),
                story_time_valid_from=t.get("story_time_valid_from", t.get("story_time", "")),
                story_time_valid_until=t.get("story_time_valid_until", ""),
                real_order=t.get("real_order", 1),
            )

        for th in canon_data.get("threads", []):
            PlotThread.objects.create(
                project=project,
                title=th["title"],
                category=th.get("category", "promise"),
                status=th.get("status", "open"),
                setup_chapter=th.get("setup_chapter", 1),
                payoff_chapter=th.get("payoff_chapter", th.get("resolution_chapter")),
                notes=th.get("notes", ""),
            )

        for ev in canon_data.get("events", []):
            ch_target = chapter_map.get(ev.get("chapter_number"), None)
            StoryEvent.objects.create(
                project=project,
                chapter=ch_target,
                event_type=ev.get("event_type", "plot_progress"),
                summary=ev.get("summary", ""),
                payload=ev.get("payload", {}),
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
