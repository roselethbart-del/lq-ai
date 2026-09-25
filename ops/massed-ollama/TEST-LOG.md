# Test log: LQ-AI on Massed Compute

One row per check. Never write the Tailscale key here. Only non-confidential test
material is used; no real contract documents.

| # | Date | What was checked | Command used | Result | Notes |
|---|---|---|---|---|---|
| 1.0 | 2026-09-25 | No local Ollama running; nothing on port 11434 | `docker compose ps`, `netstat -ano` | PASS | No Mode-2 `ollama` service; port 11434 free |
| 1.1 | 2026-09-25 | Image builds locally | `docker build -t lq-ai-massed-ollama:test .` | PASS | Ollama 0.34.4 + Tailscale v1.102.5 |
| 1.2 | 2026-09-25 | Container reaches READY (CPU, `smollm2:135m`) | `docker run -d --name lq-ai-ollama-test --env-file <key file outside repo> -e TS_HOSTNAME=lq-ai-ollama-test -e MODELS=smollm2:135m ...` | PASS | READY in ~75 s; "no GPU" warning shown as expected on a laptop |
| 1.3 | 2026-09-25 | Online on tailnet as `lq-ai-ollama-test` | `tailscale status` | PASS | |
| 1.4a | 2026-09-25 | `/api/tags` over HTTPS from the laptop | `curl https://lq-ai-ollama-test.tail6d9c1d.ts.net/api/tags` | **FAIL** | HTTPS and certificate fine, but Ollama answered 403. Cause: Ollama bound to 127.0.0.1 refuses requests addressed to any other host name. Fix: bind Ollama to 0.0.0.0 inside the container (still no published ports). Image rebuilt |
| 1.5 | 2026-09-25 | `docker stop` logs out and frees the name | `docker stop`, `tailscale status` | PASS | Device gone within seconds; restarted container got the same name (not `-1`) |
| 1.4b | 2026-09-25 | `/api/tags` over HTTPS (after fix) | same as 1.4a | PASS | `lq-ai-default:latest`, `nomic-embed-text:v1.5`, `smollm2:135m` |
| 1.6 | 2026-09-25 | Short prompt answers | `POST /api/generate` model `lq-ai-default` | PASS | Answer in 0.1 s (tiny model) |
| 1.7 | 2026-09-25 | Embedding works, size matches LQ-AI | `POST /api/embed` model `nomic-embed-text:v1.5` | PASS | 768 dimensions = `.env` `EMBEDDING_DIMENSION=768` |
| 1.8 | 2026-09-25 | NOT reachable on the laptop's localhost / published ports | `curl http://localhost:11434/api/tags`, `docker port` | PASS | No connection; no published ports |
| 1.9 | 2026-09-25 | Plain HTTP on the tailnet, port 11434 | `curl http://lq-ai-ollama-test.tail6d9c1d.ts.net:11434/api/tags` | NOTE | Answers 200. Tailnet-only and encrypted by WireGuard, not public. Can be limited to HTTPS (443) with a Tailscale access rule (README step A.4) |
| 1.10 | 2026-09-25 | Second `docker stop`: offline and removed | `docker stop`, `tailscale status` | PASS | Gone from the tailnet |
| 1.11 | 2026-09-25 | Cleanup | `docker rm`, `docker rmi` | PASS | Test container and image removed |
| 2.1 | 2026-09-25 | GitHub Actions build + publish | push of `local/massed-ollama`; run 36104849643 | PASS | Built in 1 min 59 s; `ghcr.io/roselethbart-del/lq-ai-massed-ollama:latest` |
| 2.2 | 2026-09-25 | Image is public (anonymous access) | anonymous `ghcr.io/token` + manifest request | PASS | HTTP 200 without login; public automatically because the fork is public |
| 2.3 | 2026-09-25 | Logged-out `docker pull` | `DOCKER_CONFIG=<empty> docker pull --platform linux/amd64 ...` | PASS | Digest `sha256:6b8684cc...`; amd64; contains the Test 1 fix (`OLLAMA_HOST=0.0.0.0:11434`). First try without `--platform` failed only because the laptop is ARM; Massed VMs are amd64 |

