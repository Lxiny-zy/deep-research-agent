# Changelog

## [Unreleased]

### Added

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

- Preserved the original deep-research workflow as a workflow template running on the shared
  orchestration engine; Planner, Researcher, Reflector, Synthesizer, source citations, and report
  generation remain available.
- Extended workflow catalog and persistence schemas while retaining backward compatibility with
  legacy ordered `steps` definitions.

### Fixed

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
