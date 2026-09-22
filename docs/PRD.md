# TaleTomo — AI Web-Novel Maker

> **Product requirements and technical design document**
>
> Status: **Revision 2 — implementation-ready greenfield design**  
> Repository: `D:\projects\novel-maker`  
> Intended audience: AI coding agents and human developers  
> Product tagline: **Grow a premise into a world.**  
> Requirement language: **MUST**, **SHOULD**, and **MAY** are normative. Unqualified requirements are MUST requirements for their stated release phase.

## 1. Executive summary

TaleTomo is an AI-assisted web-novel creation application. A user supplies a premise and configures genre, protagonist, setting, style, target length, and AI provider. TaleTomo then helps the user build a coherent long-form novel through planning, drafting, continuity checking, revision, and export.

The product must support projects from **1 to 4,000 chapters** and configurable web-novel chapter lengths. It must preserve consistency across:

- characters, identities, relationships, knowledge, injuries, and progression;
- plot threads, promises, mysteries, foreshadowing, and payoffs;
- setting, locations, factions, history, and world rules;
- chronology, travel, possession, and causality;
- genre, tone, pacing, point of view, tense, terminology, and prose voice.

The user’s proposed flow—summary, volumes, arcs, chapters, then chapter-by-chapter generation—is correct and should be retained. The improved design adds a **series spine**, rolling planning horizons, machine-checkable chapter contracts, structured canonical story state, hybrid retrieval, versioned artifacts, and an explicit commit step.

### Core architectural decision

A 250k-token model context is **not** the novel’s memory system. A 4,000-chapter novel can exceed millions of words. Large context windows should be used to fit a richer, carefully selected evidence package. Long-term consistency comes from structured state, hierarchical summaries, retrieval, deterministic validation, and versioned commits.

## 2. Product principles

1. **Human-controlled canon:** AI suggestions are not canonical until accepted.
2. **Plan before prose:** prose is generated against an approved local contract.
3. **Rolling planning:** the whole series has a sparse spine; only the nearby future is detailed.
4. **Evidence over confidence:** continuity warnings show source evidence, not just a score.
5. **No destructive AI edits:** generated drafts and revisions are versioned.
6. **Recoverable operations:** every long task is durable, resumable, retryable, and auditable.
7. **Provider independence:** planning and story state do not depend on one vendor’s API.
8. **Server-rendered navigation:** every major destination has its own URL; JavaScript enhances pages but does not create a whole-app SPA.
9. **Cost visibility:** show estimates before paid operations and confirmed usage afterward.
10. **Linear canon first:** alternate timelines and branching are later features, not MVP complexity.

## 3. Brand direction

### Recommended name: TaleTomo

- Pronunciation: “TAYL-toh-moh”
- Concept: `tale` + Japanese `tomo` (“friend” or “companion”)
- AI companion: **Tomo**
- Story-bible label: **Tomo’s Memory**
- Continuity area: **Memory Notes**
- Generation action: **Ask Tomo**
- Mascot: a small star-tailed bookmark familiar

This is a preliminary product-name assessment, not trademark, domain, or app-store clearance. Alternatives are **NoveMori** and **Fablemi**. Before launch, perform formal trademark, domain, package-name, and social-handle checks.

## 4. Goals and non-goals

### Goals

- Let a user create a structured novel project from a premise.
- Support 1–4,000 chapters without loading the full manuscript into one request or browser page.
- Generate a series plan at multiple levels: series, volume, arc, chapter, scene.
- Generate chapters asynchronously from approved plans.
- Maintain structured character, plot, setting, timeline, and genre state.
- Detect likely contradictions with evidence and source references.
- Allow the user to review, edit, approve, reject, or regenerate output.
- Support bring-your-own-key AI providers and configurable models.
- Preserve enough metadata to reproduce why a draft was generated.
- Export prose and project knowledge without exposing secrets.

### Non-goals for MVP

- Fully autonomous generation of 4,000 publish-ready chapters with no review.
- Automatic publishing to external fiction platforms.
- Real-time multi-author collaboration.
- Training or fine-tuning models on user manuscripts.
- Guaranteed literary quality or perfect continuity.
- Print layout, audiobook production, or illustration generation.
- Microservices as an initial architecture.
- Silent automatic rewriting of approved downstream chapters.

### MVP release boundary and priority

The first usable release is a **single-author, linear-canon vertical slice**, not a demo that only calls an LLM. Requirements are prioritized as follows:

- **P0 — release blocking:** authentication and ownership, project setup, one native provider plus one safe OpenAI-compatible provider, manual and AI-assisted bible creation, series/volume/arc/chapter planning, one-chapter generation, durable jobs, immutable drafts, context manifests, continuity evidence, explicit approval, atomic canon commit, Markdown/JSON export, and restore testing.
- **P1 — required before broad availability:** hybrid retrieval, rolling re-planning, usage budgets, revision impact reports, project search, complete continuity center, backup automation, and the 4,000-chapter scale gate.
- **P2 — post-MVP:** collaboration, alternate canon branches, EPUB/DOCX, publishing integrations, local models, illustrations, audio, and automated pacing optimization.

