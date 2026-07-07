# Infra & environment baseline (reusable across projects)

## Purpose
Not Insurance-RAG-specific. This is the general infrastructure and environment context that applies to every project built on this setup. Keep it as a separate file from any single project's requirements doc, and update it in one place - ideally `master-context.md` on GitHub - rather than copy-pasting a fresh snapshot into every new Claude Project.

## Oracle Cloud VM (primary automation backend)
- Oracle Cloud Always Free VM, Ubuntu, Mumbai region, ARM (Ampere)
- Confirmed specs: Ampere shape, 11Gi total RAM, 10Gi available at idle (verified 2026-07-07)
- n8n self-hosted via Docker, exposed via DuckDNS domain: `gaurav-n8n.duckdns.org`
- nginx reverse proxy with Let's Encrypt SSL - HTTPS fully configured
- SSH access via private key at `~/Desktop/claude/personal/`
- n8n version: 2.23.3
- Docker run command:
  ```
  docker run -d --name n8n --restart unless-stopped --env-file ~/n8n.env -p 5678:5678 -v n8n_data:/home/node/.n8n n8nio/n8n
  ```
- Env var changes require the full stop-remove-run sequence, not a restart:
  ```
  docker stop n8n && docker rm n8n && docker run -d --name n8n --restart unless-stopped --env-file ~/n8n.env -p 5678:5678 -v n8n_data:/home/node/.n8n n8nio/n8n
  ```
- `N8N_BLOCK_ENV_ACCESS_IN_NODE=false` required in `~/n8n.env` for `$env` access inside nodes (n8n's Variables UI is a paid feature; this env-file approach is the free-tier workaround)
- Containers created without a restart policy will not survive a VM reboot and require manual `docker start <name>` - always set `--restart unless-stopped` on new containers on this VM.

## Secondary infra (not actively used)
- GCP e2-micro VM (Iowa region) - backup, currently stopped
- Local Mac: Docker Desktop, Ollama (llama3), n8n startable locally via `docker start -ai n8n`

## Existing n8n workflows on the Oracle VM (so new work doesn't collide with these names)
- `interview_bot` - PM design interview question generator
- `interview_bot_debrief` - writes debrief history to GitHub, sends email digest
- `linkedIn-job-scrapper_v4` - daily job scraping + scoring digest, 8am schedule

## n8n mechanics worth knowing before building more on this VM
- HTTP Request node URL/body fields need Expression mode (the fx toggle) for `$env` and `$json` to evaluate - Fixed mode sends the literal `{{ }}` text and fails silently
- Downstream nodes lose direct `$json` access to earlier nodes - use `$('NodeName').first().json`, or flatten needed fields with a Code node early in the chain
- GitHub Contents API responses are array-wrapped: use `$input.first().json.content`, not `[0].content`
- GitHub PUT calls need a freshly fetched SHA at write time, or the write fails
- Test webhooks expire after one use - re-arm by clicking Execute before each test
- For per-item rate limiting on API calls: Loop Over Items (batch size 1) plus a Wait node inside the loop - batch settings on an LLM Chain node alone don't reliably throttle
- `gemini-2.0-flash-lite` was shut down June 1, 2026 - if an old workflow references it and fails, that's why, not a quota issue. Current free-tier alias is `gemini-flash-lite-latest` (verify at build time, these names shift often)
- Gmail OAuth must be in Production mode on Google Cloud to avoid 7-day token expiry, which requires an HTTPS redirect URI (already solved via nginx)
- Adding new OAuth scopes requires reinstalling the app and manually updating the token in n8n
- If n8n autosave/publish throws unexplained errors, check for a stale browser tab holding a collaboration lock - fully quitting and reopening the browser usually fixes it

## GitHub
- Main repo: `github.com/gauravbansal1410/learning-ai-agents` - holds interview-prep and job-search content. Committing via github.com in the browser only, **no git clone on the work laptop.** Any automation that writes to this repo must use the Contents API (PUT with base64-encoded content and the current file SHA), never `git push`.
- **Exception, scoped per-repo, not a change to the default:** repos with no sensitive content (e.g. `insurance_rag`) may be cloned locally, using a fine-grained PAT scoped to that repo only and a local (non-global) git identity - never a broad token, never the global git config. Before treating any new repo as clone-safe, actually check whether it could contain or accumulate sensitive content, don't assume by topic alone.
- **Reminder: commit workflow JSON plus a dated progress log (`YYYYMMDD-progress.md`) to GitHub at the end of every build or learning session.**

## Claude accounts
- `claude-p` - personal, Pro plan, `gauravbansal1410@gmail.com`
- `claude-w` - work, Team plan, LINE MAN Wongnai - never used for personal projects

## Claude Code vs claude.ai Projects
Not the same system, no sync between them, despite both using the word "project." Claude Code's memory (`CLAUDE.md` plus auto memory) is local to your machine, scoped per working directory or git repo. A claude.ai Project's knowledge and memory live in Anthropic's cloud, tied to your account, accessed from the browser. Context given to one is invisible to the other. The fix isn't a setting, it's discipline: keep canonical context in committed doc files (like this one), reference them from a short `CLAUDE.md` for Claude Code, and separately upload the same files as Project knowledge in the browser. Update both copies when either changes.

---
Attach this file to every new project going forward. When the VM changes, n8n gets upgraded, or a new workflow gets added, update it here (or in `master-context.md` on GitHub, which this is meant to mirror) once - not by re-describing the setup from memory at the start of each new project.
