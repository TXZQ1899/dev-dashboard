# Env-Scope TRAE Project Rules

Before repository-level work, read:

- `PROJECT_GUIDE.md`
- `RESOURCE_SCHEMAS.md` when the task involves infrastructure data or topology.

## Runtime boundary

Docker is the production runtime boundary.

`environment-web` is built as an image and all production collection, query and topology functions execute inside the container.

Therefore:

- new runtime features must work inside the container;
- do not depend on host-only absolute paths or host cron;
- do not require host-global Node/Python packages;
- persistent runtime/generated data must use the configured Docker volume;
- verify Docker/runtime implications when dependencies, paths, entrypoints or scheduling are changed.

## Scope

Work only on the requested task.

Do not scan or refactor the entire repository unless it is necessary to complete the task.

Do not modify collectors while implementing topology/path/risk functionality unless a collector change is explicitly required.

## Context efficiency

Prefer:

- task-relevant files;
- small fixtures;
- targeted searches;
- concise build/test error excerpts.

Avoid reading:

- full production snapshots;
- full large `topology.json`;
- raw Nginx archives;
- large logs;

unless the current task requires them.

## Topology rules

- Preserve evidence for relationships.
- Preserve confidence: EXACT / INFERRED / AMBIGUOUS / UNKNOWN.
- Do not select a candidate arbitrarily when multiple matches exist.
- Do not create relationships only to make a graph complete.
- Prefer Endpoint identity using IP + Port + Protocol when available.
- Keep TEST / SIMULATION / PRODUCT distinct.
- Preserve unresolved paths and explain where traversal stops.

## Implementation workflow

1. Read the relevant code and project guide.
2. Give a short plan.
3. Make focused changes.
4. Run targeted tests.
5. Summarize files changed, tests run and remaining gaps.

Avoid large unsolicited redesigns.