MVP is complete only when a new user can configure a provider, create a project, approve a story bible and local plan, generate and review a chapter, commit its state, reload the application, and continue from the exact committed state without duplicated facts or jobs.

### Product success measures

- Median time from completed provider setup to first generated draft.
- Percentage of generation jobs ending in a truthful terminal state.
- Percentage of approved chapters with current summaries and story-state snapshots.
- Continuity findings accepted, dismissed, or overridden with evidence.
- Regeneration rate and draft-to-approval rate by operation and model.
- Difference between estimated and confirmed token/cost usage.
- Duplicate canonical event count and unintended overwrite count; both target zero.

## 5. Personas

### Planner-first author

Wants detailed worldbuilding, foreshadowing, milestones, and control over canon.

### Discovery writer

Starts with a premise and discovers details during drafting. Needs easy proposals, rejection, and promotion of discoveries into canon.

### Serial web-novel author

Publishes frequently and may maintain hundreds or thousands of chapters. Needs queues, summaries, next-action guidance, search, and predictable costs.

### Continuity editor

Reviews contradictions, pacing, terminology, character knowledge, and unresolved promises.

### Privacy- and cost-conscious author

Wants their own provider, encrypted credentials, clear context disclosure, and hard usage limits.

## 6. Information architecture

The application is a multi-page server-rendered web application. Use normal links and server redirects for navigation. Use page-local JavaScript for editors, polling, filters, drag-and-drop, and diffs. Do not build a client-side router or whole-app SPA.

### Global routes

```text
/
/projects
/projects/new
/projects/{project_id}
/jobs
/jobs/{job_id}
/settings/providers
/settings/account
```

### Project routes

```text
/projects/{id}/setup
/projects/{id}/bible
/projects/{id}/characters
/projects/{id}/characters/{character_id}
/projects/{id}/locations
/projects/{id}/factions
/projects/{id}/rules
/projects/{id}/timeline
/projects/{id}/threads
/projects/{id}/outline
/projects/{id}/volumes/{volume_id}
/projects/{id}/arcs/{arc_id}
/projects/{id}/chapters
/projects/{id}/chapters/{chapter_id}/plan
/projects/{id}/chapters/{chapter_id}/edit
/projects/{id}/chapters/{chapter_id}/continuity
/projects/{id}/versions
/projects/{id}/compare/{left_id}/{right_id}
/projects/{id}/usage
/projects/{id}/export
/projects/{id}/settings
```

### Main project navigation

1. Overview
2. Outline
3. Write
4. Story Bible
5. Continuity
6. Versions
7. Export
8. Settings

### Creation wizard

Each step saves server-side before navigation:

```text
/projects/new/premise
/projects/new/direction
/projects/new/world
/projects/new/cast
/projects/new/structure
/projects/new/voice
/projects/new/review
```

Wizard steps:

1. Starting point: idea, genre template, or imported outline.
2. Premise: title, hook, central conflict, reader promise.
3. Direction: genre, subgenre, audience, tone, themes, boundaries, language.
4. World: setting, era, technology/magic, factions, locations, rules.
5. Cast: protagonist, goals, internal need, allies, antagonistic forces.
6. Structure: ending direction, series promise, volumes, opening arc, milestones.
7. Voice: POV, tense, prose density, dialogue, chapter length, style constraints.
8. Review: compact project summary, estimate, and create action.

AI-generated wizard content must be visibly marked **Proposed** until accepted.

## 7. Functional requirements

### FR-1 Project setup

- Create, edit, archive, restore, and delete projects.
- Accept target chapter counts from 1 through 4,000.
- Support configurable target chapter length with presets:
  - Short: 800–1,500 words
  - Standard: 1,500–2,500 words
  - Long: 2,500–4,000 words
  - Custom: 500–10,000 words, generated scene-by-scene when a single model response cannot safely satisfy the target
- Configure genre, subgenre, audience, tone, language, POV, tense, pacing, setting, protagonist, and content boundaries.
- Distinguish manual, AI-proposed, confirmed, and deprecated values.
- Store text as Unicode and support projects whose planning language differs from their prose language.
- Treat target words as a goal with an explicit tolerance, never as permission to truncate prose mid-sentence.

### FR-2 Provider and model configuration

