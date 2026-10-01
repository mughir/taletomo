import hashlib
import json
from typing import Any, Dict
from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify
from taletomo.canon.models import (
    CanonFact,
    Character,
    Faction,
    Item,
    Location,
    PlotThread,
    ProposedCanonItem,
    StoryEvent,
    TimelineEvent,
    TruthScope,
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

        chapters = list(project.chapters.prefetch_related("drafts").order_by("chapter_number"))
        active_ids = {ch.active_draft_id for ch in chapters if ch.active_draft_id}
        active_drafts = (
            {d.id: d for d in DraftArtifact.objects.filter(id__in=active_ids)}
            if active_ids
            else {}
        )

        for ch in chapters:
            draft = active_drafts.get(ch.active_draft_id)
            if not draft:
                drafts = list(ch.drafts.all())
                if drafts:
                    draft = max(drafts, key=lambda d: d.version_number)

            lines.append(f"## Chapter {ch.chapter_number}: {ch.title or 'Untitled'}\n")
            if draft:
                lines.append(draft.prose_content)
            else:
                lines.append("*(No draft written yet)*")
            lines.append("\n---\n")

        return "\n".join(lines)

    @staticmethod
    def export_epub(project: Project) -> bytes:
        """Packages the project manuscript into a valid, standards-compliant EPUB 3 archive."""
        import io
        import zipfile
        from xml.sax.saxutils import escape

        buffer = io.BytesIO()
        book_id = f"urn:uuid:{project.id}"
        author_name = project.owner.username if project.owner else "TaleTomo Author"
        title = escape(project.title or "Untitled Novel")
        date_str = timezone.now().strftime("%Y-%m-%dT%H:%M:%SZ")

        chapters = list(project.chapters.prefetch_related("drafts").order_by("chapter_number"))
        active_ids = {ch.active_draft_id for ch in chapters if ch.active_draft_id}
        active_drafts = (
            {d.id: d for d in DraftArtifact.objects.filter(id__in=active_ids)}
            if active_ids
            else {}
        )

        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            # 1. mimetype (first entry, uncompressed)
            zf.writestr("mimetype", b"application/epub+zip", compress_type=zipfile.ZIP_STORED)

            # 2. META-INF/container.xml
            container_xml = (
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">\n'
                '  <rootfiles>\n'
                '    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>\n'
                '  </rootfiles>\n'
                '</container>'
            )
            zf.writestr("META-INF/container.xml", container_xml)

            # 3. OEBPS/style.css
            style_css = (
                "body { font-family: 'Georgia', serif; line-height: 1.6; margin: 5%; color: #1a1a1a; }\n"
                "h1, h2 { text-align: center; font-weight: normal; margin-top: 1.5em; }\n"
                "p { text-indent: 1.5em; margin: 0; padding: 0; }\n"
                "p.no-indent { text-indent: 0; }\n"
                "hr.scene-break { border: 0; text-align: center; margin: 1.5em 0; }\n"
                "hr.scene-break:after { content: '* * *'; font-size: 1.1em; color: #555; }\n"
                ".title-page { text-align: center; margin-top: 25%; }\n"
                ".subtitle { font-style: italic; color: #555; margin-top: 1em; }\n"
            )
            zf.writestr("OEBPS/style.css", style_css)

            # 4. Chapters XHTML
            manifest_items = [
                '<item id="style" href="style.css" media-type="text/css"/>',
                '<item id="nav" href="toc.xhtml" media-type="application/xhtml+xml" properties="nav"/>',
                '<item id="titlepage" href="title.xhtml" media-type="application/xhtml+xml"/>',
            ]
            spine_items = [
                '<itemref idref="titlepage"/>',
                '<itemref idref="nav"/>',
            ]
            nav_points = []

            # Title page
            title_xhtml = (
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                '<!DOCTYPE html>\n'
                '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="en">\n'
                f'<head><title>{title}</title><link rel="stylesheet" href="style.css" type="text/css"/></head>\n'
                '<body>\n'
                '  <div class="title-page">\n'
                f'    <h1>{title}</h1>\n'
                f'    <p class="subtitle">{escape(project.premise or "")}</p>\n'
                f'    <p class="no-indent" style="margin-top: 3em;">By {escape(author_name)}</p>\n'
                '  </div>\n'
                '</body>\n</html>'
            )
            zf.writestr("OEBPS/title.xhtml", title_xhtml)

            for ch in chapters:
                draft = active_drafts.get(ch.active_draft_id)
                if not draft:
                    drafts = list(ch.drafts.all())
                    if drafts:
                        draft = max(drafts, key=lambda d: d.version_number)

                ch_title = escape(ch.title or f"Chapter {ch.chapter_number}")
                ch_filename = f"chapter_{ch.chapter_number}.xhtml"
                item_id = f"ch_{ch.chapter_number}"

                manifest_items.append(f'<item id="{item_id}" href="{ch_filename}" media-type="application/xhtml+xml"/>')
                spine_items.append(f'<itemref idref="{item_id}"/>')
                nav_points.append(f'<li><a href="{ch_filename}">{ch_title}</a></li>')

                body_paragraphs = []
                if draft and draft.prose_content:
                    for para in draft.prose_content.split("\n\n"):
                        clean_p = para.strip()
                        if not clean_p:
                            continue
                        if clean_p == "* * *":
                            body_paragraphs.append('<hr class="scene-break"/>')
                        else:
                            body_paragraphs.append(f'<p>{escape(clean_p)}</p>')
                else:
                    body_paragraphs.append('<p class="no-indent"><em>(No draft written yet)</em></p>')

                ch_xhtml = (
                    '<?xml version="1.0" encoding="UTF-8"?>\n'
                    '<!DOCTYPE html>\n'
                    '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="en">\n'
                    f'<head><title>{ch_title}</title><link rel="stylesheet" href="style.css" type="text/css"/></head>\n'
                    '<body>\n'
                    f'  <h2>Chapter {ch.chapter_number}: {ch_title}</h2>\n'
                    + "\n".join(f"  {p}" for p in body_paragraphs) +
                    '\n</body>\n</html>'
                )
                zf.writestr(f"OEBPS/{ch_filename}", ch_xhtml)

            # 5. OEBPS/toc.xhtml (EPUB 3 Navigation)
            toc_xhtml = (
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                '<!DOCTYPE html>\n'
                '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="en">\n'
                '<head><title>Table of Contents</title><link rel="stylesheet" href="style.css" type="text/css"/></head>\n'
                '<body>\n'
                '  <nav epub:type="toc" id="toc">\n'
                '    <h1>Table of Contents</h1>\n'
                '    <ol>\n'
                '      <li><a href="title.xhtml">Title Page</a></li>\n'
                + "\n".join(f"      {p}" for p in nav_points) +
                '\n    </ol>\n'
                '  </nav>\n'
                '</body>\n</html>'
            )
            zf.writestr("OEBPS/toc.xhtml", toc_xhtml)

            # 6. OEBPS/content.opf
            content_opf = (
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                '<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="BookId" version="3.0">\n'
                '  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">\n'
                f'    <dc:identifier id="BookId">{book_id}</dc:identifier>\n'
                f'    <dc:title>{title}</dc:title>\n'
                f'    <dc:creator>{escape(author_name)}</dc:creator>\n'
                '    <dc:language>en</dc:language>\n'
                f'    <meta property="dcterms:modified">{date_str}</meta>\n'
                '  </metadata>\n'
                '  <manifest>\n'
                + "\n".join(f"    {m}" for m in manifest_items) +
                '\n  </manifest>\n'
                '  <spine>\n'
                + "\n".join(f"    {s}" for s in spine_items) +
                '\n  </spine>\n'
                '</package>'
            )
            zf.writestr("OEBPS/content.opf", content_opf)

        return buffer.getvalue()

    @staticmethod
    def export_docx(project: Project) -> bytes:
        """Generates a standard manuscript format DOCX file (OpenXML) with title page and double-spaced styling."""
        import io
        import zipfile
        from xml.sax.saxutils import escape

        buffer = io.BytesIO()
        title = escape(project.title or "Untitled Novel")
        author_name = escape(project.owner.username if project.owner else "TaleTomo Author")

        chapters = list(project.chapters.prefetch_related("drafts").order_by("chapter_number"))
        active_ids = {ch.active_draft_id for ch in chapters if ch.active_draft_id}
        active_drafts = (
            {d.id: d for d in DraftArtifact.objects.filter(id__in=active_ids)}
            if active_ids
            else {}
        )

        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            # 1. [Content_Types].xml
            content_types_xml = (
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">\n'
                '  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>\n'
                '  <Default Extension="xml" ContentType="application/xml"/>\n'
                '  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>\n'
                '  <Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>\n'
                '</Types>'
            )
            zf.writestr("[Content_Types].xml", content_types_xml)

            # 2. _rels/.rels
            rels_xml = (
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">\n'
                '  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>\n'
                '</Relationships>'
            )
            zf.writestr("_rels/.rels", rels_xml)

            # 3. word/_rels/document.xml.rels
            doc_rels_xml = (
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">\n'
                '  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>\n'
                '</Relationships>'
            )
            zf.writestr("word/_rels/document.xml.rels", doc_rels_xml)

            # 4. word/styles.xml
            styles_xml = (
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">\n'
                '  <w:docDefaults>\n'
                '    <w:rPrDefault>\n'
                '      <w:rPr>\n'
                '        <w:rFonts w:ascii="Times New Roman" w:hAnsi="Times New Roman"/>\n'
                '        <w:sz w:val="24"/>\n'  # 12pt
                '      </w:rPr>\n'
                '    </w:rPrDefault>\n'
                '    <w:pPrDefault>\n'
                '      <w:pPr>\n'
                '        <w:spacing w:line="480" w:lineRule="auto"/>\n'  # Double-spaced
                '      </w:pPr>\n'
                '    </w:pPrDefault>\n'
                '  </w:docDefaults>\n'
                '</w:styles>'
            )
            zf.writestr("word/styles.xml", styles_xml)

            # 5. word/document.xml
            body_xml_parts = []

            # Title page
            body_xml_parts.append(
                f'<w:p><w:pPr><w:jc w:val="center"/></w:pPr><w:r><w:rPr><w:b/><w:sz w:val="36"/></w:rPr><w:t>{title}</w:t></w:r></w:p>'
                f'<w:p><w:pPr><w:jc w:val="center"/></w:pPr><w:r><w:rPr><w:i/></w:rPr><w:t>by {author_name}</w:t></w:r></w:p>'
                f'<w:p><w:pPr><w:jc w:val="center"/></w:pPr><w:r><w:t>Genre: {escape(project.genre)} | Tone: {escape(project.tone)}</w:t></w:r></w:p>'
                '<w:p><w:r><w:br w:type="page"/></w:r></w:p>'
            )

            for ch in chapters:
                draft = active_drafts.get(ch.active_draft_id)
                if not draft:
                    drafts = list(ch.drafts.all())
                    if drafts:
                        draft = max(drafts, key=lambda d: d.version_number)

                ch_title = escape(ch.title or f"Chapter {ch.chapter_number}")
                body_xml_parts.append(
                    f'<w:p><w:pPr><w:jc w:val="center"/></w:pPr><w:r><w:rPr><w:b/><w:sz w:val="28"/></w:rPr><w:t>Chapter {ch.chapter_number}: {ch_title}</w:t></w:r></w:p>'
                )

                if draft and draft.prose_content:
                    for para in draft.prose_content.split("\n\n"):
                        clean_p = para.strip()
                        if not clean_p:
                            continue
                        if clean_p == "* * *":
                            body_xml_parts.append(
                                '<w:p><w:pPr><w:jc w:val="center"/></w:pPr><w:r><w:t>#</w:t></w:r></w:p>'
                            )
                        else:
                            body_xml_parts.append(
                                f'<w:p><w:pPr><w:ind w:firstLine="720"/></w:pPr><w:r><w:t xml:space="preserve">{escape(clean_p)}</w:t></w:r></w:p>'
                            )
                else:
                    body_xml_parts.append(
                        '<w:p><w:r><w:rPr><w:i/></w:rPr><w:t>(No draft written yet)</w:t></w:r></w:p>'
                    )

                body_xml_parts.append('<w:p><w:r><w:br w:type="page"/></w:r></w:p>')

            doc_xml = (
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">\n'
                '  <w:body>\n'
                + "\n".join(f"    {p}" for p in body_xml_parts) +
                '    <w:sectPr><w:pgSz w:w="12240" w:h="15840"/><w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440"/></w:sectPr>\n'
                '  </w:body>\n'
                '</w:document>'
            )
            zf.writestr("word/document.xml", doc_xml)

        return buffer.getvalue()

    @staticmethod
    def export_webnovel_html(project: Project) -> str:
        """Formats the manuscript with clean, platform-optimized HTML ready for Royal Road / ScribbleHub."""
        from xml.sax.saxutils import escape

        chapters = list(project.chapters.prefetch_related("drafts").order_by("chapter_number"))
        active_ids = {ch.active_draft_id for ch in chapters if ch.active_draft_id}
        active_drafts = (
            {d.id: d for d in DraftArtifact.objects.filter(id__in=active_ids)}
            if active_ids
            else {}
        )

        parts = [
            '<div class="webnovel-manuscript">',
            f'  <!-- TaleTomo Web-Novel Export: {escape(project.title)} -->',
            f'  <h1 class="novel-title">{escape(project.title)}</h1>',
            f'  <p class="novel-premise"><em>{escape(project.premise)}</em></p>',
            '  <hr class="separator"/>',
        ]

        for ch in chapters:
            draft = active_drafts.get(ch.active_draft_id)
            if not draft:
                drafts = list(ch.drafts.all())
                if drafts:
                    draft = max(drafts, key=lambda d: d.version_number)

            ch_title = escape(ch.title or f"Chapter {ch.chapter_number}")
            parts.append(f'  <section class="chapter-block" id="chapter-{ch.chapter_number}">')
            parts.append(f'    <h2>Chapter {ch.chapter_number}: {ch_title}</h2>')

            if draft and draft.prose_content:
                for para in draft.prose_content.split("\n\n"):
                    clean = para.strip()
                    if not clean:
                        continue
                    if clean == "* * *":
                        parts.append('    <p style="text-align: center;"><strong>* * *</strong></p>')
                    else:
                        parts.append(f'    <p>{escape(clean)}</p>')
            else:
                parts.append('    <p><em>(No draft written yet)</em></p>')
            parts.append('  </section>\n  <hr class="separator"/>\n')

        parts.append('</div>')
        return "\n".join(parts)

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
                "established_outcomes": v.established_outcomes,
                "arcs": [
                    {
                        "arc_number": arc.arc_number,
                        "title": arc.title,
                        "conflict_goal": arc.conflict_goal,
                        "start_chapter": arc.start_chapter,
                        "end_chapter": arc.end_chapter,
                        "target_state_transition": arc.target_state_transition,
                    }
                    for arc in arc_qs
                ],
            }
            for v in project.volumes.prefetch_related("arcs").order_by("volume_number")
            for arc_qs in [sorted(v.arcs.all(), key=lambda a: a.arc_number)]
        ]

        chapters = (
            project.chapters.select_related("volume", "arc")
            .prefetch_related("plan__scenes", "drafts")
            .order_by("chapter_number")
        )

        chapters_data = []
        for ch in chapters:
            plan_data = None
            if hasattr(ch, "plan"):
                plan = ch.plan
                scenes = sorted(plan.scenes.all(), key=lambda s: s.scene_order)
                plan_data = {
                    "objectives": plan.objectives,
                    "required_beats": plan.required_beats,
                    "prohibited_outcomes": plan.prohibited_outcomes,
                    "continuity_requirements": plan.continuity_requirements,
                    "thread_operations": plan.thread_operations,
                    "end_state": plan.end_state,
                    "target_words": plan.target_words,
                    "tolerance_percent": plan.tolerance_percent,
                    "status": plan.status,
                    "scenes": [
                        {
                            "scene_order": s.scene_order,
                            "objective": s.objective,
                            "conflict": s.conflict,
                            "characters": s.characters,
                            "setting": s.setting,
                            "estimated_words": s.estimated_words,
                        }
                        for s in scenes
                    ],
                }

            drafts_list = sorted(ch.drafts.all(), key=lambda d: d.version_number)
            draft_id_to_version = {d.id: d.version_number for d in drafts_list}
            active_v = draft_id_to_version.get(ch.active_draft_id)

            drafts_data = [
                {
                    "version_number": d.version_number,
                    "prose_content": d.prose_content,
                    "word_count": d.word_count,
                    "model_name": d.model_name,
                    "status": d.status,
                    "parent_version": draft_id_to_version.get(d.parent_draft_id),
                }
                for d in drafts_list
            ]

            chapters_data.append(
                {
                    "chapter_number": ch.chapter_number,
                    "title": ch.title,
                    "pov_character_name": ch.pov_character_name,
                    "status": ch.status,
                    "current_word_count": ch.current_word_count,
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
                "slug": project.slug,
                "premise": project.premise,
                "target_chapters": project.target_chapters,
                "length_preset": project.length_preset,
                "target_words_per_chapter": project.target_words_per_chapter,
                "tolerance_percent": project.tolerance_percent,
                "genre": project.genre,
                "subgenre": project.subgenre,
                "audience": project.audience,
                "tone": project.tone,
                "language": project.language,
                "prose_language": project.prose_language,
                "pov": project.pov,
                "tense": project.tense,
                "pacing": project.pacing,
                "protagonist_type": project.protagonist_type,
                "novel_tags": project.novel_tags,
                "content_boundaries": project.content_boundaries,
                "active_branch_head": project.active_branch_head,
                "status": project.status,
            },
            "bible": {
                "pitch": bible.pitch,
                "hook": bible.hook,
                "central_conflict": bible.central_conflict,
                "reader_promise": bible.reader_promise,
                "world_setting": bible.world_setting,
                "era": bible.era,
                "magic_tech_rules": bible.magic_tech_rules,
                "factions_overview": bible.factions_overview,
                "tone_rules": bible.tone_rules,
                "prose_style_guide": bible.prose_style_guide,
                "author_constraints": bible.author_constraints,
                "status": bible.status,
            }
            if bible
            else None,
            "spine": {
                "series_promise": spine.series_promise,
                "ending_direction": spine.ending_direction,
                "major_milestones": spine.major_milestones,
                "sparse_volumes_overview": spine.sparse_volumes_overview,
                "status": spine.status,
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
                        "traits": c.traits,
                        "goals": c.goals,
                        "internal_need": c.internal_need,
                        "wounds_status": c.wounds_status,
                        "is_alive": c.is_alive,
                        "beliefs": c.beliefs,
                        "metadata": c.metadata,
                    }
                    for c in project.characters.all()
                ],
                "locations": [
                    {
                        "name": loc.name,
                        "description": loc.description,
                        "travel_rules": loc.travel_rules,
                        "current_state": loc.current_state,
                    }
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
                        "is_canon": t.is_canon,
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
                        "story_time_valid_from": f.story_time_valid_from,
                        "story_time_valid_until": f.story_time_valid_until,
                        "revision_valid_from": f.revision_valid_from,
                        "revision_valid_until": f.revision_valid_until,
                        "provenance": f.provenance,
                        "confidence": f.confidence,
                        "canonical_status": f.canonical_status,
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
                    for ev in project.story_events.select_related("chapter").all()
                ],
                "items": [
                    {
                        "name": it.name,
                        "description": it.description,
                        "status_notes": it.status_notes,
                        "is_destroyed": it.is_destroyed,
                        "destroyed_at_chapter": it.destroyed_at_chapter,
                        "current_holder_name": it.current_holder.name if it.current_holder else "",
                        "current_location_name": it.current_location.name if it.current_location else "",
                    }
                    for it in project.items.select_related("current_holder", "current_location").all()
                ],
                "proposed_items": [
                    {
                        "chapter_number": item.chapter.chapter_number,
                        "kind": item.kind,
                        "summary": item.summary,
                        "payload": item.payload,
                        "status": item.status,
                    }
                    for item in ProposedCanonItem.objects.filter(chapter__project=project).select_related("chapter")
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
        if not isinstance(backup_data, dict):
            raise ValueError("Backup data must be a JSON object")

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
        title = p_info.get("title", "Restored Novel")
        project = Project.objects.create(
            owner=owner,
            title=title,
            slug=p_info.get("slug") or slugify(title),
            premise=p_info.get("premise", ""),
            target_chapters=p_info.get("target_chapters", 100),
            length_preset=p_info.get("length_preset", "standard"),
            target_words_per_chapter=p_info.get("target_words_per_chapter", 2200),
            tolerance_percent=p_info.get("tolerance_percent", 15),
            genre=p_info.get("genre", "Fantasy"),
            subgenre=p_info.get("subgenre", ""),
            audience=p_info.get("audience", ""),
            tone=p_info.get("tone", ""),
            language=p_info.get("language", "English"),
            prose_language=p_info.get("prose_language", "English"),
            pov=p_info.get("pov", "Third Person Limited"),
            tense=p_info.get("tense", "Past Tense"),
            pacing=p_info.get("pacing", "Balanced"),
            protagonist_type=p_info.get("protagonist_type", ""),
            novel_tags=p_info.get("novel_tags", ""),
            content_boundaries=p_info.get("content_boundaries", ""),
            active_branch_head=p_info.get("active_branch_head", "rev_1"),
            status=p_info.get("status", "active"),
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
                era=b_info.get("era", ""),
                magic_tech_rules=b_info.get("magic_tech_rules", []),
                factions_overview=b_info.get("factions_overview", ""),
                tone_rules=b_info.get("tone_rules", []),
                prose_style_guide=b_info.get("prose_style_guide", ""),
                author_constraints=b_info.get("author_constraints", []),
                status=b_info.get("status", "approved"),
            )

        s_info = backup_data.get("spine")
        if s_info:
            SeriesSpine.objects.create(
                project=project,
                series_promise=s_info.get("series_promise", ""),
                ending_direction=s_info.get("ending_direction", ""),
                major_milestones=s_info.get("major_milestones", []),
                sparse_volumes_overview=s_info.get("sparse_volumes_overview", []),
                status=s_info.get("status", "approved"),
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
                established_outcomes=v.get("established_outcomes", []),
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
                pov_character_name=ch_data.get("pov_character_name", ""),
                status=ch_data.get("status", "unplanned"),
                current_word_count=ch_data.get("current_word_count", 0),
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
                    thread_operations=p_data.get("thread_operations", {"advance": [], "reinforce": [], "open": [], "close": []}),
                    end_state=p_data.get("end_state", []),
                    target_words=p_data.get("target_words", 2200),
                    tolerance_percent=p_data.get("tolerance_percent", 15),
                    status=p_data.get("status", "proposed"),
                )
                for s_data in p_data.get("scenes", []):
                    ScenePlan.objects.create(
                        chapter_plan=plan_obj,
                        scene_order=s_data.get("scene_order", 1),
                        objective=s_data.get("objective", ""),
                        conflict=s_data.get("conflict", ""),
                        characters=s_data.get("characters", []),
                        setting=s_data.get("setting", ""),
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
                traits=c.get("traits", []),
                goals=c.get("goals", ""),
                internal_need=c.get("internal_need", ""),
                wounds_status=c.get("wounds_status", ""),
                is_alive=c.get("is_alive", True),
                beliefs=c.get("beliefs", []),
                metadata=c.get("metadata", {}),
            )

        for loc in canon_data.get("locations", []):
            Location.objects.create(
                project=project,
                name=loc["name"],
                description=loc.get("description", ""),
                travel_rules=loc.get("travel_rules", ""),
                current_state=loc.get("current_state", ""),
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
                is_canon=t.get("is_canon", True),
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
            if not ch_target and chapter_map:
                ch_target = next(iter(chapter_map.values()))
            if ch_target:
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
                story_time_valid_from=f.get("story_time_valid_from", ""),
                story_time_valid_until=f.get("story_time_valid_until", ""),
                revision_valid_from=f.get("revision_valid_from", "rev_1"),
                revision_valid_until=f.get("revision_valid_until", ""),
                provenance=f.get("provenance", "Restored"),
                confidence=f.get("confidence", 1.0),
                canonical_status=f.get("canonical_status", CanonFact.Status.CONFIRMED),
            )

        char_name_map = {c.name.lower(): c for c in Character.objects.filter(project=project)}
        loc_name_map = {l.name.lower(): l for l in Location.objects.filter(project=project)}
        for it_data in canon_data.get("items", []):
            h_name = str(it_data.get("current_holder_name", "")).strip().lower()
            l_name = str(it_data.get("current_location_name", "")).strip().lower()
            Item.objects.create(
                project=project,
                name=it_data["name"],
                description=it_data.get("description", ""),
                status_notes=it_data.get("status_notes", ""),
                is_destroyed=it_data.get("is_destroyed", False),
                destroyed_at_chapter=it_data.get("destroyed_at_chapter"),
                current_holder=char_name_map.get(h_name),
                current_location=loc_name_map.get(l_name),
            )

        for p in canon_data.get("proposed_items", []):
            ch_target = chapter_map.get(p.get("chapter_number"), None)
            if not ch_target and chapter_map:
                ch_target = next(iter(chapter_map.values()))
            if ch_target:
                ProposedCanonItem.objects.create(
                    project=project,
                    chapter=ch_target,
                    kind=p.get("kind") or p.get("item_type", ProposedCanonItem.Kind.FACT),
                    summary=p.get("summary", ""),
                    payload=p.get("payload", {}),
                    status=p.get("status", ProposedCanonItem.Status.PROPOSED),
                )

        return project

