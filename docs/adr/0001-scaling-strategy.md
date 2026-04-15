# ADR 0001: Scaling Strategy, Platform Direction, and Technology Options

- Status: Accepted
- Date: 2026-04-15

## Context

`feedr` is currently a compact FastAPI application built in a single `main.py` module with Jinja2 templates, SQLite storage, and an in-process background feed fetcher.

That design is appropriate for early product development because it keeps the app easy to ship, debug, and change. It also creates clear scaling pressure points:

- request handling and background feed fetching compete inside one process
- SQLite is a strong local and single-node choice, but it is a weak fit for horizontal scaling
- deployment is optimized for one container image, not multiple cooperating services
- the codebase is productive today, but architectural boundaries are implicit rather than explicit

This ADR records how the team should think about scaling the app and what technology choices to prefer when pressure increases.

## Decision

For the next stage of `feedr`, the project should:

1. Keep the existing Python and FastAPI stack in the near term.
2. Avoid a language rewrite until there is evidence that delivery speed, hiring, runtime cost, or operational limits justify it.
3. Plan the first meaningful scale step as a move from SQLite to PostgreSQL.
4. Split background feed ingestion from the web process before attempting horizontal scaling.
5. Prefer container hosting with managed PostgreSQL and simple worker deployment before considering Kubernetes.

In short: scale the current architecture incrementally before rewriting the stack.

## Why

- The current bottlenecks are architectural and operational more than language-driven.
- A database and process split yields much more scale headroom than a rewrite alone.
- FastAPI is already a good fit for the app's HTTP APIs, auth flows, and server-rendered UI.
- Python remains productive for feed ingestion, parsing, and application development.
- The team can defer high-cost rewrites until there is real product or traffic evidence.

## Options Considered

### Option A: Keep Python/FastAPI, SQLite, and one process

#### Pros

- Lowest complexity
- Fastest path for small-team iteration
- Cheap local development and deployment

#### Cons

- Weak concurrency story once write volume grows
- No clean path to multiple app instances sharing one database file
- Background fetching can impact request latency
- Operational visibility stays limited

#### Verdict

Good for local development and low-scale single-node deployment, but not the right medium-term target.

### Option B: Keep Python/FastAPI, move to PostgreSQL, split web and worker

#### Pros

- Solves the main scaling bottlenecks without a rewrite
- Preserves current development velocity
- Enables multiple web instances
- Gives better transactional safety, indexing, and operational tooling
- Makes feed ingestion independently deployable

#### Cons

- Adds infrastructure and migration work
- Requires introducing a proper migration workflow
- Needs some codebase reorganization for clearer service boundaries

#### Verdict

Recommended near- to medium-term direction.

### Option C: Rewrite backend in Go

#### Pros

- Strong concurrency model
- Lower memory footprint and fast binaries
- Good fit for network-heavy worker services

#### Cons

- Full rewrite cost is high
- Product iteration slows during transition
- Existing Python code and tests lose direct reuse
- Main pain points would still require database and worker separation decisions

#### Verdict

Reasonable only if operational efficiency becomes the dominant concern and the team is ready to pay the rewrite cost.

### Option D: Rewrite backend in TypeScript/Node.js

#### Pros

- Shared language across backend and any future frontend expansion
- Strong ecosystem for web applications
- Easier hiring in some teams

#### Cons

- Rewrite cost remains high
- Does not inherently solve current scaling constraints
- Feed parsing and ingestion would need a new operational baseline and library choices

#### Verdict

Not justified by the current problem set alone.

## Database Options

### SQLite

Use when:

- running locally
- deploying a single node
- keeping operations minimal matters more than concurrent scale

Concerns:

- limited write concurrency
- poor fit for multi-instance horizontal scale
- operational backup, migration, and analytics options are narrower

### PostgreSQL

Use when:

- running multiple web instances
- background workers and web processes need shared durable storage
- indexing, reporting, and operational maturity matter

Benefits:

- better concurrency and transactional behavior
- richer indexing options for article queries and friendships
- cleaner path for migrations and observability
- wide hosting support and team familiarity across most platforms

Decision:

PostgreSQL is the preferred next database once `feedr` moves beyond single-node scale.

## Hosting Options

### Single Container Host

Examples:

- Once-style deployment
- one VM running Docker

Best for:

- early deployment
- low traffic
- minimal ops overhead

Limitations:

- no clean horizontal scale with SQLite
- background and web workloads stay coupled

### Managed Container Platform with Managed Postgres

Examples:

- Fly.io
- Render
- Railway
- ECS/Fargate with RDS

Best for:

- small-to-medium scale
- web plus worker separation
- moderate ops maturity

Benefits:

- easy container deploys
- managed Postgres available
- workers and web services can scale independently

Decision:

Preferred next hosting shape.

### Kubernetes

Best for:

- larger team ownership
- many services
- custom networking and operational requirements

Limitations:

- too much operational complexity for the app's current scale and team shape

Decision:

Do not adopt Kubernetes as the next step.

## Target Evolution Path

### Phase 1: Current State

- single FastAPI app
- SQLite
- in-process background fetcher
- one container deployment

### Phase 2: First Scale Step

- move to PostgreSQL
- introduce proper schema migrations
- split feed fetching into a separate worker process
- keep FastAPI and server-rendered UI
- deploy web and worker separately on a managed container platform

### Phase 3: Further Scale if Needed

- add job queueing for feed refresh work
- introduce scheduling and retry policies
- partition or shard feed ingestion workloads if needed
- evaluate caching for expensive read paths
- consider extracting modules or services from `main.py`

### Phase 4: Rewrite Decision Point

Only consider a language rewrite if one or more of these become true:

- Python runtime cost becomes materially problematic
- worker concurrency requirements outgrow the current design even after architecture cleanup
- team composition strongly favors another ecosystem
- product velocity is being constrained by Python rather than codebase structure

## Consequences

### Positive

- preserves delivery speed now
- avoids premature rewrite risk
- gives a clear migration path toward higher scale
- aligns infrastructure changes with real bottlenecks

### Negative

- some technical debt remains in the single-module app structure
- the team will need a deliberate migration plan for PostgreSQL and worker separation
- documentation and architectural discipline become more important as scale grows

## Follow-Up Work

1. Introduce a real migration tool before moving to PostgreSQL.
2. Refactor feed ingestion so it can run as a standalone worker entry point.
3. Separate core domain areas in code: auth, feeds, articles, friendships, import/export.
4. Add more automated tests around feed ingestion, sharing, and OPML flows.
5. Define observability expectations for fetch failures, queue lag, and request latency.