- Support at least one native provider and one OpenAI-compatible endpoint in MVP.
- Store endpoint, model, API key, capability metadata, and user-defined limits.
- Encrypt API keys at rest; never show the full key after save.
- Never place secrets in prompts, logs, exports, browser JSON, or error messages.
- Validate credentials and model availability with a minimal request.
- Show detected context limit, structured-output support, streaming support, and pricing status.
- Permit separate models for planning, drafting, summarization, extraction, and checking.
- Let users set per-job, daily, and project-level token or cost limits.
- Saving provider configuration and testing it are separate operations.
- Maintain a capability profile per model: context limit, maximum output, structured-output mode, streaming, tool support, tokenizer source, price source, and last verification time.
- When provider capabilities cannot be detected reliably, require user-entered limits and label them unverified.
- Treat custom endpoints as untrusted network destinations: reject loopback, link-local, private-network, metadata-service, non-HTTP(S), and redirect-to-private targets unless an administrator explicitly allowlists them.
- Never infer successful support for 250k context from a model name alone; validate configured limits or mark them user-asserted.
- The provider gateway and context assembler MUST support verified model profiles with context windows of at least 250,000 tokens without hard-coded truncation below the model profile. Smaller-context models MAY be used when the assembled mandatory context fits.

### FR-3 Hierarchical planning

Represent and edit:

```text
Series premise
  → Series spine
    → Volume plan
      → Arc plan
        → Chapter contract
          → Scene plan
            → Prose
```

- Generate plans in bounded batches, not one 4,000-chapter prompt.
- Allow manual editing, reordering, approval, locking, and superseding.
- Show affected plans and threads before applying a replan.
- Store dependencies, intended state transitions, setup/payoff links, and provenance.
- Generate a 5–20 chapter detailed horizon while preserving only a sparse plan farther ahead.

### FR-4 Canon and story state

Maintain structured records for:

- characters and aliases;
- relationships;
- locations and movement;
- factions;
- items, abilities, injuries, and progression;
- world rules;
- timeline events;
- character knowledge and beliefs;
- plot threads, promises, mysteries, deadlines, and payoffs;
- glossary and terminology;
- author constraints and style rules.

AI-extracted changes remain proposed until accepted. Confirmed canon cannot be silently rewritten by prose generation.

### FR-5 Context assembly

Every generation request uses a typed context package containing:

1. Immutable series rules and style constraints.
2. Current volume and arc intent.
3. Chapter contract and scene plan.
4. Current structured story state.
5. Character knowledge and relationships.
6. Timeline and location constraints.
7. Active plot threads and obligations.
8. Recent chapter and arc summaries.
9. Retrieved older evidence.
10. User-pinned excerpts.
11. Output schema and generation instructions.

Persist a context manifest with source IDs, source versions, token counts, priorities, and hashes. Never silently exceed the provider’s input budget.

### FR-6 Draft generation

- Generate one chapter or scene sequence at a time.
- Run as a durable background job.
- Preserve usable partial output after provider failure.
- Save each attempt as a separate noncanonical version.
- Support full regeneration and selected-scene regeneration.
- Record provider, model, parameters, prompt version, context manifest, timing, usage, and errors.
- Never overwrite an accepted draft automatically.

### FR-7 Continuity checking

Check at minimum:

- identity and naming;
- character status, injuries, possessions, and abilities;
- character knowledge versus world truth;
- location and travel plausibility;
- timeline ordering;
- world-rule violations;
- premature or duplicate plot resolution;
- setup/payoff status;
- POV and tense drift;
- terminology and genre/tone drift.

Every finding includes severity, confidence, claim, conflicting evidence, source references, and suggested action. Literary disagreement should normally be advisory; deterministic integrity violations may block approval.

### FR-8 Review and approval

- Compare draft versions side by side.
- Edit prose directly.
- Approve, reject, or regenerate.
- Review extracted canon changes separately.
- Show unresolved high-severity findings before approval.
- Allow explicit override with an optional rationale.
- Mark approved chapters locked.

### FR-9 Search and navigation

- Navigate directly to chapter numbers up to 4,000.
- Search titles, prose, summaries, entities, threads, and glossary terms.
- Filter by volume, arc, POV, status, and continuity finding.
- Paginate all large lists.
- Show previous/next chapter links and breadcrumbs.

### FR-10 Import, export, and backup

MVP exports:

- Markdown
- Plain text
- Structured JSON project backup

Later exports:

- DOCX
- EPUB
- Per-volume ZIP
- Platform-specific chapter bundles

Exports run asynchronously for large projects and never include credentials.

### FR-11 Job and usage management

Workflow states:

```text
Queued → Preparing context → Submitted → Generating → Checking → Ready
                    ↘ Cancelled          ↘ Failed / Needs review / Stale
```

- Persist jobs and attempts.
- Use idempotency keys.
- Show progress, stage, timestamps, result location, errors, retry, and cancel actions.
- Mark interrupted jobs truthfully after lease timeout or worker restart.
- Display estimated cost before work and confirmed provider usage afterward.
- Separate the logical job from provider-call attempts. Retrying creates an attempt; it does not duplicate the logical operation.
- Record an `unknown_provider_outcome` state when a connection fails after submission and the provider does not expose request lookup. Do not automatically resubmit a potentially billable request without warning.
- Cancellation means “stop further local work and request provider cancellation when supported”; it must not falsely claim a remote request was cancelled.
- Every job transition must be append-only in the audit log and guarded by an expected current state.
- A worker must acquire a renewable lease before executing a step. Expired leases may be recovered, but committed steps must remain idempotent.
- Before a billable call, reserve the operation’s maximum estimated tokens/cost atomically against applicable limits. Reconcile the reservation with confirmed usage afterward and release unused capacity. Concurrent jobs must not bypass a shared limit.
- If pricing or token accounting is unknown, display that fact and enforce a user-approved token/request ceiling rather than presenting a fabricated cost estimate.

