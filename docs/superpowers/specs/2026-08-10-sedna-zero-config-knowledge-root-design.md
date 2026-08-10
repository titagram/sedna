# Sedna Zero-Configuration Knowledge Root Design

**Date:** 2026-08-10

**Status:** Approved for implementation

**Scope:** Portable, profile-aware resolution of Sedna's canonical knowledge root when Sedna is
used as a Hades plugin

## Context

Sedna's Hades-facing tools currently require either an explicit `knowledge_root` argument or a
`sedna_knowledge_root` value supplied by the plugin context. This is safe and testable, but it
prevents a newly installed plugin from working without machine-specific setup.

The plugin must instead choose a safe local default dynamically. The repository must never
contain an absolute path from the development machine, and an installation on another macOS,
Linux, or Windows host must derive its root from that host's active Hades environment.

Hades already owns the definition of its active home directory. Its resolver accounts for
context-local overrides, `HERMES_HOME`, the compatibility `HADES_HOME` variable, and the
platform-native default. Sedna should reuse that policy while preserving its ability to run in
a standalone Python environment where Hades is not installed.

## Goals

1. Make the four Sedna knowledge tools usable on a fresh Hades installation without an explicit
   `knowledge_root`.
2. Resolve the default independently on every machine and active Hades context.
3. Preserve explicit and context-level overrides for tests, advanced installations, and data
   migration.
4. Avoid cross-profile knowledge leakage caused by independently reimplementing only part of
   Hades home resolution.
5. Create storage only when a knowledge operation actually opens the runtime.
6. Retain the existing descriptor-bound creation, overlap checks, journaling, index barriers,
   and fail-closed behavior.
7. Keep Sedna importable and testable without Hades installed.

## Non-goals

- Automatically copying or deleting an existing knowledge root.
- Automatically adopting the pilot root at `~/.hades/knowledge/sedna-machines`.
- Adding a plugin installer, interactive first-run wizard, or global configuration mutation.
- Changing the canonical storage schemas, ingestion semantics, retrieval ranking, or LLM
  provider contract.
- Making the current working directory, source repository, or raw-document directory a storage
  fallback.

## Decision

The effective knowledge root is resolved on demand with this precedence:

1. non-empty `knowledge_root` supplied in the individual tool request;
2. non-empty `ctx.sedna_knowledge_root` supplied by the host context;
3. the active Hades home returned by Hades' official home resolver, followed by
   `knowledge/sedna`;
4. only when the Hades resolver cannot be imported, a standalone platform-native Hades home,
   followed by `knowledge/sedna`.

The normal effective path is therefore:

```text
<active Hades home>/knowledge/sedna
```

Typical examples are:

```text
macOS/Linux:  ~/.hades/knowledge/sedna
Windows:      %LOCALAPPDATA%\hades\knowledge\sedna
```

`HERMES_HOME`, `HADES_HOME`, and context-local Hades overrides remain the responsibility of the
Hades resolver. Sedna does not duplicate their precedence when the resolver is available.

## Resolution Architecture

### Pure selection

Root selection validates and returns a `Path`; it performs no directory creation and opens no
database. Plugin registration and module import therefore remain side-effect free.

Explicit and context values keep the existing bounded string/path validation. Blank values,
overlong values, NUL-containing values, and unsupported objects are rejected rather than
treated as defaults.

### Host-aware lazy resolution

The Hades home resolver is imported inside the default-resolution function, not at Sedna module
import time. This avoids a hard installation dependency for standalone Sedna use.

Only absence of the Hades resolver permits the standalone fallback. If the resolver exists but
raises, returns an invalid value, or cannot establish the active home, Sedna returns a stable
safe plugin error. It must not silently select another profile or the process user's default
home.

The host integration is isolated behind a small internal resolver function so a future public
home-directory property on `PluginContext` can replace the lazy import without changing tool
contracts.

### Standalone fallback

When Hades is genuinely unavailable, Sedna computes the same platform-native base convention:

- POSIX: `Path.home() / ".hades"`;
- Windows: `%LOCALAPPDATA%/hades`, with `~/AppData/Local/hades` only when `LOCALAPPDATA` is absent.

This fallback is for standalone execution and tests. It does not try to reproduce Hades profile
or context-local behavior.

### Runtime ownership

The selected path is passed unchanged to `HadesKnowledgeRuntime.create`. Existing runtime and
repository code remains responsible for descriptor-relative creation, symlink resistance,
identity retention, source/root overlap rejection, canonical recovery, and SQLite lifecycle.

