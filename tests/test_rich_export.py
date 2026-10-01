import io
import zipfile
import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from taletomo.exporting.services import ExportService
from taletomo.generation.models import DraftArtifact
from taletomo.planning.models import Chapter
from taletomo.planning.services import PlanningService

User = get_user_model()


@pytest.fixture
def project_with_drafts(db):
    user = User.objects.create_user(username="novel_author", password="password123")
    project = PlanningService.create_project_with_scaffold(
        owner=user,
        title="Chronicles of the Clockwork Spire",
        premise="An airship engineer uncovers the heart of a mechanical god.",
        target_chapters=3,
    )
    ch1 = Chapter.objects.get(project=project, chapter_number=1)
    ch1.title = "The Broken Gear"
    draft1 = DraftArtifact.objects.create(
        chapter=ch1,
        version_number=1,
        prose_content="Steam hissed against the copper manifold.\n\n* * *\n\nAcross the brass bridge, shadows gathered.",
        word_count=13,
        model_name="test-model",
        status="accepted",
    )
    ch1.active_draft_id = draft1.id
    ch1.save()

    ch2 = Chapter.objects.get(project=project, chapter_number=2)
    ch2.title = "Flight into the Aether"
    draft2 = DraftArtifact.objects.create(
        chapter=ch2,
        version_number=1,
        prose_content="The propellers spun up to pitch.",
        word_count=6,
        model_name="test-model",
        status="accepted",
    )
    ch2.active_draft_id = draft2.id
    ch2.save()

    return project, user


@pytest.mark.django_db
def test_export_epub_archive_structure(project_with_drafts):
    project, user = project_with_drafts
    epub_bytes = ExportService.export_epub(project)
    assert isinstance(epub_bytes, bytes)
    assert len(epub_bytes) > 0

    with zipfile.ZipFile(io.BytesIO(epub_bytes), "r") as zf:
        namelist = zf.namelist()

        # 1. First entry must be 'mimetype' stored uncompressed per EPUB OCF specification
        assert namelist[0] == "mimetype"
        mimetype_info = zf.getinfo("mimetype")
        assert mimetype_info.compress_type == zipfile.ZIP_STORED
        assert zf.read("mimetype").decode("utf-8") == "application/epub+zip"

        # 2. META-INF/container.xml must point to OEBPS/content.opf
        assert "META-INF/container.xml" in namelist
        container_xml = zf.read("META-INF/container.xml").decode("utf-8")
        assert 'full-path="OEBPS/content.opf"' in container_xml

        # 3. Content OPF package file
        assert "OEBPS/content.opf" in namelist
        content_opf = zf.read("OEBPS/content.opf").decode("utf-8")
        assert f"<dc:title>{project.title}</dc:title>" in content_opf
        assert f"<dc:creator>{user.username}</dc:creator>" in content_opf
        assert 'item id="ch_1"' in content_opf
        assert 'item id="ch_2"' in content_opf
        assert '<itemref idref="ch_1"/>' in content_opf

        # 4. Table of Contents & Title Page
        assert "OEBPS/toc.xhtml" in namelist
        toc_xhtml = zf.read("OEBPS/toc.xhtml").decode("utf-8")
        assert 'epub:type="toc"' in toc_xhtml
        assert "The Broken Gear" in toc_xhtml

        assert "OEBPS/title.xhtml" in namelist
        title_xhtml = zf.read("OEBPS/title.xhtml").decode("utf-8")
        assert project.title in title_xhtml
        assert user.username in title_xhtml

        # 5. Chapter XHTML files
        assert "OEBPS/chapter_1.xhtml" in namelist
        ch1_xhtml = zf.read("OEBPS/chapter_1.xhtml").decode("utf-8")
        assert "The Broken Gear" in ch1_xhtml
        assert "Steam hissed against the copper manifold." in ch1_xhtml
        assert '<hr class="scene-break"/>' in ch1_xhtml
        assert "Across the brass bridge, shadows gathered." in ch1_xhtml


