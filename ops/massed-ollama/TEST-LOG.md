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

## Test 1: local image test (free, laptop, CPU only)
**PASS** (after one fix). Found and fixed: Ollama's 403 on requests via the tailnet
name (see 1.4a). Also confirmed: the embedding size (768) matches LQ-AI's setting, and
logout on stop frees the name so the next start gets the same name.

## Test 2: published image (free)
**PASS.** GitHub builds and publishes the image; it can be downloaded without logging in.

## Test 3: first Massed VM (paid)
_Not run yet._

## Test 4: LQ-AI end-to-end (paid)
_Not run yet._

## Test 5: model switch (paid)
_Not run yet._

## Test 6: teardown
_Not run yet._

## Summary
_Written at the end of testing: what passed, what failed, what was fixed._
