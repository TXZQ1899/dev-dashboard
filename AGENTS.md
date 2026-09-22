# Env-Scope Agent Instructions

Read and follow:

- `PROJECT_GUIDE.md`
- `RESOURCE_SCHEMAS.md` when the task touches collected data, topology, paths or risk analysis.

## Working rules

- Keep the current task narrowly scoped.
- Treat Docker as the production runtime boundary: new runtime features must work inside the `environment-web` container.
- Do not introduce host-only paths, host cron dependencies, or required host-global packages unless explicitly requested.
- Persistent generated/runtime data must use the configured container volume rather than relying on the disposable container layer.
- Do not scan the whole repository by default.
- Prefer relevant source files, small fixtures and targeted tests.
- Do not read full production snapshots or full `topology.json` unless necessary.
- Do not modify collectors unless the task requires collection changes.
- Preserve topology evidence and confidence semantics.
- Never invent relationships to make a path complete.
- Treat `EXACT`, `INFERRED`, `AMBIGUOUS` and `UNKNOWN` distinctly.
- Prefer Endpoint (`IP + Port + Protocol`) over IP-only correlation when available.
- Return/retain unresolved paths instead of fabricating a match.
- Avoid unrelated refactors.

For implementation tasks:

1. inspect the relevant files;
2. give a short plan;
3. implement;
4. run targeted tests;
5. summarize files changed, tests run and remaining gaps.
