# Deployment

Use a separate private deployment directory for each role. The examples use `$HOME/.local/share/personal-agent-oss`. Choose your own IANA timezone; UTC is the safe default. Never use the source repository as the private directory.

## Linux broker: native service

Install Python 3.11+ and clone this repository. Initialize locally on the server:

```sh
python3 manage.py --home "$HOME/.local/share/personal-agent-oss" init --role broker --timezone UTC
python3 manage.py --home "$HOME/.local/share/personal-agent-oss" doctor
python3 manage.py --home "$HOME/.local/share/personal-agent-oss" install
```

`init` generates separate random client and worker tokens in private config files (0600). Set `config/client-setup.json` to your actual HTTPS endpoint. Copy only the worker token to the Mac worker config using a secure channel. Retain the client setup privately for iPhone workflow generation. No credentials are printed by these commands.

`install` copies all runtime modules into a versioned release, points `current` at it, and renders service definitions. It does not activate them. With a Linux user systemd session:

```sh
mkdir -p "$HOME/.config/systemd/user"
cp "$HOME/.local/share/personal-agent-oss/services/personal-agent-oss.service" "$HOME/.config/systemd/user/"
systemctl --user daemon-reload
systemctl --user enable --now personal-agent-oss
systemctl --user status personal-agent-oss
```

A user service needs a user manager that stays running after logout (configure lingering if appropriate), or adapt the generated unit to a dedicated system account and system service. Run it as an unprivileged account. This packaging does not change your server account or nginx automatically.

Expose the loopback broker through an HTTPS reverse proxy. `deploy/nginx.conf.example` is an include for an existing TLS server. Check nginx configuration before reloading. Keep port 18864 bound to loopback. Authenticated `/health` is service status, not Apple Health data.

In private `server-config.json`, set `profile` for your own context. `memory_authority` defaults to `mac`. Hourly proactive jobs and Singapore weather checks default to off; enable explicitly using `hourly_enabled` and `singapore_weather_enabled`. Configure `routine_wind_down_minutes` only from the actual iPhone sleep plan, or leave null. Restart the broker after config changes. Prompt tone does not enable a scheduler by itself.

An optional `routine_daily_plan` object in private server config can bypass iPhone signals: set `enabled=true`, local `wake` and `bedtime` strings in `HH:MM` format, and an IANA `timezone`. The broker schedules wake plus 30 minutes and bedtime minus 5 minutes. It seeds only future events, so enabling after bedtime does not replay tonight's reminder. This is a manually maintained daily plan, not automatic Apple Health synchronization. When editing or disabling an existing plan, stop the broker and reconcile its already-pending daily-plan events before restarting; they are not automatically rewritten by the config loader.

## Linux broker: optional container

Use either native systemd or Compose, not both on the same port/database. Initialize private broker config first. In that config set `host` to `0.0.0.0` (inside the container only) and `database` to `/data/state.sqlite3`. Bind mounts must be accessible to container UID 10001; keep the data directory 0700 and config readable only by the service identity. This ownership setup is manual. Native `doctor` assumes native paths, so it is not the container config validator.

```sh
AGENT_CONFIG_DIR=/absolute/private/config AGENT_DATA_DIR=/absolute/private/data AGENT_TIMEZONE=UTC \
  docker compose -f deploy/compose.yaml up -d --build
```

Compose publishes only on host loopback. It does not provision a VM, DNS, TLS or firewall. The Compose definition is provided but was not executed against a fresh Docker host during this extraction.

## Mac worker

Install Python 3.13 and your supported Codex CLI separately. Then:

```sh
python3.13 manage.py --home "$HOME/.local/share/personal-agent-oss" init --role mac --timezone UTC
```

Edit private `worker-config.json`: HTTPS `base_url`, worker token, absolute Codex executable. Authenticate Codex separately into the generated isolated `codex-home`; do not copy or publish authentication files in the source tree. Existing deployments may rely on CLI features that differ by build. This code retains the working CLI command shape; verify your installed `codex exec --help` supports its flags. `interactive_fast=false` disables the optional fast settings. Authentication and model access are not supplied by this project.

The private `assistant-instructions.txt` is editable and read for each task. Changing the public default does not overwrite it on upgrade. Keep memory in `data/workspace/memory`; the agent reads the index then individual records. Do not enable writes by broadening the worker sandbox beyond the existing task workspace.

```sh
python3.13 manage.py --home "$HOME/.local/share/personal-agent-oss" doctor
python3.13 manage.py --home "$HOME/.local/share/personal-agent-oss" install
launchctl bootstrap "gui/$(id -u)" "$HOME/.local/share/personal-agent-oss/services/org.personalagent.oss.worker.plist"
launchctl print "gui/$(id -u)/org.personalagent.oss.worker"
```

Generated plists remain in the deployment directory. `bootstrap` starts them for the current login; for automatic loading at future logins, copy selected plists into `~/Library/LaunchAgents`. Use one active worker and one voice connection for a given deployment. Logs are private in `logs/`; individual task runs may contain user context and are never publication artifacts.

## Native Apple tools

Build with Xcode Command Line Tools; these commands create separate OSS apps and do not overwrite a legacy deployment's apps:

```sh
python3.13 src/build_calendar_bridge.py
python3.13 src/build_pim_bridge.py
```

Set `pim_tools_enabled=true` if requested. Rerun `install` after building apps. It renders helper plists when the apps exist. Start each helper via its generated plist so it receives this deployment's data path and timezone:

```sh
launchctl bootstrap "gui/$(id -u)" "$HOME/.local/share/personal-agent-oss/services/org.personalagent.oss.calendar.plist"
launchctl bootstrap "gui/$(id -u)" "$HOME/.local/share/personal-agent-oss/services/org.personalagent.oss.tools.plist"
```

Authorize Calendar, Reminders and Notes through macOS when prompted. If a new Mac or new signature needs authorization, verify the helper's actual API status. Permissions are not transferable in a backup. Do not run these helpers by double-clicking for a custom deployment: without the generated environment they use their default path. Rebuilding ad-hoc signed apps may require permission again; the tools builder refuses to overwrite an existing app automatically.

## Upgrade and rollback

Back up private state before upgrades; stop services for a coordinated snapshot. Update source, run tests, then run `install` again. It records the previous release and changes only the `current` code pointer. Restart the applicable services explicitly:

```sh
launchctl kickstart -k "gui/$(id -u)/org.personalagent.oss.worker"
# Linux alternative:
systemctl --user restart personal-agent-oss
```

For code rollback:

```sh
python3.13 manage.py --home "$HOME/.local/share/personal-agent-oss" rollback
```

Then restart services. Config, models and database schema are not rolled back by that command. If a future release changes the schema incompatibly, stop services and restore the pre-upgrade backup into a fresh deployment instead. Release directories are retained until the operator removes them.

## Optional resumable task sessions

`persist_interactive_sessions=true` (enabled in new deployments) retains Codex rollout files for future interactive tasks in the isolated Codex home; background briefings remain ephemeral. The worker writes `data/last-interactive-session.json` after a successful saved task. It cannot recover rollout files from earlier ephemeral runs. Resuming a saved task in a terminal does not submit that terminal conversation back into the broker history. Treat rollout files as private user data and manage retention separately.

New deployments use [tool-based interaction](TOOL-BASED-AGENT.md) by default. Existing deployments should opt in deliberately using a compatible broker, updated voice process, new instructions and the corresponding config flags.
