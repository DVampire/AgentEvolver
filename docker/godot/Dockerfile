FROM ubuntu:24.04

ARG GODOT_VERSION=4.7-stable
ARG NODE_VERSION=22.14.0
ARG MCP_REVISION=b5fa8cb17bdb14f16ec6ae8d7d62a56a3beaf128
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    ca-certificates curl unzip git xvfb xauth mesa-vulkan-drivers xz-utils \
    libx11-6 libx11-xcb1 libxcursor1 libxinerama1 libxrandr2 libxi6 \
    libasound2t64 libpulse0 libgl1 libegl1 libfontconfig1 fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*
RUN set -eu; arch="$(uname -m)"; \
    case "$arch" in x86_64) node_arch=x64 ;; aarch64) node_arch=arm64 ;; *) exit 1 ;; esac; \
    cd /tmp; archive="node-v${NODE_VERSION}-linux-${node_arch}.tar.xz"; \
    curl -fSL --retry 3 -O "https://nodejs.org/dist/v${NODE_VERSION}/${archive}"; \
    curl -fSL --retry 3 -O "https://nodejs.org/dist/v${NODE_VERSION}/SHASUMS256.txt"; \
    grep " ${archive}$" SHASUMS256.txt | sha256sum -c -; \
    tar -xJf "$archive" -C /usr/local --strip-components=1; rm "$archive" SHASUMS256.txt
RUN set -eu; arch="$(uname -m)"; \
    case "$arch" in x86_64) godot_arch=x86_64 ;; aarch64) godot_arch=arm64 ;; *) exit 1 ;; esac; \
    cd /tmp; archive="Godot_v${GODOT_VERSION}_linux.${godot_arch}.zip"; \
    url="https://github.com/godotengine/godot-builds/releases/download/${GODOT_VERSION}"; \
    curl -fSL --retry 3 -O "$url/$archive"; \
    curl -fSL --retry 3 -o SHA512-SUMS.txt "$url/SHA512-SUMS.txt"; \
    grep " ${archive}$" SHA512-SUMS.txt | sha512sum -c -; \
    unzip -q "$archive" -d /tmp/godot-unzip; \
    mv "/tmp/godot-unzip/Godot_v${GODOT_VERSION}_linux.${godot_arch}" /usr/local/bin/godot; \
    chmod +x /usr/local/bin/godot; rm -rf "$archive" SHA512-SUMS.txt /tmp/godot-unzip
RUN git init /opt/godot-mcp && cd /opt/godot-mcp \
    && git remote add origin https://github.com/beremaran/godot-agent-loop.git \
    && git fetch --depth 1 origin "$MCP_REVISION" && git checkout --detach FETCH_HEAD \
    && test "$(git rev-parse HEAD)" = "$MCP_REVISION" \
    && npm ci --ignore-scripts && npm run build && npm prune --omit=dev --ignore-scripts
ENV GODOT_PATH=/usr/local/bin/godot GODOT_BIN=/usr/local/bin/godot \
    GODOT_MCP_TOOL_SURFACE=full LIBGL_ALWAYS_SOFTWARE=1 DISPLAY=:99 \
    XDG_CACHE_HOME=/tmp/godot-cache XDG_CONFIG_HOME=/tmp/godot-config \
    XDG_DATA_HOME=/tmp/godot-data
LABEL org.opencontainers.image.source="https://github.com/DVampire/AgentEvolver" \
      agentevolver.godot.mcp_revision="${MCP_REVISION}"
# Verify the official archive, then keep only this architecture's Linux templates.
RUN set -eu; arch="$(uname -m)"; \
    case "$arch" in x86_64) template_arch=x86_64 ;; aarch64) template_arch=arm64 ;; *) exit 1 ;; esac; \
    cd /tmp; archive="Godot_v${GODOT_VERSION}_export_templates.tpz"; \
    url="https://github.com/godotengine/godot-builds/releases/download/${GODOT_VERSION}"; \
    curl -fsSL --retry 3 --max-time 1200 -O "$url/$archive"; \
    curl -fsSL --retry 3 -o SHA512-SUMS.txt "$url/SHA512-SUMS.txt"; \
    grep " ${archive}$" SHA512-SUMS.txt | sha512sum -c -; \
    mkdir -p /opt/godot/export_templates; \
    unzip -q -j "$archive" "templates/linux_debug.${template_arch}" "templates/linux_release.${template_arch}" "templates/version.txt" -d /tmp/linux-templates; \
    template_version="$(cat /tmp/linux-templates/version.txt)"; \
    mv /tmp/linux-templates "/opt/godot/export_templates/${template_version}"; \
    chmod -R a+rX /opt/godot/export_templates; rm "$archive" SHA512-SUMS.txt
COPY entrypoint.sh /usr/local/bin/godot-container
RUN chmod +x /usr/local/bin/godot-container
ENTRYPOINT ["/usr/local/bin/godot-container"]