## 8. Long-form generation architecture

### 8.1 Modular monolith first

Start with a modular monolith and durable workers:

```text
Server-rendered web app
        ↓
Project and workflow orchestrator
        ├── Planning module
        ├── Context assembly module
        ├── Provider gateway
        ├── Generation pipeline
        ├── Consistency engine
        ├── Version/revision module
        └── Export module
        ↓
PostgreSQL + object storage + retrieval index + job broker
```

Selected implementation baseline for the first implementation:

- Django with server-rendered templates;
- PostgreSQL for transactional state;
- PostgreSQL full-text search plus pgvector behind a retrieval interface;
- Celery workers with Redis as transport, while PostgreSQL workflow records remain the source of truth for state and idempotency;
- S3-compatible object storage for large artifacts and exports;
- server-rendered templates with small page-local Vue components, vendored locally, and no client-side router;
- pytest, pytest-django, and deterministic fake-provider fixtures for verification.

An implementation may replace a selected component only through a short architecture decision record explaining compatibility, migration impact, and how the same acceptance criteria remain satisfied.

Do not split into microservices until measured scaling or team ownership requires it.

### 8.2 Domain entities

| Entity | Purpose |
|---|---|
| Project | Top-level novel configuration |
| SeriesBible | Genre, voice, themes, rules, boundaries |
| SeriesSpine | Sparse long-range story map |
| Volume | Major narrative/publishing unit |
| Arc | Operational conflict and state transition |
| ChapterPlan | Chapter contract and dependencies |
| ScenePlan | Local objective, conflict, and transition |
| Character | Identity, traits, goals, state, knowledge |
| Location | Description, rules, history, current state |
| Faction | Goals, resources, membership, alliances |
| WorldRule | Magic, technology, social, or physical constraint |
| TimelineEvent | Ordered story event with validity interval |
| PlotThread | Setup, progress, deadline, payoff, status |
| CanonFact | Subject/predicate/value with scope and provenance |
| StoryEvent | Immutable state change produced by accepted prose |
| StorySnapshot | Materialized state at a branch revision |
| Summary | Scene, chapter, arc, volume, or series compression |
| DraftArtifact | Immutable prose version |
| ContextManifest | Exact sources and budgets used for generation |
| ContinuityFinding | Evidence-backed possible inconsistency |
| GenerationJob | Durable workflow and attempts |
| RevisionBranch | Isolated alternate history for major edits |

### 8.3 Required state machines

```text
Plan:     proposed → approved → locked → superseded
Draft:    generating → generated → under_review → accepted | rejected
Chapter:  unplanned → planned → drafting → review → approved → locked
Fact:     proposed → confirmed → deprecated
Finding:  open → fixed | intentional | dismissed | superseded
```

Transitions require authorization, expected-version checks, and an audit entry. `generated`, `accepted`, `approved`, and `confirmed` are distinct states and must never be collapsed into one boolean.

### 8.4 Domain invariants

- Project-scoped records MUST carry `project_id`; retrieval and authorization MUST filter by it before ranking or loading content.
- Stable IDs identify entities; names and aliases are editable attributes and cannot serve as foreign keys.
- Chapter numbers are unique within a project branch, but reorder operations preserve stable chapter IDs.
- Every generated artifact is immutable. Editing creates a new version linked to its parent.
- Every summary, embedding, extraction, and finding records its source artifact version.
- Only one atomic commit may advance a branch from a given head revision.
- Canon events, branch-head advancement, materialized-state updates, and audit records occur in one database transaction or not at all.
- Object-store uploads are finalized before their database artifact becomes visible; orphaned uploads are collected safely.
- Derived data is marked stale when a source changes and cannot be presented as current until rebuilt.
- Delete is soft-delete plus a documented grace period until permanent purge; exports and backups must represent deletion state correctly.

### 8.5 Storage boundaries

- PostgreSQL is authoritative for users, projects, configuration, plans, prose versions, canon, workflow state, context manifests, findings, usage, and audit records.
- Approved prose and draft text remain transactionally versioned in PostgreSQL so search, revision, and restore do not depend on an eventually consistent object copy.
- Object storage holds large imports, generated export bundles, and optionally encrypted raw provider payloads. Each object has a database record with owner, size, media type, checksum, retention class, and creation source.
- Redis is disposable transport/cache state. Losing Redis may delay work but must not lose the authoritative job, accepted draft, canon, budget reservation, or audit history.
- Embeddings are derived data. They reference source artifact versions and can be rebuilt without changing canon.

