#!/usr/bin/env bash
# Startup for the LQ-AI Massed Ollama container. In order:
#   1. join the tailnet under a fixed name (TS_HOSTNAME)
#   2. get an HTTPS certificate for that name and put Ollama behind it
#   3. start Ollama and download the chosen models (MODELS) + the embedding model
#   4. print READY, then keep running until the container is stopped
#   5. on stop: log out of Tailscale so the name is free for the next VM
#
# The Tailscale key is never printed. Detailed logs of Tailscale and Ollama go
# to /tmp/tailscaled.log and /tmp/ollama.log inside the container.

set -uo pipefail

log() { echo "[$(date -u +%H:%M:%S) UTC] $*"; }

# ---- Settings (passed in with -e on the docker run line) -------------------
TS_HOSTNAME="${TS_HOSTNAME:-lq-ai-ollama}"
MODELS="${MODELS:-}"
# The embedding model is always the same exact version, whatever chat model is
# chosen, so document search results stay comparable from day to day.
EMBED_MODEL="${EMBED_MODEL:-nomic-embed-text:v1.5}"
# The first model in MODELS is also made available under this fixed name, so
# LQ-AI's default choices (smart/fast/budget) work whichever model you pick.
DEFAULT_NAME="${DEFAULT_NAME:-lq-ai-default}"

TAILSCALED_PID=""
OLLAMA_PID=""
LOGGED_IN=0

# ---- Clean shutdown ---------------------------------------------------------
stop_everything() {
  if [ "$LOGGED_IN" = 1 ]; then
    log "Logging out of Tailscale so '$TS_HOSTNAME' is free for the next VM..."
    if tailscale logout >/dev/null 2>&1; then
      log "Logged out of Tailscale."
    else
      log "WARNING: Tailscale logout failed. The ephemeral key will still remove this device a while after it goes offline."
    fi
  fi
  [ -n "$OLLAMA_PID" ] && kill "$OLLAMA_PID" 2>/dev/null
  [ -n "$TAILSCALED_PID" ] && kill "$TAILSCALED_PID" 2>/dev/null
  wait 2>/dev/null
}

on_stop_signal() {
  log "Stop requested."
  stop_everything
  log "Stopped."
  exit 0
}
trap on_stop_signal TERM INT

fail() {
  log "ERROR: $*"
  log "NOT READY. See the README troubleshooting section."
  stop_everything
  exit 1
}

# ---- Check settings ---------------------------------------------------------
[ -n "${TS_AUTHKEY:-}" ] || fail "TS_AUTHKEY is not set. Add -e TS_AUTHKEY=... to the docker run command."
[ -n "$MODELS" ] || fail "MODELS is not set. Add e.g. -e MODELS=qwen3.5:9b to the docker run command."

# Turn "a, b ,c" into a clean list: a b c
CHAT_MODELS=()
IFS=',' read -ra _raw <<< "$MODELS"
for m in "${_raw[@]}"; do
  m="$(echo "$m" | tr -d '[:space:]')"
  [ -n "$m" ] && CHAT_MODELS+=("$m")
done
[ "${#CHAT_MODELS[@]}" -gt 0 ] || fail "MODELS contains no model names."

log "Starting. Name on tailnet: $TS_HOSTNAME. Chat model(s): ${CHAT_MODELS[*]}. Embedding model: $EMBED_MODEL."

# ---- Step 1: join the tailnet ----------------------------------------------
# Userspace networking: Tailscale runs as a normal program and needs no extra
# container privileges (no --cap-add, no /dev/net/tun). Its state lives in
# /tmp inside the container, so nothing survives the VM being terminated.
log "Step 1/4: joining the tailnet..."
mkdir -p /var/run/tailscale /tmp/tailscale
tailscaled --tun=userspace-networking --statedir=/tmp/tailscale >/tmp/tailscaled.log 2>&1 &
TAILSCALED_PID=$!

for _ in $(seq 1 30); do
  tailscale status >/dev/null 2>&1 && break
  # "status" fails before login too; the socket existing is enough to go on.
  [ -S /var/run/tailscale/tailscaled.sock ] && break
  sleep 1
done
[ -S /var/run/tailscale/tailscaled.sock ] || fail "Tailscale did not start. Last lines of its log: $(tail -n 5 /tmp/tailscaled.log)"

if ! tailscale up --auth-key="$TS_AUTHKEY" --hostname="$TS_HOSTNAME" --accept-dns=false >/tmp/tailscale-up.log 2>&1; then
  fail "Could not join the tailnet (is the key valid, reusable and not expired?). Tailscale said: $(grep -v -i 'tskey' /tmp/tailscale-up.log | tail -n 3)"
fi
LOGGED_IN=1
# The key is not needed any more; keep it away from Ollama.
unset TS_AUTHKEY