@pytest.mark.django_db
def test_export_docx_openxml_structure(project_with_drafts):
    project, user = project_with_drafts
    docx_bytes = ExportService.export_docx(project)
    assert isinstance(docx_bytes, bytes)
    assert len(docx_bytes) > 0

    with zipfile.ZipFile(io.BytesIO(docx_bytes), "r") as zf:
        namelist = zf.namelist()

        # Core OpenXML package components
        assert "[Content_Types].xml" in namelist
        assert "_rels/.rels" in namelist
        assert "word/_rels/document.xml.rels" in namelist
        assert "word/styles.xml" in namelist
        assert "word/document.xml" in namelist

        # Validate styles: Times New Roman, 12pt (sz val=24), double-spaced (spacing line=480)
        styles_xml = zf.read("word/styles.xml").decode("utf-8")
        assert 'w:ascii="Times New Roman"' in styles_xml
        assert 'w:sz w:val="24"' in styles_xml
        assert 'w:line="480"' in styles_xml

        # Validate document content
        doc_xml = zf.read("word/document.xml").decode("utf-8")
        assert project.title in doc_xml
        assert user.username in doc_xml
        assert "Chapter 1: The Broken Gear" in doc_xml
        assert "Steam hissed against the copper manifold." in doc_xml
        # Scene break formatted as centered #
        assert "<w:t>#</w:t>" in doc_xml
        assert "Across the brass bridge, shadows gathered." in doc_xml


@pytest.mark.django_db
def test_export_webnovel_html_formatting(project_with_drafts):
    project, _ = project_with_drafts
    html_output = ExportService.export_webnovel_html(project)

    assert '<div class="webnovel-manuscript">' in html_output
    assert f'<h1 class="novel-title">{project.title}</h1>' in html_output
    assert '<section class="chapter-block" id="chapter-1">' in html_output
    assert "<h2>Chapter 1: The Broken Gear</h2>" in html_output
    assert "<p>Steam hissed against the copper manifold.</p>" in html_output
    assert '<p style="text-align: center;"><strong>* * *</strong></p>' in html_output
    assert "<p>Across the brass bridge, shadows gathered.</p>" in html_output
    assert '<section class="chapter-block" id="chapter-2">' in html_output
    assert "<h2>Chapter 2: Flight into the Aether</h2>" in html_output


@pytest.mark.django_db
def test_export_views_http_endpoints_and_tenant_isolation(client, project_with_drafts):
    project, user = project_with_drafts
    other_user = User.objects.create_user(username="other_author", password="password123")

    epub_url = reverse("taletomo:project_export_epub", kwargs={"project_id": project.id})
    docx_url = reverse("taletomo:project_export_docx", kwargs={"project_id": project.id})
    webnovel_url = reverse("taletomo:project_export_webnovel", kwargs={"project_id": project.id})

    # 1. Unauthenticated -> 302 to login
    for url in [epub_url, docx_url, webnovel_url]:
        resp = client.get(url)
        assert resp.status_code == 302
        assert "/accounts/login" in resp.url or "/login" in resp.url

    # 2. Authenticated as other user -> 404 tenant isolation
    client.force_login(other_user)
    for url in [epub_url, docx_url, webnovel_url]:
        resp = client.get(url)
        assert resp.status_code == 404

    # 3. Authenticated as project owner -> 200 with appropriate mime types & attachment headers
    client.force_login(user)

    # EPUB
    resp_epub = client.get(epub_url)
    assert resp_epub.status_code == 200
    assert resp_epub["Content-Type"] == "application/epub+zip"
    assert f'filename="{project.slug}.epub"' in resp_epub["Content-Disposition"]
    assert len(resp_epub.content) > 0

    # DOCX
    resp_docx = client.get(docx_url)
    assert resp_docx.status_code == 200
    assert resp_docx["Content-Type"] == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    assert f'filename="{project.slug}.docx"' in resp_docx["Content-Disposition"]
    assert len(resp_docx.content) > 0

    # Web-Novel HTML
    resp_webnovel = client.get(webnovel_url)
    assert resp_webnovel.status_code == 200
    assert "text/html" in resp_webnovel["Content-Type"]
    assert f'filename="{project.slug}_webnovel.html"' in resp_webnovel["Content-Disposition"]
    assert b"webnovel-manuscript" in resp_webnovel.content