## 9. Canonical state model

The system stores several complementary memories:

1. **Immutable story events:** what happened in narrative reality.
2. **Materialized current state:** latest derived state for fast context assembly.
3. **Character beliefs:** what each character thinks is true.
4. **Narrator claims:** assertions made in prose.
5. **Hierarchical summaries:** compact retrieval layers.
6. **Raw prose:** authoritative textual evidence.
7. **Open obligations:** promises, clues, debts, mysteries, and deadlines.

A canonical fact contains:

```text
subject
predicate
value
truth_scope
story_time_valid_from
story_time_valid_until
revision_valid_from
revision_valid_until
provenance
confidence
canonical_status
```

Truth scopes must distinguish:

- `world_truth`
- `narrator_assertion`
- `character_belief:<id>`
- `rumor`
- `prophecy`
- `plan_only`
- `author_note`
- `noncanonical_draft`

A mistaken character belief is not automatically a world contradiction.

### Atomic commit rule

Only the commit stage can promote generated claims into canonical state:

```text
Draft
 → extract claims/events
 → run deterministic and AI checks
 → user approves draft
 → user reviews proposed canon changes
 → atomically append story events
 → update materialized state
 → write summaries and retrieval records
 → advance branch head
```

If a worker crashes before commit, no canonical state changes are allowed.

The commit transaction must compare the expected branch head with the current head. A mismatch produces `STALE`, preserves the reviewed draft, and requires context revalidation; it must not auto-merge. Canon changes are append-only corrections or superseding facts, never destructive history edits.

## 10. Rolling planning strategy

Do not detail all 4,000 chapters before writing chapter one.

| Horizon | Detail |
|---|---|
| Entire series | Sparse spine, endpoint, irreversible milestones |
| Current and next volume | Detailed arcs and state transitions |
| Next 5–20 chapters | Concrete chapter contracts |
| Current chapter | Scene plans and word allocation |

At the end of each chapter, update the nearby contracts if the actual state differs. Preserve higher-level invariants. At arc boundaries, review actual versus intended outcomes and detail the next arc. At volume boundaries, run a broader audit.

Each plan stores `depends_on`, `establishes`, `supersedes`, `valid_from_revision`, and invalidation status. Earlier edits mark dependent plans stale or invalid; they do not silently rewrite approved future prose.

## 11. Retrieval and context budgeting

### Retrieval sources

- immutable series constraints;
- chapter and scene contract;
- current entity state;
- nearby timeline events;
- participant profiles and beliefs;
- locations and rules;
- open threads and obligations;
- recent summaries;
- relevant older passages;
- glossary and style anchors;
- previous continuity decisions.

### Hybrid retrieval

1. Extract entity and thread IDs from the chapter contract.
2. Deterministically expand one graph hop for mandatory relationships and state.
3. Search full text and embeddings with project, branch, revision, truth-scope, and temporal filters.
4. Rerank by semantic similarity, lexical similarity, entity overlap, thread overlap, temporal relevance, dependency strength, and salience.
5. Diversify evidence so one repeated passage does not consume the package.
6. Verify that every mandatory contract constraint has supporting context.

Never retrieve future-chapter information into an earlier chapter unless it is explicitly marked as an approved plan or author note.

### Token budget

```text
usable_input = model_context_limit
              - requested_output_tokens
              - safety_margin
              - provider_overhead_reserve
```

Use category budgets for constraints, plans, state, retrieved evidence, and recent prose. A 250k model can receive more evidence, but the same filtering and provenance rules apply. If mandatory context cannot fit, stop with an actionable preflight error; do not silently truncate canon.

## 12. Chapter contract

Every generated chapter is validated against a structured contract such as:

```yaml
chapter_number: 812
pov_character: char_12
location_ids: [loc_43]
time_window: {start: "Year 18 Frost 4 08:00", end: "Year 18 Frost 4 11:00"}
objectives:
  - Force the protagonist to accept a temporary alliance.
required_beats:
  - The evidence is shown.
  - The protagonist notices the authentic seal.
prohibited_outcomes:
  - Do not identify the traitor.
  - Do not resolve the siege.
continuity_requirements:
  - The protagonist's left hand remains injured.
thread_operations:
  advance: [thread_gate_traitor]
  reinforce: [thread_injury]
  open: []
  close: []
end_state:
  - Conditional alliance exists.
target_words: 2600
tolerance_percent: 15
```

The contract is the immediate specification for planning, generation, checking, and review.

## 13. Generation pipeline

```text
PLANNED
 → CONTEXT_ASSEMBLED
 → SCENE_PLAN_READY
 → DRAFTED
 → EXTRACTED
 → CRITIQUED
 → REPAIRING (optional)
 → APPROVED
 → COMMITTING
 → COMMITTED
```