# Read back the full name the tailnet actually gave us.
FQDN="$(tailscale status --json --peers=false | sed -n 's/.*"DNSName": *"\([^"]*\)".*/\1/p' | head -n 1)"
FQDN="${FQDN%.}"
[ -n "$FQDN" ] || fail "Joined the tailnet but could not read this device's name."
if [ "${FQDN%%.*}" != "$TS_HOSTNAME" ]; then
  log "WARNING: the tailnet named this device '${FQDN%%.*}' instead of '$TS_HOSTNAME'."
  log "WARNING: an old device with that name is still registered, so LQ-AI will NOT find this VM."
  log "WARNING: fix: see README troubleshooting, 'The name shows up as ${TS_HOSTNAME}-1'."
fi
log "Joined the tailnet as $FQDN."

# ---- Step 2: HTTPS certificate + put Ollama behind it -----------------------
# The LQ-AI gateway only accepts HTTPS for addresses outside your own machine,
# so Ollama is published on the tailnet as https://<name> (port 443).
log "Step 2/4: getting an HTTPS certificate for $FQDN (can take up to a minute)..."
if ! tailscale cert --cert-file /tmp/cert.pem --key-file /tmp/cert.key "$FQDN" >/tmp/tailscale-cert.log 2>&1; then
  fail "Could not get an HTTPS certificate. Check that HTTPS Certificates are enabled in the Tailscale admin console, and whether the weekly certificate limit was reached (README troubleshooting). Tailscale said: $(tail -n 3 /tmp/tailscale-cert.log)"
fi
# The certificate stays cached inside Tailscale; the copies on disk are not needed.
rm -f /tmp/cert.pem /tmp/cert.key
if ! tailscale serve --bg --https=443 http://127.0.0.1:11434 >/tmp/tailscale-serve.log 2>&1; then
  fail "Could not set up HTTPS forwarding. Tailscale said: $(tail -n 3 /tmp/tailscale-serve.log)"
fi
log "HTTPS ready: https://$FQDN (tailnet only, no public port)."

# ---- Step 3: start Ollama and download models -------------------------------
log "Step 3/4: starting Ollama..."
ollama serve >/tmp/ollama.log 2>&1 &
OLLAMA_PID=$!
for _ in $(seq 1 60); do
  ollama list >/dev/null 2>&1 && break
  kill -0 "$OLLAMA_PID" 2>/dev/null || fail "Ollama stopped while starting. Last lines of its log: $(tail -n 5 /tmp/ollama.log)"
  sleep 1
done
ollama list >/dev/null 2>&1 || fail "Ollama did not start within 60 seconds."

# Report whether Ollama found a GPU (it logs one "inference compute" line per device).
DEVICE_LINES="$(grep 'inference compute' /tmp/ollama.log || true)"
GPU_LINES="$(echo "$DEVICE_LINES" | grep -vi 'library=cpu' || true)"
if [ -z "$DEVICE_LINES" ]; then
  log "Note: could not confirm the GPU from Ollama's log; the health check (/api/ps) will show it."
elif [ -n "$GPU_LINES" ]; then
  echo "$GPU_LINES" | while read -r line; do
    name="$(echo "$line" | grep -o 'description="[^"]*"' | cut -d'"' -f2)"
    [ -n "$name" ] || name="$(echo "$line" | grep -o 'name="[^"]*"' | cut -d'"' -f2)"
    mem="$(echo "$line" | grep -o 'total="[^"]*"' | cut -d'"' -f2)"
    log "GPU found: ${name:-unknown model}${mem:+ ($mem)}"
  done
else
  log "WARNING: Ollama found NO GPU. Models will run on the CPU and be very slow."
  log "WARNING: on Massed, check that the docker run command includes --gpus all."
fi

# Download one model, printing progress every 30 seconds.
pull_model() {
  local model="$1" start=$SECONDS last_note=$SECONDS pid pct
  log "Downloading $model..."
  ollama pull "$model" >/tmp/pull.log 2>&1 &
  pid=$!
  while kill -0 "$pid" 2>/dev/null; do
    sleep 5
    if [ $((SECONDS - last_note)) -ge 30 ] && kill -0 "$pid" 2>/dev/null; then
      pct="$(tail -c 400 /tmp/pull.log | tr '\r' '\n' | grep -o '[0-9]*%' | tail -n 1)"
      log "  ...still downloading $model ${pct:+($pct) }after $((SECONDS - start))s"
      last_note=$SECONDS
    fi
  done
  if wait "$pid"; then
    log "Downloaded $model in $((SECONDS - start))s."
  else
    fail "Could not download $model (check the exact name on ollama.com/library). Ollama said: $(tail -c 400 /tmp/pull.log | tr '\r' '\n' | grep -v '^$' | tail -n 2)"
  fi
}

for m in "${CHAT_MODELS[@]}" "$EMBED_MODEL"; do
  pull_model "$m"
done

# Fixed name for LQ-AI's default choices. "ollama cp" only adds a second name;
# it does not copy the model's data.
ollama cp "${CHAT_MODELS[0]}" "$DEFAULT_NAME" >/dev/null 2>&1 \
  || fail "Could not create the fixed name $DEFAULT_NAME for ${CHAT_MODELS[0]}."
log "$DEFAULT_NAME now points to ${CHAT_MODELS[0]}."

# ---- Step 4: ready ----------------------------------------------------------
log "Step 4/4: models available on this VM:"
ollama list | sed 's/^/    /'
log "READY: https://$FQDN serves ${CHAT_MODELS[*]} (default: $DEFAULT_NAME -> ${CHAT_MODELS[0]}) and $EMBED_MODEL"

# Keep running until stopped. If Ollama itself dies, stop cleanly.
wait "$OLLAMA_PID"
fail "Ollama stopped unexpectedly. Last lines of its log: $(tail -n 5 /tmp/ollama.log)"