| 3.0 | 2026-09-25 | Massed pre-filled run command inspected | Massed UI | NOTE | Default is `docker run -d --runtime=nvidia --gpus all --shm-size 10g --network=host <image>`. Kept `--runtime=nvidia --gpus all --shm-size 10g`; **removed `--network=host`** (it would expose Ollama on the public IP). README updated |
| 3.1 | 2026-09-25 | First VM (64.247.196.143) | launch with template | **FAIL** | The `PASTE-TAILSCALE-KEY` placeholder was left in, so the VM couldn't join the tailnet (container stopped itself as designed). Public IP closed. VM replaced. Lesson added to the daily routine: check that the placeholder is gone |
| 3.2 | 2026-09-25 | Second VM (64.247.196.195) reaches READY | Massed UI launch | PASS | |
| 3.3 | 2026-09-25 | On tailnet as `lq-ai-ollama` (not `-1`) | `tailscale status` | PASS | |
| 3.4 | 2026-09-25 | Models listed from the laptop | `GET https://lq-ai-ollama.tail6d9c1d.ts.net/api/tags` | PASS | `qwen3.5:9b`, `lq-ai-default:latest`, `nomic-embed-text:v1.5` |
| 3.5 | 2026-09-25 | Runs on the GPU | `GET /api/ps` | PASS | 6.6 GB of 6.6 GB on the GPU (100%), context 32768 |
| 3.6 | 2026-09-25 | Timed short prompt | `POST /api/generate` model `lq-ai-default`, twice | PASS | 1st: 54 s total (31 s loading the model onto the GPU); 2nd: 0.5 s, ~103 tokens/s |
| 3.7 | 2026-09-25 | Embedding | `POST /api/embed` | PASS | 768 dimensions |
| 3.8 | 2026-09-25 | SECURITY: public IP must not answer (container running) | `curl -m 6 http://64.247.196.195:{11434,443,80}/api/tags`, `https://64.247.196.195/` | PASS | No answer on any port |
| 4.0 | 2026-09-25 | Found: the gateway only receives `OLLAMA_BASE_URL` from `.env`, and runs from a stored copy `/etc/lq-ai/gateway.yaml` (Docker volume `gateway-config`), not the local file | `docker compose config`, `gateway/entrypoint.sh` | NOTE | Plan adjusted: `massed-ollama` reads `OLLAMA_BASE_URL`; the edited `gateway.yaml` is copied into the stored copy. README section C updated |
| 4.1 | 2026-09-25 | Backups of `.env` and `gateway.yaml` | `cp` to `C:\Users\bartr\lq-ai-backups\` | PASS | Outside the repo; verified identical before editing |
| 4.2 | 2026-09-25 | Config change applied | `.env`: only `OLLAMA_BASE_URL` changed; `gateway.yaml`: `massed-ollama` (Tier 2), smart/fast/budget → `lq-ai-default`, embedding → `nomic-embed-text:v1.5`, `ollama-local` disabled | PASS | Validated with the gateway's own config loader + egress policy before going live |
| 4.3 | 2026-09-25 | Gateway recreated, healthy, no errors in log | `docker compose up -d --no-deps --force-recreate gateway` | PASS | |
| 4.4 | 2026-09-25 | Gateway container reaches the VM | `httpx.get(https://lq-ai-ollama.tail6d9c1d.ts.net/api/tags)` inside gateway | PASS | HTTP 200 |
| 4.5 | 2026-09-25 | Test chat + embedding through the gateway | `POST /v1/chat/completions` (smart), `POST /v1/embeddings` (embedding) | PASS | Tier 2; 768 dimensions; api, arq-worker, ingest-worker all expect 768 |
| 4.6 | 2026-09-25 | User chat in the LQ-AI web UI | manual | PASS | Routing log: `smart` → `massed-ollama/lq-ai-default`, Tier 2 |
| 4.7 | 2026-09-25 | Upload of fictional test document to a new, empty knowledge base; question answered (47 days) | manual; routing log + `document_chunks` | PASS | 3 new passages, all embedded via `massed-ollama/nomic-embed-text:v1.5`; the 1,341 older passages without embeddings untouched, so no existing documents were sent to the VM |
| 5.1 | 2026-09-25 | Terminate VM in Massed (without `docker stop`): does the name leave the tailnet? | Massed Terminate; `tailscale status` every 15 s for 571 s | **FAIL** | Offline immediately, but still registered after 571 s. Cause: Terminate is an abrupt power-off, so the container's logout never runs; ephemeral cleanup takes longer than 10 min. Fix: `docker stop lq-ai-ollama` over SSH before terminating is now a required daily step (README). This time the offline device was removed by hand in the admin console (user) |
| 5.2 | 2026-09-25 | Name free after manual removal | `tailscale status` | PASS | |
| 5.3 | 2026-09-25 | New VM (64.247.196.50) with `MODELS=mistral:7b`; public IP closed | `curl -m 6 http://64.247.196.50:{11434,443,80}` | PASS | No answer on any port |
| 5.4 | 2026-09-25 | Name is `lq-ai-ollama` (not `-1`) | `tailscale status` | PASS | |
| 5.5 | 2026-09-25 | LQ-AI picks up the new model with no config change | gateway `GET /v1/models` | PASS | Picker shows `massed-ollama/mistral:7b`; `qwen3.5:9b` gone automatically |
| 5.6 | 2026-09-25 | Default choices use the new model | `/api/tags` digests; `POST /v1/chat/completions` model `smart`, twice | PASS | `lq-ai-default` = `mistral:7b` (digest 6577803aa9a0); Tier 2; 43.3 s first (GPU load), 0.8 s second |
| 5.7 | 2026-09-25 | Runs on the GPU | `GET /api/ps` | PASS | 8.9 GB, 100% on the GPU, context 32768 |
| 6.1 | 2026-09-25 | Clean shutdown over SSH | `ssh Ubuntu@64.247.196.50`; `sudo docker stop lq-ai-ollama` | PASS | Username `Ubuntu`; `sudo` required (plain `docker` = permission denied). README updated |
| 6.2 | 2026-09-25 | No running instances in Massed (billing stopped) | Massed Running Instances page (user) | PASS | Empty |
| 6.3 | 2026-09-25 | `lq-ai-ollama` removed from the tailnet | `tailscale status` | PASS | Gone immediately, thanks to the logout on stop |
| 6.4 | 2026-09-25 | Gateway with VM off: fails fast, doesn't hang | `POST /v1/chat/completions` (smart) inside gateway | PASS | HTTP 503 in 0.2 s: `provider_unavailable`, "failed to reach Ollama: ConnectError"; Massed models drop out of `/v1/models` |
| 6.5 | 2026-09-25 | What the user sees in LQ-AI with VM off | chat "hello" in the web UI (user) | PASS | "Error: provider_unavailable. The assistant message was persisted with the partial content above for audit." Wording added to README |

## Test 1: local image test (free, laptop, CPU only)
**PASS** (after one fix). Found and fixed: Ollama's 403 on requests via the tailnet
name (see 1.4a). Also confirmed: the embedding size (768) matches LQ-AI's setting, and
logout on stop frees the name so the next start gets the same name.

## Test 2: published image (free)
**PASS.** GitHub builds and publishes the image; it can be downloaded without logging in.

## Test 3: first Massed VM (paid)
**PASS** on the second VM. The first VM failed only because the key placeholder was
left in the command. Security check passed on both.

## Test 4: LQ-AI end-to-end (paid)
**PASS.** Chat and document search in LQ-AI both run on the Massed VM, labelled Tier 2.

## Test 5: model switch (paid)
**PASS** after one fix. Switching to Mistral needed no LQ-AI change. Found and fixed: a
VM terminated in Massed keeps its name on the tailnet for well over 10 minutes, so
`docker stop` before terminating is now a required daily step.

## Test 6: teardown
**PASS.** Clean stop + terminate leaves nothing running or billing, frees the name, and
LQ-AI shows a clear error instead of hanging.

## Summary (2026-09-25)
All six tests pass.

Failures found and fixed along the way:
1. **Ollama refused tailnet requests (403)** (Test 1). While listening only on its own
   loopback, Ollama rejects requests addressed to another name. Fixed by listening
   inside the container (still no published ports).
2. **Key placeholder left in the command** (Test 3, first VM). It couldn't join the
   tailnet and stopped itself; nothing was exposed. The daily routine now includes a
   check of the command box before Deploy.
3. **Massed's default command includes `--network=host`** (Test 3). It would have exposed
   Ollama on the public IP. Removed from the template; the README warns about it.
4. **The gateway reads a stored copy of `gateway.yaml` and only `OLLAMA_BASE_URL` from
   `.env`** (Test 4). The config change was adapted and README section C documents it.
5. **Terminating in Massed doesn't free the name** (Test 5): still taken after 10+
   minutes. `sudo docker stop lq-ai-ollama` before terminating is now a required daily
   step.

Also fixed outside this project: LQ-AI's Redis save file was damaged by an abrupt
shutdown (backed up, repaired with `redis-check-aof --fix`, 1,382 bytes dropped).

Open decisions: Tailscale access rule to limit the VM (README step A.4);
pseudonymization for Tier 2 (deferred).
