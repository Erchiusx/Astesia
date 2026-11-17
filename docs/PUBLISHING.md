# Publication

The source tree is intended for GitHub. It is not the runtime directory. No remote repository is configured by the extraction workflow.

Before first publication:

1. Review the MIT license and choose the repository name/visibility.
2. Run the public-tree guard and tests. Review `git diff --cached` and `git ls-files` before committing. The guard checks its explicit allowlist and common secret patterns; it is not an exhaustive secrets audit. Use an independent scanner over the full Git history before making a repository public.
3. Never add private config or persona, signed shortcuts, SQLite backups, logs, account sessions, voice reference assets, recordings, model weights, or vendored experimental environments.
4. Document what was actually tested in the release. Static CI and synthetic tests do not establish live iPhone, Zello, native permissions or model quality.
5. Create the repository and push only after the staged source review. If a real credential ever entered a published commit, revoke it; deleting the current file does not remove the old credential from history.

GitHub creation and push are intentionally separate from local packaging. Future issues, screenshots and test fixtures should use synthetic data.