Failure states: `RETRYABLE_FAILURE`, `NEEDS_USER_REVIEW`, `CANCELLED`, `STALE`.

### Pipeline rules

1. Load contract and active branch head.
2. Validate contract against current canon.
3. Assemble and persist context manifest.
4. Generate or validate scene plan.
5. Generate prose as noncanonical artifact.
6. Extract claims, events, thread operations, and summaries.
7. Run deterministic checks.
8. Run evidence-grounded model critique.
9. Repair bounded issues; re-extract after every repair.
10. Stop for user review if blocking issues remain.
11. Commit atomically only after approval.
12. Enqueue summarization, embedding, and post-commit checks.

Never blindly retry content-policy failures. Transport and rate-limit failures may retry with bounded exponential backoff and jitter.

The repair step may change only the noncanonical draft. It cannot weaken the approved chapter contract, remove a blocking finding, alter retrieved evidence, or mutate canon. Any proposed plan/canon change returns to explicit user review.

## 14. Revision and branching

Editing an approved earlier chapter creates a revision artifact. For changes that alter canon, create a branch or explicit revision transaction.

Before acceptance, show:

- affected summaries;
- affected story events and snapshots;
- invalidated chapter plans;
- downstream chapters requiring review;
- plot threads whose setup/payoff relationship changes.

Approved downstream prose must not be automatically overwritten. The user chooses whether to recompute derived state, review impacted chapters, or keep the edit text-only.

## 15. UX requirements

### Dashboard

Show:

- continue writing;
- novels and current arc;
- generation activity;
- continuity issues needing attention;
- unresolved placeholders;
- recent snapshots;
- next recommended action.

### Outline

Render server-side hierarchy rows with status, POV, target/actual words, primary conflict, findings, and generation state. Paginate or window large lists.

### Editor

Three-column layout:

- left: nearby chapter navigator;
- center: manuscript editor, scenes, word count, save state;
- right: Tomo assistant with Assist, Context, Checks, and Notes tabs.

AI output always enters a diff state with Insert, Replace, Regenerate, and Discard actions. Automatic snapshots occur before large replacements.

### Continuity center

Group findings by character, timeline, location, rules, terminology, plot threads, and repetition. Every finding must show exact source references and actions: Fix, Add exception, Mark intentional, or Dismiss.

### Job page

Show requested operation, current stage, context preparation, usage, result link, retry/cancel controls, and whether work was saved. Long requests must never leave the user staring at an unresponsive POST.

### Error and recovery UX

Every actionable failure message must answer:

1. Which operation and stage failed?
2. Was user work saved?
3. Was a provider request submitted, and is billing status known?
4. Is retry safe, unsafe, or unnecessary?
5. What exact next action can the user take?

Validation errors remain inline and preserve submitted non-secret values. Provider, worker, and export failures link to a persistent job page. A failed continuity scan never hides or deletes a generated draft.

## 16. Non-functional requirements

### Performance and scale

- The initial reference environment is a single application deployment with 4 vCPU, 8 GB RAM, PostgreSQL, Redis, and four generation-worker processes, tested with a 4,000-chapter fixture and 20 concurrent interactive users. Record exact software and hardware details with every benchmark.
- Creating a durable job and returning its page should complete within 2 seconds at p95 under the reference load; provider execution is excluded.
- Normal server-rendered project pages should complete server processing within 500 ms at p95 on the reference environment, excluding large searches and exports.
- Chapter, job, finding, and audit lists use keyset or bounded pagination and never render all 4,000 chapters.
- Opening a 4,000-chapter project must not load chapter prose, all plans, or all embeddings into application memory.
- Context assembly time and selected token count must be observable per job.
- Search and retrieval targets must be measured on the release fixture corpus, not asserted from query plans alone.

### Reliability and recovery

- Database transactions protect every canon mutation.
- Automated backups include PostgreSQL and required object artifacts.
- Restore is tested before release and periodically afterward; a backup without a successful restore test is not considered verified.
- Initial operational targets: RPO no more than 24 hours and RTO no more than 4 hours. Production owners may tighten them through deployment configuration.
- Worker restart, duplicate delivery, and browser refresh must not duplicate accepted artifacts, canon events, or provider attempts.

### Accessibility and browser behavior

- Target WCAG 2.2 AA for primary creation, planning, editing, review, and provider flows.
- All primary actions are keyboard reachable and have visible focus.
- Status and continuity severity are never conveyed by color alone.
- Support the current and previous major versions of Chrome, Edge, Firefox, and Safari.
- Editor recovery works after refresh, expired session, and transient network loss; never claim “saved” before server acknowledgement.

### Observability

- Structured logs use correlation IDs for request, job, attempt, project, and provider request IDs without logging prose or secrets by default.
- Metrics cover queue age, job duration by stage, retries, unknown provider outcomes, token usage, cost, context size, retrieval latency, stale artifacts, and failed commits.
- Health endpoints distinguish web readiness, worker readiness, database connectivity, queue connectivity, and object-store connectivity.
- Audit records capture actor, action, target, old/new version references, timestamp, and reason where supplied.

