# LQ-AI on a daily Massed Compute GPU

Each day you rent a GPU virtual machine (VM) on Massed Compute, choose the open-source
model you want, and LQ-AI on your laptop reaches it at **one fixed address**:

```
https://lq-ai-ollama.tail6d9c1d.ts.net
```

The VM changes every day, but the address doesn't, so the LQ-AI configuration never
needs changing again.

**How it works, in plain English**

- The VM runs a single container built from this folder: **Ollama** (runs the model)
  plus **Tailscale** (joins your private network, your "tailnet", under the fixed name).
- Ollama can only be reached **through your tailnet**. LQ-AI uses HTTPS. Plain HTTP on
  port 11434 also answers, but only inside the tailnet, where traffic is already
  encrypted. Nothing is published on the VM's public internet address.
- LQ-AI's gateway only accepts HTTPS for anything outside your own machine, so the
  container gets a free HTTPS certificate for its name each time it starts.
- When the container is stopped, it logs out of the tailnet so the name is free for
  tomorrow's VM.

**What's in this folder**

| File | What it is |
|---|---|
| `Dockerfile` | Recipe for the image: official Ollama + official Tailscale, both pinned to exact versions |
| `entrypoint.sh` | What the container does at startup and shutdown (explained step by step inside) |
| `README.md` | This guide |
| `TEST-LOG.md` | Record of every test run |
| `../../.github/workflows/massed-ollama-image.yml` | GitHub builds and publishes the image for you |

The image contains **no models and no secrets**, so it's safe for it to be public.

---

## Privacy position (read once)

