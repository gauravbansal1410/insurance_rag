# n8n workflows

Hand-authored workflow JSON for this project's n8n automation, meant to be imported via n8n's UI (not built through n8n's API — no API key is configured for this instance; generating one requires the UI, and this project hasn't needed n8n API access for anything else). See `docs/query_architecture.md`'s "Runtime orchestration" section for the design this implements: n8n stays a thin layer (chat trigger, relay to the Python service), never reimplementing `query/`'s already-tested step 3-8 logic as native nodes.

## `insurance_rag_chat.json`

Three nodes: **Chat Trigger** (n8n's built-in chat UI, shows a static greeting asking for age *before* the workflow ever runs — the workflow itself only ever sees the user's actual answers, never a `null` "start" message, since n8n's Chat Trigger has no way to speak first) → **HTTP Request** (`POST /chat` on the Python service, forwarding `sessionId`/`chatInput` as `session_id`/`message`) → **Set** (maps the service's `reply` field to `output`, which n8n's chat UI expects to display).

## Service deployment (done, 2026-08-01)

`service/` runs on the Oracle VM as a systemd **user** service
(`~/.config/systemd/user/insurance-rag-service.service`, `python3 -m
uvicorn main:app`), enabled + started (`systemctl --user enable --now`)
so it survives both a logout (via the `loginctl enable-linger ubuntu`
set up during the precompute-job incident, `docs/infra-baseline.md`)
and a reboot, with `Restart=on-failure` if it crashes.

**Bound to `172.17.0.1:8000` specifically — not `0.0.0.0`, not
`127.0.0.1`.** `172.17.0.1` is the Docker bridge gateway IP (confirmed
via `docker network inspect bridge`) — reachable from containers on
that bridge (n8n) and from the host itself, but *not* from the VM's
public internet-facing interface, since this service has zero
authentication and `0.0.0.0` would have exposed it publicly.

**Real blocker found and fixed, not just a networking guess:** the
host's `iptables` `INPUT` chain only allowed ports 22/80/443 plus
established connections, rejecting everything else (including
container-to-host traffic) with `icmp-host-prohibited` — this is *not*
Docker networking, it's the host firewall. Fixed with one narrowly
scoped rule, inserted before the existing REJECT: `sudo iptables -I
INPUT 7 -i docker0 -p tcp --dport 8000 -j ACCEPT` (only accepts port
8000 from the `docker0` bridge interface — not from the public
internet), verified SSH access was unaffected on a fresh connection
immediately after, then persisted via `sudo netfilter-persistent save`
(rules are managed by `netfilter-persistent`, stored in
`/etc/iptables/rules.v4` on this VM).

Verified end-to-end: `docker exec n8n wget -qO- http://172.17.0.1:8000/health`
returns `{"status":"ok","policies_loaded":7}` from inside the n8n
container itself.

**Import:** n8n UI → Workflows → Import from File → select this JSON. The HTTP Request node's URL (`http://172.17.0.1:8000/chat`) is the real, verified address.

**Still unverified (this session's best understanding, not confirmed against the live n8n UI):**
- Node type strings/versions (`@n8n/n8n-nodes-langchain.chatTrigger` v1.1, `n8n-nodes-base.httpRequest` v4.2, `n8n-nodes-base.set` v3.4) — n8n usually auto-migrates minor version mismatches on import, but if the import fails or a node shows a warning, that's the first thing to check.
- The `output` field name convention (what n8n's plain, non-AI-Agent chat workflow needs the last node to produce) — verify the chat window actually displays the reply after importing, and adjust the Set node's output field name if it doesn't.
