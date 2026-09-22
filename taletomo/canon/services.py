from typing import Any, Dict, List, Optional
from django.db import transaction
from taletomo.canon.models import (
    CanonFact,
    Character,
    Location,
    PlotThread,
    StoryEvent,
    StorySnapshot,
    TruthScope,
    WorldRule,
)
from taletomo.core.models import AuditLog
from taletomo.planning.models import Chapter, Project


class StaleHeadError(Exception):
    """Raised when committing against a branch head that has already moved."""
    pass


class CanonService:
    @staticmethod
    def get_materialized_state(project: Project) -> Dict[str, Any]:
        """Materializes current canonical state for context assembly or snapshots."""
        characters = [
            {
                "id": str(c.id),
                "name": c.name,
                "aliases": c.aliases,
                "role": c.role,
                "is_alive": c.is_alive,
                "wounds": c.wounds_status,
                "beliefs": c.beliefs,
            }
            for c in project.characters.all()
        ]
        locations = [
            {"id": str(loc.id), "name": loc.name, "description": loc.description, "rules": loc.travel_rules}
            for loc in project.locations.all()
        ]
        rules = [
            {"id": str(r.id), "title": r.title, "rule": r.rule_statement, "category": r.category}
            for r in project.rules.all()
        ]
        open_threads = [
            {"id": str(t.id), "title": t.title, "category": t.category, "status": t.status}
            for t in project.plot_threads.filter(status__in=[PlotThread.Status.OPEN, PlotThread.Status.PROGRESSING])
        ]
        confirmed_facts = [
            {"subject": f.subject, "predicate": f.predicate, "value": f.value, "scope": f.truth_scope}
            for f in project.canon_facts.filter(canonical_status=CanonFact.Status.CONFIRMED)[:100]
        ]

        return {
            "characters": characters,
            "locations": locations,
            "rules": rules,
            "active_threads": open_threads,
            "facts": confirmed_facts,
            "branch_head": project.active_branch_head,
        }

    @staticmethod
    @transaction.atomic
    def commit_chapter_canon(
        project: Project,
        chapter: Chapter,
        expected_head: str,
        events: List[Dict[str, Any]],
        facts: List[Dict[str, Any]],
        actor=None,
    ) -> StorySnapshot:
        """Atomically appends story events, confirms canon facts, advances branch head, and saves snapshot."""
        # 1. Branch head check to prevent stale commit / race conditions
        current_project = Project.objects.select_for_update().get(id=project.id)
        if current_project.active_branch_head != expected_head:
            raise StaleHeadError(
                f"Branch head mismatch: expected '{expected_head}', but current head is '{current_project.active_branch_head}'."
            )

        # 2. Append Story Events
        for ev in events:
            StoryEvent.objects.create(
                project=current_project,
                chapter=chapter,
                event_type=ev.get("event_type", "plot_progress"),
                summary=ev.get("summary", ""),
                payload=ev.get("payload", {}),
            )

        # 3. Create or Confirm Canon Facts
        for f in facts:
            CanonFact.objects.create(
                project=current_project,
                subject=f.get("subject", ""),
                predicate=f.get("predicate", ""),
                value=f.get("value", ""),
                truth_scope=f.get("scope", TruthScope.WORLD_TRUTH),
                provenance=f"Chapter {chapter.chapter_number}",
                revision_valid_from=current_project.active_branch_head,
                canonical_status=CanonFact.Status.CONFIRMED,
            )

        # 4. Advance branch head revision (e.g. rev_1 -> rev_2)
        try:
            curr_rev_num = int(current_project.active_branch_head.split("_")[-1])
            next_head = f"rev_{curr_rev_num + 1}"
        except Exception:
            next_head = f"{current_project.active_branch_head}_next"

        old_head = current_project.active_branch_head
        current_project.active_branch_head = next_head
        current_project.save(update_fields=["active_branch_head", "updated_at"])

        # 5. Lock Chapter
        chapter.status = Chapter.Status.LOCKED
        chapter.save(update_fields=["status", "updated_at"])

        # 6. Capture Materialized Snapshot
        mat_state = CanonService.get_materialized_state(current_project)
        snapshot = StorySnapshot.objects.create(
            project=current_project,
            revision=next_head,
            materialized_state=mat_state,
        )

        # 7. Audit Log
        AuditLog.objects.create(
            project_id=current_project.id,
            actor=actor,
            action="CANON_COMMIT",
            target_type="Chapter",
            target_id=str(chapter.id),
            old_version=old_head,
            new_version=next_head,
            reason=f"Committed Chapter {chapter.chapter_number} canon and state",
        )

        return snapshot
