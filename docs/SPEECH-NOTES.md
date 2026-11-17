# Recent speech in Notes

Optional macOS background mirroring writes the most recent three `speak(text)` messages into one dedicated Notes entry. The two morning/evening routine phases are excluded before the retention limit is applied; hourly/weather speech is eligible. Each `speak` call counts as one entry, so a preliminary update and a later answer count separately. Entries show their local date/time, newest first.

The collector reads a worker-authenticated, read-only broker endpoint every few seconds. The already-authorized native PIM host renders the desired state through Notes APIs. Model instructions and tool-call arguments are unchanged. Queueing speech, ASR and TTS do not wait for Notes. Offline failures preserve the last good desired/note state and retry later.

## Setup

First update the broker and build the native PIM host containing `speech_notes_bridge.inc.m`. Updating an existing signed app must stop the real native process, not just a one-shot `/usr/bin/open` launcher. Permission refresh may be required by macOS; do not alter privacy databases.

Copy `config/speech-notes.example.json` to the private deployment's `data/speech-notes/config.json` (0600, directory 0700). Choose a title, IANA timezone, a fresh UUID for `mirror_id`, and optionally a target Notes folder ID. Set `enabled=true` only when configured. Blank note ID plus `allow_create=true` provisions a new dedicated note. If the title already exists, the host refuses automatic replacement: explicitly bind the intended note ID instead. Prefer an iCloud folder for phone sync. Local Notes identifiers may need rebinding on another Mac.

`keep` supports 1–3; default 3. `after=0` initializes from the recent eligible messages already retained by the broker; a Unix timestamp can restrict the mirror to later speech. The policy, bound note ID, desired content and status live outside the agent's writable workspace. Do not commit those files or note contents.

Rerun `manage.py install` after setup. An enabled policy renders a `org.personalagent.oss.speech-notes.plist` collector and the native tools plist if the app exists. Activate those services explicitly as described in DEPLOYMENT.md. No voice-worker restart is needed.

## Retention and safety

Only the dedicated note's managed body is replaced. Old entries are removed from that body; the broker conversation history and agent memory are unchanged. The note should be reserved for automatic content. If it is renamed, moved into Recently Deleted, password protected, has attachments, or cannot be verified safely, the host pauses that write and records an error instead of modifying some other note. Deleting the target never causes a title-based replacement to be created automatically after binding.

The host records `status.json` after readback, including whether it succeeded, the bound ID, rendered version and count. `desired.json` is the latest proposed state. Disable the policy to stop updates; the current note is left intact. Notes/iCloud synchronization to the phone is managed by Apple and was not independently timed by this feature's tests.
