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
- **`~/n8n.env` contains real secrets beyond n8n's own config** (`APIFY_API_TOKEN`, `GITHUB_PAT`, etc., used by n8n workflows via `$env`) — confirmed the hard way 2026-08-01 that a keyword-based grep filter (excluding lines matching "password/secret/key") is NOT reliable for redacting this file before displaying it, since these variable names don't contain those words and got shown in full. If this file's contents ever need inspecting again, show variable **names only**, never values (e.g. `sed 's/=.*/=.../'` or `cut -d= -f1`), rather than trying to denylist which lines look sensitive.
- Containers created without a restart policy will not survive a VM reboot and require manual `docker start <name>` - always set `--restart unless-stopped` on new containers on this VM.
- Qdrant (vector DB, insurance_rag project) self-hosted via Docker, confirmed running 2026-07-26:
  ```
  docker run -d --name qdrant --restart unless-stopped -p 127.0.0.1:6333:6333 -v qdrant_data:/qdrant/storage qdrant/qdrant
  ```
  Bound to `127.0.0.1:6333`, not `0.0.0.0:6333` - confirmed via `docker ps` and cross-checked from an external host (`curl` from outside the VM times out; `curl localhost:6333/healthz` from inside the VM succeeds). Qdrant has no built-in auth on the free/OSS image, so exposing it on all interfaces would leave the vector store openly writable from the internet. Reachable only from processes running on the VM itself (e.g. n8n) - use SSH port-forwarding for any external admin access rather than rebinding to `0.0.0.0`.
  - **Local access (dashboard, ad-hoc queries):** `qdrant-tunnel` alias in `~/.zshrc` runs `ssh -i ~/.ssh/claude_code_oracle -L 6333:localhost:6333 ubuntu@68.233.108.134` - forwards local port 6333 to the VM's loopback-bound Qdrant. Run `qdrant-tunnel`, wait for the connection, then browse `http://localhost:6333/dashboard`. Close the terminal/tunnel when done - this is manual, on-demand access, not a standing connection.
  - **General rule for future services on this VM:** default any data-store or admin-surface container (no built-in auth, or auth you haven't verified) to `-p 127.0.0.1:<port>:<port>`, not a bare `-p <port>:<port>` (which binds `0.0.0.0`, all interfaces). n8n above binds `0.0.0.0:5678` but is fronted by the nginx/Let's Encrypt reverse proxy with its own auth - that's the exception that justifies it, not the default.

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
- **`insurance_rag` specifically:** the Oracle VM keeps its own local git clone of this repo (the query pipeline reads Layer 1/2 JSON from it at runtime, not GitHub's raw endpoint per query — see `docs/query_architecture.md`'s runtime data source note). End-of-session checklist for this repo also includes running `git pull` on that VM-side clone once local changes are pushed, so the two copies don't drift.
  - **Clone actually created 2026-07-31** (previously documented as the intended design here, but not yet actually done — confirmed missing when checked that day). Cloned at `~/insurance_rag` on the VM via plain `git clone https://github.com/gauravbansal1410/insurance_rag.git` — **no PAT or credential helper needed at all**, since this repo is public (confirmed via the GitHub API) and the VM only ever reads (`git pull`), never pushes. The credential-helper plan below this bullet was written assuming private-repo-style auth would be needed; that assumption was wrong for a public repo's read-only access — keep the credential-helper approach in mind only if this repo (or a future one used the same way) is ever made private.
  - Python deps (`google-genai`, `qdrant-client`, `voyageai`) installed via `python3 -m pip install ... --break-system-packages` (the VM didn't have `pip` at all initially — `sudo apt-get install -y python3-pip` first). `.env` (gitignored, never committed) copied over via `scp` directly, not typed into any command, then `chmod 600` — `QDRANT_URL=http://localhost:6333` works as-is on the VM since Qdrant runs locally there too, no SSH tunnel needed for VM-local scripts.
  - **Running a long admin/ingestion job (e.g. `chunking/precompute_rerank_scores.py`) on this VM, detached from your SSH session:** confirmed the hard way 2026-07-31 that plain `nohup command & disown` is NOT sufficient — a ~1.5 hour job with 7/8 tags done was silently killed when the SSH session ended, with zero error output, most likely `systemd-logind` tearing down the session's cgroup scope. Fix, both applied: `sudo loginctl enable-linger ubuntu` (lets the user's systemd instance persist past logout) and launching via `setsid nohup command > log 2>&1 < /dev/null & disown` (fully detaches into its own session, not just ignoring SIGHUP). Verify a job is still alive after disconnecting with a fresh `ssh ... "ps -o pid,etime,time -p <pid>"` — don't just trust that the launch command returned cleanly.
  - Non-interactive `git pull` on the VM needs a credential helper configured there (e.g. `git config credential.helper store`, or an SSH deploy key) using the same scoped fine-grained PAT already used for this repo's clone — never a token embedded in the remote URL, per `CLAUDE.md`'s key-handling rule. **Not yet needed in practice** (see above — this repo doesn't require it for the read-only clone/pull it currently does), but keep this approach in mind if that ever changes.
  - **`service/` (the FastAPI query service) deployed 2026-08-01** as a systemd **user** service, not another `nohup`/`setsid` job — this one needs to run indefinitely, not just survive one long batch run, so it gets proper `Restart=on-failure` and start-on-boot via `systemctl --user enable --now`. Unit file at `~/.config/systemd/user/insurance-rag-service.service` (`ExecStart=/usr/bin/python3 -m uvicorn main:app --host 172.17.0.1 --port 8000`, `EnvironmentFile=/home/ubuntu/insurance_rag/.env` — plain `KEY=value` lines are directly compatible with systemd's `EnvironmentFile` directive, no reformatting needed). Relies on the same `loginctl enable-linger ubuntu` set up for the precompute-job incident above.
    - **Bound to `172.17.0.1` (the Docker bridge gateway, confirmed via `docker network inspect bridge`), not `0.0.0.0` or `127.0.0.1`.** `0.0.0.0` would expose this zero-authentication service on the VM's public interface too; `127.0.0.1` is unreachable from n8n's own container (different network namespace). `172.17.0.1` is reachable from Docker containers on the default bridge and from the host itself, but not from the public internet.
    - **Real blocker, not a Docker networking issue:** n8n's container still couldn't reach `172.17.0.1:8000` ("Host is unreachable") even with correct bridge routing confirmed. Root cause was the host's own `iptables` `INPUT` chain, which only allows ports 22/80/443 plus established connections, rejecting everything else (including container-to-host traffic) with `icmp-host-prohibited`. Since a firewall mistake here could lock out SSH to this VM entirely, treated as a genuinely risky change: confirmed the persistence mechanism first (`netfilter-persistent`, rules in `/etc/iptables/rules.v4` — this VM does not use `ufw` directly, despite `ufw-*` chains being present), got explicit confirmation before touching anything, added exactly one narrowly-scoped rule (`sudo iptables -I INPUT 7 -i docker0 -p tcp --dport 8000 -j ACCEPT` — accepts port 8000 only from the `docker0` bridge interface, inserted before the existing REJECT rule), verified a **fresh** SSH connection still worked immediately after (not just the already-open session), verified n8n's container could reach the service (`docker exec n8n wget -qO- http://172.17.0.1:8000/health`), then persisted via `sudo netfilter-persistent save`.
    - **Redeploying new code:** `ssh ... "cd ~/insurance_rag && git pull && systemctl --user restart insurance-rag-service.service"`, then re-check `/health` — a restart takes a few seconds, don't assume it's up immediately.

## Claude accounts
- `claude-p` - personal, Pro plan, `gauravbansal1410@gmail.com`
- `claude-w` - work, Team plan, LINE MAN Wongnai - never used for personal projects

## Claude Code vs claude.ai Projects
Not the same system, no sync between them, despite both using the word "project." Claude Code's memory (`CLAUDE.md` plus auto memory) is local to your machine, scoped per working directory or git repo. A claude.ai Project's knowledge and memory live in Anthropic's cloud, tied to your account, accessed from the browser. Context given to one is invisible to the other. The fix isn't a setting, it's discipline: keep canonical context in committed doc files (like this one), reference them from a short `CLAUDE.md` for Claude Code, and separately upload the same files as Project knowledge in the browser. Update both copies when either changes.

---
Attach this file to every new project going forward. When the VM changes, n8n gets upgraded, or a new workflow gets added, update it here (or in `master-context.md` on GitHub, which this is meant to mirror) once - not by re-describing the setup from memory at the start of each new project.