The first operation that needs a runtime may create the default root. Merely importing Sedna,
registering the plugin, validating an invalid target, or rejecting an unauthorized query must
not create it.

## Tool Behavior

All four knowledge tools use the same resolver:

- `sedna_learn_documents`;
- `sedna_retrieve_knowledge`;
- `sedna_get_knowledge_artifact`;
- `sedna_maintain_knowledge`.

Calls that already pass `knowledge_root` are backward compatible. Hosts that already expose
`ctx.sedna_knowledge_root` are also backward compatible. Omitting both now selects the dynamic
default instead of returning `knowledge_root_required`.

An invalid explicit or context override remains an input error. Failure of an available Hades
home resolver is reported using a fixed, non-sensitive runtime/configuration error; raw host
exceptions and local filesystem details are not returned to the LLM.

Pre-backend rejection remains pre-backend: malformed targets and unauthorized scopes must not
open the knowledge runtime merely because a default is now available.

## Isolation and Portability Invariants

1. No source or configuration file contains a developer-machine absolute default.
2. Two different resolved Hades homes produce two different Sedna roots.
3. An explicit override never mutates the host default.
4. A context override never leaks into another context through module-global caching.
5. Default resolution is performed per operation; the result is not cached globally.
6. The knowledge root can never be the selected learning source, contain it, or be contained by
   it under the existing confinement rules.
7. Resolver failure never falls through to the repository, source tree, current directory, or a
   different Hades profile.

## Pilot Data and Migration

The existing machine-writeup pilot remains at:

```text
/Users/gabriele/.hades/knowledge/sedna-machines
```

The new default on that same development machine will normally be:

```text
/Users/gabriele/.hades/knowledge/sedna
```

This change performs no implicit migration. The pilot remains available for explicit use. A
separate, intentional operation may later re-ingest the source corpus into the default root or
copy canonical data through a versioned migration procedure. Silent merging is excluded because
it could combine namespaces, stale indexes, or data created under different compiler versions.

## Documentation Changes

The README and Hades LLM tool guide will document:

- that `knowledge_root` is optional;
- the exact precedence order;
- the `<active Hades home>/knowledge/sedna` default;
- environment/platform examples;
- per-operation dynamic resolution and profile isolation;
- how to retain or explicitly select an existing pilot/custom root;
- that tool-specific skills remain outside Sedna's strategic knowledge store.

The plugin manifest does not need a machine-specific configuration entry. Adding such an entry
would reintroduce setup work and duplicate the host's existing home policy.

## Verification Strategy

### Resolver unit tests

- an explicit request root wins over every other source;
- a context root wins over the dynamic default;
- Hades' resolved home is used when neither override exists;
- two simulated context homes produce distinct roots without process-global leakage;
- resolver import absence activates the standalone fallback;
- resolver execution failure fails closed and does not activate the fallback;
- POSIX and Windows standalone paths are derived without hardcoded local paths;
- blank, NUL-containing, overlong, and invalid override/default values are rejected.

### Plugin integration tests

- registration creates no directory;
- a valid first knowledge operation creates and uses the dynamic root;
- a second operation reuses canonical state and remains idempotent;
- malformed-target and unauthorized-scope calls do not create the root;
- explicit/context overrides retain their current behavior;
- learning-source overlap is rejected before canonical writes;
- separate simulated installations do not share canonical or indexed state.

### Regression gates

- existing plugin, learning, runtime, repository, retrieval, and migration tests remain green;
- the complete test suite and Ruff checks pass;
- repository search confirms that the pilot's absolute path appears only in migration/history
  documentation where intentionally cited, never as executable configuration or a default.

## Rollout

1. Add the isolated root-resolution helper and its tests.
2. Switch the four plugin tools to the new resolver without altering their explicit input
   contracts.
3. Add zero-configuration integration and isolation regressions.
4. Update README and the Hades LLM guide.
5. Run focused and full verification.
6. Keep the pilot root untouched; evaluate explicit migration only as a separate decision.

## Acceptance Criteria

The feature is complete when a fresh Hades installation can invoke a Sedna knowledge tool
without passing `knowledge_root`, the tool securely uses
`<active Hades home>/knowledge/sedna`, separate homes remain isolated, no registration or
pre-backend rejection creates storage, all existing overrides remain compatible, documentation
matches the implementation, and the full verification suite passes.