### Data retention and portability

- Retention periods for raw provider responses, prompts, generated artifacts, deleted projects, audit records, and exports are separately configurable and shown to administrators.
- Project export uses a versioned schema and includes a manifest with checksums and format version.
- Restore validates schema version, checksums, ownership, references, and missing artifacts before mutating an existing account.

## 17. Security and privacy

- Encrypt provider secrets at rest using an application-managed key or secret manager.
- Use least-privilege database and object-storage credentials.
- Redact keys, authorization headers, and sensitive provider payload fields from logs.
- Provide a context disclosure before generation: what project content is being sent and to which provider.
- Offer project deletion and export.
- Do not use user manuscripts for training unless a separate explicit product policy and consent flow exists.
- Separate user authorization from project ownership checks on every route.
- Validate uploaded files and enforce size/type limits.
- Apply rate limits to provider tests, generation, exports, and account endpoints.
- Protect state-changing browser requests with CSRF defenses and secure session-cookie settings.
- Escape generated prose, imported text, model errors, and provider metadata by default; sanitized rich text is opt-in. Model output is untrusted content, not HTML or instructions to the application.
- Treat imported manuscripts and retrieved passages as untrusted prompt data. Delimit them from system instructions and never permit their content to select tools, endpoints, credentials, or application actions.
- Prevent cross-project and cross-user retrieval with authorization filters before full-text or vector ranking; test this as a security invariant.
- Rotate encryption keys through versioned key identifiers without re-entering provider credentials.
- Restrict outbound provider requests by scheme, DNS/IP resolution, redirect validation, port policy, timeout, and response-size limits to mitigate SSRF and resource exhaustion.
- Apply decompression, archive-entry, filename, and total-size limits to imports to prevent zip bombs and path traversal.
- Define whether prompts and prose are included in telemetry; default to metadata-only telemetry.

## 18. Recommended implementation sequence

### Phase 0 — foundation

- Project repository and local development setup.
- Authentication and project ownership.
- PostgreSQL schema and migrations.
- Server-rendered layout and project navigation.
- Provider adapter interface with fake test provider.
- Durable job model and worker heartbeat.

### Phase 1 — vertical slice

- Create project.
- Configure one provider.
- Create series bible manually.
- Create one volume, arc, chapter contract, and scene plan.
- Generate one chapter.
- Save draft version and context manifest.
- Run basic continuity checks.
- Approve chapter and commit one story snapshot.
- Export and restore the vertical-slice project through the versioned JSON backup format.

### Phase 2 — long-form core

- Rolling horizon planning.
- Canon facts, events, temporal validity, and character beliefs.
- Hybrid retrieval.
- Hierarchical summaries.
- Plot-thread ledger and setup/payoff tracking.
- Retry, resume, cancellation, usage, and cost controls.

### Phase 3 — author workflow

- Full editor with diff and version history.
- Revisions and downstream impact reports.
- Search and continuity center.
- Markdown export plus expanded backup/export management UI.
- Multiple provider adapters.

### Phase 4 — scale and intelligence

- 4,000-chapter simulation and performance hardening.
- Better reranking and retrieval evaluation.
- Voice drift detection.
- Long-range pacing analysis.
- Optional branches, collaborative review, DOCX/EPUB, and publishing integrations.

## 19. Testing and verification

### Unit tests

- Token budgeting and context quotas.
- Temporal interval overlap.
- Fact supersession and truth scope.
- Alias resolution.
- Character knowledge propagation.
- Dependency invalidation.
- Idempotency keys.
- Provider error classification.

### Property tests

- Rebuilding state from events equals incremental projection.
- Non-overlapping temporal facts do not conflict.
- Retrying commits does not duplicate events.
- Context never exceeds computed budget.
- Context never crosses project, branch, or revision boundaries.

### Retrieval fixtures

Include similar names, old facts, rumors, future facts, superseded facts, and evidence hundreds of chapters apart. Measure required-fact recall, irrelevant-fact precision, mandatory-constraint coverage, diversity, and future-information leakage.

### Continuity fixtures

Test resurrection without explanation, impossible travel, item ownership conflict, secret leakage, age conflict, broken world rule, premature reveal, intentional lie, valid state change, unreliable narrator, and explicit retcon.

### Workflow integration tests

Use a deterministic fake provider to test successful generation, malformed extraction, timeout after submission, worker crash at every stage, repair exhaustion, cancellation, stale branch head, resume, and duplicate-submit protection.

### Scale gate

Before release, run a synthetic 4,000-chapter state simulation. It must demonstrate:

- bounded context growth;
- no future or cross-branch retrieval leakage;
- rebuildable materialized state;
- resumable jobs;
- no duplicate canonical events after crashes;
- acceptable search and navigation latency;
- complete downstream impact reports after earlier revisions.

