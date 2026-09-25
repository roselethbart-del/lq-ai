# LQ-AI on Massed Compute: Ollama + Tailscale in one image.
#
# No models and no secrets are baked in, so this image can be public.
# Models are downloaded when the container starts (MODELS variable) and the
# Tailscale key is passed in at launch (TS_AUTHKEY variable).
#
# Both base images are pinned by version AND digest, so a rebuild months from
# now produces the same software. To upgrade, change the version and digest
# together (see README, "Updating the image").

# Stage 1: borrow the two Tailscale programs from the official Tailscale image.
FROM tailscale/tailscale:v1.102.5@sha256:c507f3a2a6ab1cabd8d809b98edeb41edbd5c3fb6ad9632ffd098b4c7d0b4065 AS tailscale

# Stage 2: start from the official Ollama image (includes NVIDIA GPU support).
FROM ollama/ollama:0.34.4@sha256:8262851b2846b87c649eddf3e76beb270c52f4d1bc94559f47efde16b0841551

COPY --from=tailscale /usr/local/bin/tailscaled /usr/local/bin/tailscale /usr/local/bin/
COPY entrypoint.sh /usr/local/bin/entrypoint.sh

# Strip Windows line endings (in case the file was edited on Windows) and make
# the startup file runnable.
RUN sed -i 's/\r$//' /usr/local/bin/entrypoint.sh && chmod 755 /usr/local/bin/entrypoint.sh

# Ollama listens inside the container. It is NOT reachable from the VM's public
# IP because the docker run command publishes no ports (-p); the way in is
# HTTPS over the tailnet via `tailscale serve`.
# Why 0.0.0.0 and not 127.0.0.1: when Ollama listens on 127.0.0.1 only, it
# refuses (HTTP 403) any request addressed to another host name, which includes
# our tailnet name, so LQ-AI could not use it. Found in Test 1.
ENV OLLAMA_HOST=0.0.0.0:11434

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
