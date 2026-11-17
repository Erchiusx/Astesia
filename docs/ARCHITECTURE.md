# Architecture and boundaries

The Linux broker accepts authenticated questions, leases jobs to an outbound Mac worker, stores results and queues voice delivery. The Mac voice process maintains one Zello connection, decodes PTT audio, runs a resident ASR child, submits text and sends synthesized replies. Broker restart recovery is lease-based; delivery retries are not an exactly-once guarantee.

Code in a versioned release is separate from `config/`, `data/`, `logs/` and the isolated `codex-home/`. Python runtime paths use `PERSONAL_AGENT_CONFIG_DIR`, `PERSONAL_AGENT_DATA_DIR` and `PERSONAL_AGENT_TIMEZONE`. Native helper plists pass matching data paths/timezone. Memory lives in `data/workspace/memory`; authoritative Health snapshots live in `data/wellness` outside the agent's writable workspace.

The worker passes the current request and a bounded calendar preview as user data. Persona and task rules use developer instructions. Persistent interactive sessions provide conversational continuity; memory tools query and maintain the local database on demand. No memory index or synthetic history list is injected in tool mode. PIM changes require current interactive task context, exact request evidence, stable IDs, idempotent request IDs and optimistic version checks. Background briefings are read-only by policy and mutation guards. These guards are not a substitute for OS access controls or protection against a malicious process running as the same desktop user.

The broker has separate client/worker tokens, neither scoped to an individual iPhone. The protocol's `memory_authority=mac` keeps durable agent memory on the Mac; broker conversation history and temporary Health relay still contain private data. Do not treat a public-source project as a public-data service. Health relay ACK removes its live row, not every old SQLite page or backup copy.

Sleep events have deadlines and daily deduplication. Late events expire rather than replaying a stale bedtime reminder. Hourly checks and the Singapore-specific weather adapter are optional. Defaults are UTC, no user location, no proactive hourly/weather jobs, no PIM writes enabled, no Health export ingestion by the worker, no voice connection and no retained PTT training dataset.

## Known gaps

- Single-user design; no multi-tenant account isolation or web administration.
- Full fresh Linux + Mac + iPhone deployment and real model inference have not been validated for this extraction.
- Docker configuration has not been run against a fresh host; permissions for bind mounts remain operator-managed.
- macOS privacy grants, iPhone automations, TLS and account provisioning remain interactive/manual steps.
- The old experimental native WebRTC/Whisper alternatives are outside the supported release; Qwen3-ASR is the fixed supported input engine.
- ASR direct dependencies are pinned, but full transitive and GPT-SoVITS environment locks still need a reproducible model-stack release.
- Config/schema migrations are not yet a general versioned migration framework. Back up before upgrades; code rollback alone cannot roll back state.
- Existing private deployments require a deliberate cutover; packaging does not modify them.

The default tool-based interaction and compatibility boundaries are documented in [TOOL-BASED-AGENT.md](TOOL-BASED-AGENT.md).
