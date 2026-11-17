# Private migration

Git moves code; it does not move a user's state or permissions. Create separate backups for broker and Mac roles. Restore into newly initialized, stopped deployments. Do not merge a backup over an active data directory.

## Export

Stop the relevant worker, voice and helper processes, and broker if taking a coordinated backup. SQLite's backup API creates a consistent snapshot of each database even while open, but separate databases and files are not one shared transaction. Stopping all writers makes a whole-deployment snapshot meaningful.

```sh
python3.13 manage.py --home /absolute/private/deployment backup --output /absolute/private/backups/agent.zip
```

The backup contains the broker database if present and authoritative local memory database if present. It includes memory versions, evidence and audit records. Add `--include-health` only if you want local Health JSON in that private backup. Memory indexes are rebuilt after restore.

Excluded: credentials, Codex authentication home, persona/config files, raw PTT recordings, model weights, voice inbox/transient audio, IPC requests, task run logs and helper executables. Back up selected private config, persona and an explicitly retained speech dataset separately using your preferred encrypted backup tool. This project's ZIP is private (0600) but **unencrypted**. Do not upload it to GitHub or issue attachments.

## Restore

Install code and initialize the same role on the destination. Set its timezone deliberately. The destination generates fresh tokens; restoration does not overwrite them.

```sh
python3.13 manage.py --home /absolute/new/deployment restore --input /absolute/private/backups/agent.zip
```

Restoration validates the archive paths, file allowlist, hashes and SQLite integrity before publishing files. It refuses nonempty data destinations. Old queued/running jobs and sleep events are expired; old messages are superseded; the relay cache is cleared. Completed conversation history and memory remain. Old audio is never replayed as part of migration. The operation is not a transaction across filesystem moves; if a disk failure interrupts final publication, discard that new destination and retry with a fresh one.

Then configure new service addresses and credentials, restore your private persona, reacquire your own Codex login, and reauthorize native Apple tools. Rebuild iPhone shortcuts for the new endpoint/token. Install model environments separately and set their absolute paths. External model-volume UUID checks must match the new disk or be omitted for internal storage.

Run `doctor`, install the release, then activate services. A restored SQLite database may contain historical service metadata and user content: review the new private server config, especially `profile`, `memory_authority`, proactive settings and wind-down duration, before startup. Never use a code rollback to undo a database migration.

## Existing pilot deployment

The pre-extraction pilot is deliberately not auto-imported or restarted by this repository. Its config, prompt, logs and recordings must not enter this source tree. An operator can create an offline staging deployment outside the repository, copy only the desired database/health paths into `data/`, and use this backup tool against that staging deployment. First make source backups and compare memory counts/versions on both sides. Translating the old private config and GPT-SoVITS paths is still a manual cutover task; no automatic production migration has been claimed or performed.