### Security and isolation tests

- User A cannot read, search, retrieve, mutate, export, or infer User B’s project records.
- Vector and full-text retrieval cannot return a different project or future branch revision.
- Custom endpoints cannot reach blocked private, loopback, link-local, or cloud-metadata destinations, including through redirects or DNS rebinding.
- Imported prompt-injection text cannot alter system instructions or trigger application actions.
- Logs, traces, job payloads, browser data, and exports contain no provider keys.
- Rendered model output and imported prose cannot execute script.

### Quality evaluation gates

Maintain versioned golden story fixtures. For the release fixture set:

- mandatory chapter-contract constraint retrieval recall must be at least 99%;
- future-chapter, cross-branch, and cross-project evidence leakage must be zero;
- all deterministic blocking contradictions must be detected;
- intentional lies and temporally non-overlapping facts must not be reported as blocking contradictions;
- every finding must reference resolvable evidence IDs;
- prompt, retrieval-weight, model, and evaluator versions must be stored with every evaluation run.

These thresholds evaluate the deterministic system and curated fixture corpus. They are not claims of perfect real-world literary judgment.

## 20. Acceptance criteria

- A user can create targets of 1, 400, and 4,000 chapters.
- Setup proposals remain visibly proposed until accepted.
- Provider secrets are encrypted and absent from logs, exports, and context manifests.
- A generation request returns quickly with a persisted job.
- Refreshing or leaving the page does not lose or duplicate the job.
- Every draft stores model, parameters, context manifest, source versions, and usage.
- Context assembly stays within provider limits or gives a clear preflight error.
- Confirmed canon cannot be silently mutated by generation.
- A seeded contradiction creates an evidence-linked continuity finding.
- Failed checks do not delete the draft.
- Earlier-chapter edits never silently rewrite later approved prose.
- A 4,000-chapter project can be navigated without rendering all chapters.
- Export includes prose, plans, bible, summaries, and structured canon but no secrets.
- Crash-and-resume tests create no duplicate canonical events.
- An uncertain provider timeout is displayed as an unknown outcome and is not silently resubmitted.
- Cross-user and cross-project isolation tests pass for routes, search, vector retrieval, jobs, and exports.
- A restore test reconstructs a project whose prose, plans, canon, artifact versions, and checksums match the backup manifest.
- Primary authoring flows pass keyboard and automated accessibility checks without color-only status meaning.
- The golden continuity/retrieval suite meets the quality gates defined above.

## 21. Decision register

These are the implementation defaults. Changing one requires an architecture decision record; they are not invitations for an implementation agent to choose silently.

| Decision | Recommended MVP default |
|---|---|
| Canon approval | Draft approval and canon-change approval are separate steps |
| Planning | Rolling horizon; detailed next 5–20 chapters |
| Branching | Linear canon only; revisions create reviewable branches later |
| Models | Separate operation profiles behind one provider gateway |
| Retrieval | Deterministic entity expansion + full text; embeddings optional but supported |
| Continuity blocking | Block only deterministic integrity failures; warn on literary findings |
| Raw provider retention | Raw response envelopes disabled by default; normalized draft/artifact content is retained according to project history |
| Cost control | Preflight estimate, hard project limit, confirmed post-call usage |
| Frontend | Server-rendered pages with vendored, page-local Vue and no client-side router |
| Backend | Django modular monolith with durable Celery workers |
| Job transport | Celery/Redis transport; PostgreSQL workflow state is authoritative |
| Custom endpoints | Disabled for unsafe network ranges; admin allowlist for exceptions |
| Accessibility | WCAG 2.2 AA for primary workflows |
| Backup baseline | Daily backup, RPO ≤24h, RTO ≤4h, verified restore |
| Deleted-project grace period | 30 days before permanent purge, unless an administrator applies a shorter legal/security hold policy |

## 22. Definition of done for an AI coding agent

An implementation is not complete when screens or prompts merely exist. It is complete only when the agent has:

1. Implemented the requested slice with migrations and error handling.
2. Added tests before or alongside behavior.
3. Exercised the real provider boundary with a fake provider and, when credentials are available, a safe provider validation request.
4. Verified background job persistence, retry, and resume behavior.
5. Verified that secrets are absent from logs and exports.
6. Verified generated output is versioned and noncanonical until approval.
7. Verified context manifests and token limits.
8. Verified at least one seeded continuity contradiction with evidence.
9. Run the relevant test suite, build, and lint commands.
10. Reported real command output, remaining risks, and any unverified integrations.
11. Verified tenant isolation, CSRF, output escaping, and custom-endpoint SSRF protections for affected features.
12. Updated migrations, backup/export schema, audit events, and operational documentation when the data model changes.
13. Exercised restore or rollback behavior when the slice affects persistent state.

No agent should claim 4,000-chapter support based only on a database integer range. The scale gate and long-horizon simulation are required evidence.