- **Tier 2, not Tier 1.** LQ-AI labels this setup **Tier 2 ("cloud inference you
  control")**. Tier 1 means your own hardware with no internet needed. A rented VM can
  never be Tier 1. Caveat: the project's Tier 2 examples assume your *own* AWS, Azure or
  Google account under your own data-processing agreement. Massed owns the hardware you
  rent, so check Massed's terms if that matters for privileged work.
- **What travels to the VM:** your chat messages, and passages from uploaded documents
  (to compute search "embeddings"). All of it is encrypted by the tailnet on the way.
  Nothing is written to the VM's disk except the downloaded models, and terminating the
  VM wipes it. The embeddings themselves are stored only in LQ-AI's database on your
  laptop.
- **Pseudonymization is not applied here.** In LQ-AI it is off, and even when switched
  on it only covers Tiers 3–5. The Massed model therefore sees real names and amounts.
  (Your decision on this is pending.)
- **Heads-up on skills:** a few columns in the snapshot skills (limitation of liability,
  indemnification, governing law) require Tier 3 or higher, so they won't run on this
  setup.

---

## One-time setup

Do these once, in order. Tick them off as you go.

### A. Tailscale admin console (https://login.tailscale.com/admin)

1. [ ] **Check that HTTPS certificates are on.** Go to **DNS**, then **HTTPS Certificates**.
   They should already be enabled, since the old RunPod setup used them.
2. [ ] **Remove the old offline devices.** Go to **Machines**, find
   `runpod-rtx6000-ollama` and `xdqsrh10nqqzjs-6441141e`, then choose **...** > **Remove**.
3. [ ] **Create the auth key.** Go to **Settings** > **Keys** > **Generate auth key**. Set:
   - **Reusable**: ON (the same key works every day)
   - **Ephemeral**: ON (the VM's device is removed automatically after it goes offline)
   - **Pre-approved**: ON (if shown)
   - **Expiration**: the maximum (90 days). Put a reminder in your calendar to make a new one.

   Copy the key (it starts with `tskey-auth-`) into your password manager.
   **Never** put it in a file in this repository, in an email, or in a chat.
4. [ ] *(Recommended, needs your decision; not done yet)* **Limit what the VM can
   reach.** By default every device on a tailnet can connect to every other device, so
   the rented VM could, in principle, try to connect to your laptop. A short access rule
   can allow "laptop to VM on HTTPS (port 443)" and nothing else, which also closes the
   plain-HTTP port 11434 on the tailnet. Ask Claude to prepare it if you want it.

### B. Publish the image with GitHub (free)

Your fork is `https://github.com/roselethbart-del/lq-ai`. The image will be:

```
ghcr.io/roselethbart-del/lq-ai-massed-ollama:latest
```

1. [ ] On your fork, open **Actions**. If GitHub asks, click **"I understand my
   workflows, go ahead and enable them"**.
2. [ ] Push the `local/massed-ollama` branch to your fork (Claude does this after you say yes).
   GitHub then builds the image automatically (about 3–5 minutes). Watch it under
   **Actions** > **massed-ollama image**. A green tick means success.
3. [x] **The image must be public** (Massed downloads it without logging in). This
   happened automatically because your fork is public (verified in Test 2). If it ever
   isn't, open your GitHub profile > **Packages** > **lq-ai-massed-ollama** > **Package
   settings** > **Change visibility** > **Public**.

The image is built for Intel/AMD processors, which is what Massed VMs use. Your laptop
has an ARM processor, so for local tests either build it yourself (as in Test 1) or add
`--platform linux/amd64` when downloading.

To rebuild later (for example after an update), use **Actions** > **massed-ollama image**
> **Run workflow**, choosing the `local/massed-ollama` branch.

*Fallback if GitHub Actions is unavailable:* build on the laptop and push by hand (Docker
Desktop running; you need a GitHub token with `write:packages`):

```powershell
cd "C:\Users\bartr\Documents\01 Techtools\lq-ai\ops\massed-ollama"
docker build -t ghcr.io/roselethbart-del/lq-ai-massed-ollama:latest .
docker login ghcr.io -u roselethbart-del
docker push ghcr.io/roselethbart-del/lq-ai-massed-ollama:latest
```

### C. One-time LQ-AI change (done 2026-09-25, during Test 4)

Both files are **your local files** (git ignores them), so this doesn't conflict with
upstream updates. The originals are backed up in `C:\Users\bartr\lq-ai-backups\`.

**`.env`**: only one line changed:

```
OLLAMA_BASE_URL=https://lq-ai-ollama.tail6d9c1d.ts.net
```

**`gateway.yaml`**:
- The old `runpod-local` entry is renamed to `massed-ollama` (Tier 2), with
  `base_url: ${OLLAMA_BASE_URL:-https://lq-ai-ollama.tail6d9c1d.ts.net}` and models
  `lq-ai-default` and `nomic-embed-text:v1.5`. It reads `OLLAMA_BASE_URL` because
  that's the only Ollama variable `docker-compose.yml` passes to the gateway.
- `smart`, `fast` and `budget` point to `massed-ollama` / `lq-ai-default`.
- `embedding` points to `massed-ollama` / `nomic-embed-text:v1.5` (768 numbers, matching
  `EMBEDDING_DIMENSION=768` and the database).
- In `inference_tiers.overrides`, `massed-ollama: 2` replaces `runpod-local: 2`. This
  line is what makes LQ-AI show Tier 2.
- The old `ollama-local` entry (labelled Tier 1) is switched off with `enabled: false`.
  The `local`, `local-fast` and `local-thinking` choices still appear in the picker but
  answer "unavailable", which is correct because there's no model on your laptop.

**Important: the gateway runs from its own stored copy.** It doesn't read
`gateway.yaml` directly: it uses `/etc/lq-ai/gateway.yaml` inside the Docker volume
`gateway-config`, which was created from your file on first start. After editing your
`gateway.yaml`, copy it into place and restart the gateway:

```powershell
docker compose exec gateway cp /usr/share/lq-ai/gateway.yaml.example /etc/lq-ai/gateway.yaml
docker compose up -d --no-deps --force-recreate gateway
```

(Inside the container, your `gateway.yaml` appears under the name
`gateway.yaml.example`.) After an `.env` change, only the second command is needed.
Caution: model aliases changed in LQ-AI's admin screen live only in the stored copy;
the first command would overwrite them.

**Why `lq-ai-default`?** LQ-AI's default choices (and the citation checker) must name
one exact model. Each day the container gives the first model in `MODELS` a second,
fixed name, `lq-ai-default`, so those defaults keep working whether you pick Qwen or
Mistral. You can also pick the real model name (for example
`massed-ollama/qwen3.5:9b`) directly in LQ-AI's model picker. New models appear there
within about a minute of the VM being READY.

---

## The docker run command (paste into Massed)

In the Massed web UI, deploy a VM from a Docker image:

- **Docker container / image:**
  ```
  ghcr.io/roselethbart-del/lq-ai-massed-ollama:latest
  ```
- **Docker run command** (one line; replace the two CAPITALISED parts):
  ```
  docker run -d --runtime=nvidia --gpus all --shm-size 10g --name lq-ai-ollama --stop-timeout 30 -e TS_AUTHKEY=PASTE-TAILSCALE-KEY -e TS_HOSTNAME=lq-ai-ollama -e MODELS=MODEL-TAG -e OLLAMA_CONTEXT_LENGTH=32768 ghcr.io/roselethbart-del/lq-ai-massed-ollama:latest
  ```

> **Important:** Massed pre-fills this box with its own line containing
> `--network=host`. Always replace the **whole** box with the line above.
> `--network=host` would put Ollama on the VM's public internet address.

What each part means:

| Part | Meaning |
|---|---|
| `-d --name lq-ai-ollama` | Run in the background under a name, so `docker logs lq-ai-ollama` works |
| `--runtime=nvidia --gpus all` | Give the container the VM's GPU(s), the way Massed's own default line does |
| `--shm-size 10g` | Extra shared memory; part of Massed's default line, harmless |
| `--stop-timeout 30` | On stop, allow 30 s for the clean Tailscale logout |
| `-e TS_AUTHKEY=...` | Your Tailscale key from one-time step A.3 |
| `-e TS_HOSTNAME=lq-ai-ollama` | The fixed name. Only change it for tests |
| `-e MODELS=...` | The model tag(s) from the table below. Several are allowed, comma-separated, e.g. `qwen3.5:9b,mistral:7b`. The first is the default |
| `-e OLLAMA_CONTEXT_LENGTH=32768` | How much text the model can consider at once (about 25,000 words). Larger needs more GPU memory |

What it deliberately does **not** have: no `--network=host` and no `-p` (no public
ports), and no `--cap-add` or `--device` (Tailscale runs as a normal program, needing
no extra privileges). `nomic-embed-text:v1.5` is always downloaded as well; you don't
list it.

**Your Tailscale key and Massed:** the key is part of the command you paste, so Massed
stores it with your instance details. That's why it's an expiring key you can revoke at
any time in the Tailscale admin console.

---

## Which GPU for which model

Pick the **cheapest GPU listed on Massed with at least the memory shown**. Sizes
checked against the Ollama library on 2026-09-25. Memory needs assume the
`OLLAMA_CONTEXT_LENGTH=32768` above; a longer context needs more.

| Model tag (use exactly) | Maker | Download | GPU memory |
|---|---|---|---|
| `mistral:7b` | Mistral | 4.4 GB | 24 GB |
| `ministral-3:8b` | Mistral | 6.0 GB | 24 GB |
| `qwen3.5:9b` | Qwen | 6.6 GB | 24 GB |
| `ministral-3:14b` | Mistral | 9.1 GB | 24 GB |
| `magistral:24b` (reasoning) | Mistral | 14.3 GB | 48 GB |
| `mistral-small3.2:24b` | Mistral | 15.2 GB | 48 GB |
| `qwen3.5:27b` | Qwen | 17.4 GB | 48 GB |
| `qwen3:30b` | Qwen | 18.6 GB | 48 GB |
| `qwen3:32b` | Qwen | 20.2 GB | 48 GB |
| `qwen3.5:35b` | Qwen | 23.9 GB | 48 GB |
| `llama3.3:70b` | Meta | 42.5 GB | 80 GB |

- A 1 GPU quantity is enough for everything in this table.
- Download time counts against your rented time: roughly 1–2 minutes per 10 GB on a
  datacenter connection.
- Other models: search https://ollama.com/library. The tag is the part after the `:`
  on the model's **Tags** page. As a rule of thumb, GPU memory needed is the download
  size plus about 30% plus a few GB for context.

---

## Daily routine

1. **Choose the model** and look up its GPU size in the table.
2. **Launch** on https://vm.massedcompute.com: pick that GPU type, quantity 1, the image
   name, and the docker run command with your key and model filled in. **Before
   clicking Deploy, check the box:** it must not contain `PASTE-TAILSCALE-KEY` (the key
   goes there) or `--network=host`. Deploy.
3. **Wait for READY.** When the VM shows as running, connect with SSH from PowerShell.
   The IP and password are on the Running Instances page; the username is `Ubuntu`
   (capital U). Paste the password with a **right-click**; it stays invisible. Then
   watch the log:
   ```
   ssh Ubuntu@VM_IP
   sudo docker logs -f lq-ai-ollama
   ```
   `sudo` is needed on Massed VMs; if asked, give the same VM password again. Wait for
   a line starting with `READY:`. Press Ctrl+C to stop watching (the container keeps
   running). The docker command may still be starting for a minute or two after the VM
   itself is up; that's normal.
4. **Quick health check** (below) in PowerShell on your laptop.
5. **Work in LQ-AI** as usual.
6. **Finish, in this order:**
   1. In the SSH window, run `sudo docker stop lq-ai-ollama`. Wait until it prints
      `lq-ai-ollama` (a few seconds). This logs the VM out of the tailnet and frees the
      name `lq-ai-ollama` for tomorrow. Then type `exit`.
   2. Then **terminate the VM** on the Running Instances page. Terminating stops
      billing; there's no pause.

   **Don't skip step 1.** Massed's Terminate switches the VM off abruptly, so it can't
   log out itself, and the name then stays taken for a long time (still taken after
   10 minutes in Test 5). If you forgot, remove the offline `lq-ai-ollama` in the
   Tailscale admin console under **Machines** before launching the next VM.

### Quick health check (PowerShell on the laptop)

```powershell
$u = "https://lq-ai-ollama.tail6d9c1d.ts.net"

# 1. Is it on the tailnet with the right name? (look for lq-ai-ollama, not lq-ai-ollama-1)
tailscale status

# 2. Which models are available?
(Invoke-RestMethod "$u/api/tags").models | Select-Object name

# 3. Does it answer?
Invoke-RestMethod "$u/api/generate" -Method Post -Body '{"model":"lq-ai-default","prompt":"Reply with five words.","stream":false}' | Select-Object response

# 4. Is it on the GPU? size_vram should equal size (100% on the GPU)
(Invoke-RestMethod "$u/api/ps").models | Select-Object name, size, size_vram, context_length
```

---

## Troubleshooting

**The name shows up as `lq-ai-ollama-1`.** Yesterday's device is still registered, so
the tailnet gave the new VM a different name and LQ-AI can't find it. The log says so
with a WARNING. Fix:

1. In the Tailscale admin console, under **Machines**, remove the *offline* `lq-ai-ollama`.
2. On the VM, run `sudo docker restart lq-ai-ollama`. It logs out and in again under the
   right name.

To prevent it, always run `sudo docker stop lq-ai-ollama` before terminating (daily step 6).
If the VM is already running as `-1`, you can also remove the offline device first and
then run `sudo docker restart lq-ai-ollama` on the VM (it takes a new certificate).

**Models don't show up in LQ-AI.**
1. Check that the log shows `READY:`.
2. Run health check 2. If it fails from the laptop, the problem is the tailnet (next item).
3. LQ-AI refreshes its model list every minute; wait one minute and reload the page.
4. Check the gateway log for errors:
   `docker compose logs --tail 50 gateway`.

**The VM is running but not on the tailnet.**
- The container may still be starting; check the log.
- `ERROR: Could not join the tailnet` means the key is expired or revoked, or wasn't
  created as Reusable. Make a new key (step A.3).
- No log at all (`sudo docker ps` shows no `lq-ai-ollama`) means the docker run command
  didn't start. Check for typos.

**`ERROR: Could not get an HTTPS certificate`.** Either HTTPS certificates are off in
the Tailscale admin console (step A.1), or the weekly limit was reached. The free
certificate service allows **5 certificates per week for the same name**, and each VM
start uses one. More than 5 launches in 7 days (including restarts) will hit it.
Temporary workaround: launch with `-e TS_HOSTNAME=lq-ai-ollama-b`, and for that week set
`OLLAMA_BASE_URL=https://lq-ai-ollama-b.tail6d9c1d.ts.net` in `.env`, then run
`docker compose up -d --no-deps --force-recreate gateway`. Change it back afterwards.

**The model runs on the CPU instead of the GPU** (very slow answers; health check 4
shows `size_vram` well below `size`; or the log says `Ollama found NO GPU`).
- Make sure `--gpus all` is in the docker run command.
- The model plus context may not fit: choose a bigger GPU from the table, or lower
  `OLLAMA_CONTEXT_LENGTH` (for example to 16384).

**LQ-AI shows an error when the VM is off.** That's expected: with no VM there's no
model. The chat shows, straight away:

> Error: provider_unavailable. The assistant message was persisted with the partial content above for audit.

Launch a VM, wait for READY, and try again. The Massed models also disappear from the
model picker while no VM is running, and document uploads can't be processed (they
need the embedding model on the VM).

**Detailed logs inside the container** (over SSH):
`sudo docker exec lq-ai-ollama tail -n 50 /tmp/ollama.log` (Ollama) and
`sudo docker exec lq-ai-ollama tail -n 50 /tmp/tailscaled.log` (Tailscale).

---

## Updating the image

Base versions are pinned in `Dockerfile` (Ollama `0.34.4`, Tailscale `v1.102.5`). To
update, ask Claude to bump both version and digest, run Test 1 again, then push; GitHub
rebuilds automatically.
