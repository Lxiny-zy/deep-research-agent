# Changelog

## [Unreleased]

### Added

- Added a deterministic trust boundary between search and synthesis: source URL/prompt-injection
  policy decisions are audited, findings require a verbatim evidence quote, and only
  program-verified findings can reach report generation.
- Added second-pass claim validation: deterministic quote checks remain the hard gate, while
  LLM-based semantic support checks and cross-claim contradiction markers constrain which
  findings can be used by Reflector and Synthesizer.
- Persisted evidence quotes, verification status/method, source-content hashes, and verification
  reasons with an Alembic migration and API schema support.
- Persisted semantic support status, claim IDs, consistency status, contradiction links, and
  verification rationales with an Alembic migration and API schema support.
- Added a public Lxiny project welcome experience with verified administrator entry, startup credential validation, and visitor-facing capability highlights.
- Added semantic workflow input/output nodes, immediate cycle prevention, editable global fallback model settings, and dimensional card interactions.
- Added a versioned multi-agent workflow graph model with nodes, edges, viewport state, DAG
  scheduling, parallel branches, joins, and safe conditional routing.
- Added WorkflowRun and StepRun lifecycle models, node-level timeout, retry, exponential
  backoff, fallback-agent, fail-fast, token-budget, and cancellation behavior.
- Added persistent workflow definition snapshots and Blackboard checkpoints, manual resume API,
  and automatic recovery of interrupted runs during application startup.
- Added an orchestration studio UI for selecting agents, configuring dependencies, conditions,
  reflection loops, and reliability policies, plus a live execution pipeline view.
- Added a free-position node canvas with port connections, pan/zoom controls, MiniMap, and
  persisted node coordinates.
- Added transient model configuration testing and OpenAI-compatible remote model discovery, with
  searchable model selection in the profile editor.
- Added mutually exclusive temperature and reasoning-effort model parameter modes; reasoning
  profiles omit temperature from requests and send low/medium/high reasoning effort instead.

### Changed

- Redesigned the frontend research experience: top-navigation layout with ambient signal theme,
  reworked new-research / run / history / settings pages, live telemetry preview, and a refreshed
  design-system stylesheet split (`design-system.css`, `experience.css`, `run-page-styles.css`).
- Fixed follow-up frontend layout regressions from the redesign (grid alignment, overflow, and
  responsive breakpoints).
- Preserved the original deep-research workflow as a workflow template running on the shared
  orchestration engine; Planner, Researcher, Reflector, Synthesizer, source citations, and report
  generation remain available.
- Extended workflow catalog and persistence schemas while retaining backward compatibility with
  legacy ordered `steps` definitions.

### Removed

- Removed one-off process documents that had gone stale (a local audit fix log, a superseded
  frontend redesign report, and two Docker-era review/interview snapshots); durable content lives
  in this changelog, `README.md`, and the maintained interview guides.

### Known limitations

- `docker-compose.yml` does not pass through `MAX_*`, `REQUEST_TIMEOUT`, or `LLM_USER_AGENT`
  environment variables to the API container.
- `/healthz` returns `ok` unconditionally without checking database connectivity.
- API rate limiting is per-process and keyed on `client.host`, so it is ineffective behind a
  proxy without forwarded-for handling and does not aggregate across instances.
- `save_sources` exists on the repository but has no caller yet.

### Fixed

- Kept checkpointed runs recoverable across orderly shutdowns, stopped the
  recovery producer before cancelling workers, and made resource/engine cleanup
  resilient to repeated cancellation and startup interruption.
- Recognized empty-database and URI-mode SQLite memory URLs so schema creation
  stays on the application's live connection instead of a discarded Alembic
  connection.
- Froze non-secret Catalog role behavior, prompts, profile references, and
  terminal-role semantics in each run checkpoint so Catalog edits or deletions
  cannot invalidate recovery.
- Kept cross-instance SSE subscriptions open until durable terminal state,
  added bounded terminal replay fallbacks, and atomically removed prior-attempt
  terminal events when resuming to prevent same-status ABA replays.
- Fenced every background run write with a renewable lease token, prevented stale
  workers from resurrecting expired leases, and made recovery re-read checkpoints
  after acquisition, page through all orphaned runs, and survive per-run failures.
- Preserved the initial workflow-run identity and definition across startup and
  resume, synchronized resumed status before returning `202`, and blocked deletes
  while another instance holds a run lease.
- Merged completed parallel graph branches into failure checkpoints, retained
  independent parallel scratch updates, and kept lease-loss cancellation from
  masking the original cancellation or run error.
- Allowed independent conditional parallel workflow edges, bounded long/Unicode
  edge IDs, isolated semantic canvas node/edge IDs, and made conflict retries use
  the server's current workflow version without discarding the draft.
- Fixed Tavily key-pool failover ordering and added best-effort cleanup across all
  LLM, catalog, search, and key-pool clients.
- Isolated every retry and fallback attempt on a deep Blackboard snapshot so
  failed attempts cannot leak partial mutations into later attempts or branches.
- Reconciled legacy SQLite databases with a migration-local frozen schema,
  rejected explicit `null` updates for required catalog fields, and rejected
  duplicate workflow edge IDs before persistence.
- Kept resumed runs visibly active over stale failed/cancelled snapshots and
  collapsed superseded step records by semantic workflow node.
- Made owned LLM and search clients lazy and exception-safe, honored falsey
  injected dependencies, made agent instances single-use, and closed CLI-owned
  resources on every exit path.
- Scanned raw and decoded source URL path/query/fragment for prompt-injection signals before
  sources enter LLM context, and rejected multicast, non-public, and ambiguous numeric IP hosts.
- Re-ran claim consistency verification after `team_fanout` child results are merged so cross-team
  contradictions are marked before aggregation.
- Prepared local SQLite schemas during API startup, including repair for legacy `create_all`
  databases missing the persisted evidence/semantic verification columns.
- Required the single report-producing workflow role, including custom synthesize cards, to be the
  graph terminal in the editor, API, and runtime; partial graph updates are merged and revalidated,
  and runs without a report can no longer finish successfully.
- Aligned five catalog/workflow timestamp columns with ORM non-nullability through an Alembic
  migration that backfills existing NULL values before enforcing `NOT NULL`.
- Fixed workflow drag-and-drop negotiation, preserved disabled workflow state on edits, and removed stale edge conditions when dependencies are unchecked.
- Fixed runtime settings being ignored by the legacy research stream, kept API keys out of SSE URLs, and made Docker PostgreSQL passwords safe for reserved URL characters.
- Fixed welcome-page headline collisions, project-capability navigation, card alignment, hover overflow, and narrow-device form/modal overflow.
- Added deterministic Blackboard merging for parallel graph layers and explicit lifecycle states
  for failed, skipped, retrying, and cancelled nodes.
- Added server-side validation for graph cycles, invalid references, unavailable agents, unsafe
  conditions, missing terminal roles, and invalid fallback agents.
- Added explicit ANY/ALL/SUCCESS_ALL join modes, database recovery leases for multi-instance
  deployments, in-place WorkflowRun checkpoint updates, and responsive orchestration layouts for
  desktop, split-screen, and mobile-sized windows.
- Improved visual depth with restrained background light fields, layered card surfaces, hover
  elevation, selected-node focus, conditional-edge styling, and responsive full-screen editing.
- Changed newly added workflow nodes to start disconnected, enabled free port-to-port branching,
  and added selectable, keyboard-deletable and double-click-deletable edges.
