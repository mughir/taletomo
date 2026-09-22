from django.urls import path
from taletomo.web import views

app_name = "taletomo"

urlpatterns = [
    path("", views.home, name="home"),
    path("projects/", views.project_list, name="project_list"),
    path("projects/new/", views.project_new, name="project_new"),
    path("projects/<uuid:project_id>/", views.project_overview, name="project_overview"),
    path("projects/<uuid:project_id>/bible/", views.project_bible, name="project_bible"),
    path("projects/<uuid:project_id>/characters/", views.project_characters, name="project_characters"),
    path("projects/<uuid:project_id>/locations/", views.project_locations, name="project_locations"),
    path("projects/<uuid:project_id>/rules/", views.project_rules, name="project_rules"),
    path("projects/<uuid:project_id>/threads/", views.project_threads, name="project_threads"),
    path("projects/<uuid:project_id>/timeline/", views.project_timeline, name="project_timeline"),
    path("projects/<uuid:project_id>/outline/", views.project_outline, name="project_outline"),
    path("projects/<uuid:project_id>/chapters/<uuid:chapter_id>/plan/", views.chapter_plan, name="chapter_plan"),
    path("projects/<uuid:project_id>/chapters/<uuid:chapter_id>/edit/", views.chapter_edit, name="chapter_edit"),
    path("projects/<uuid:project_id>/chapters/<uuid:chapter_id>/generate/", views.chapter_generate, name="chapter_generate"),
    path("projects/<uuid:project_id>/chapters/<uuid:chapter_id>/approve-draft/", views.chapter_approve_draft, name="chapter_approve_draft"),
    path("projects/<uuid:project_id>/chapters/<uuid:chapter_id>/commit-canon/", views.chapter_commit_canon, name="chapter_commit_canon"),
    path("projects/<uuid:project_id>/chapters/<uuid:chapter_id>/continuity/", views.chapter_continuity, name="chapter_continuity"),
    path("findings/<uuid:finding_id>/update/", views.finding_update, name="finding_update"),
    path("projects/<uuid:project_id>/versions/", views.project_versions, name="project_versions"),
    path("projects/<uuid:project_id>/compare/<uuid:left_id>/<uuid:right_id>/", views.compare_drafts, name="compare_drafts"),
    path("projects/<uuid:project_id>/export/", views.project_export, name="project_export"),
    path("projects/<uuid:project_id>/export/markdown/", views.project_export_markdown, name="project_export_markdown"),
    path("projects/<uuid:project_id>/export/json/", views.project_export_json, name="project_export_json"),
    path("projects/restore/", views.project_restore, name="project_restore"),
    path("jobs/", views.job_list, name="job_list"),
    path("jobs/<uuid:job_id>/", views.job_detail, name="job_detail"),
    path("jobs/<uuid:job_id>/status/", views.job_status_api, name="job_status_api"),
    path("settings/providers/", views.settings_providers, name="settings_providers"),
    path("settings/providers/test/", views.test_provider, name="test_provider"),
]
