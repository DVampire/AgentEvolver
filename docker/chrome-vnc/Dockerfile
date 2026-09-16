# Headful Chrome + VNC: a virtual display (Xvfb), a VNC server (x11vnc), and a
# websockify bridge serving noVNC on 6080 — so the frontend can watch the live browser
# over a WebSocket, while Playwright drives it via CDP.
#
# Ubuntu, with Chrome from Google's own repository.
#
# This used to build on `opensandbox/chrome`, but nothing here needed that image except
# the browser binary: this container's ENTRYPOINT is its own script, not the OpenSandbox
# agent, so it never had a control channel to lose. An official base and an official
# Chrome say plainly where each part comes from.
#
# Ports:
#   9222  Chrome DevTools (CDP)         — driven by Playwright (connect_over_cdp)
#   5900  raw VNC (RFB)                 — internal
#   6080  websockify (RFB over WebSocket) — what noVNC in the browser connects to
#
# Build (the browser env auto-builds this when missing; or run it yourself):
#   docker build -t agentevolver/chrome-vnc:latest docker/chrome-vnc/
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

# Xvfb (virtual display), a minimal WM, x11vnc (RFB server), noVNC+websockify, and the
# fonts a page needs to render text rather than boxes.
RUN apt-get update && apt-get install -y --no-install-recommends \
        xvfb \
        fluxbox \
        x11vnc \
        novnc \
        websockify \
        x11-utils \
        procps \
        ca-certificates \
        wget \
        fonts-liberation \
        fonts-noto-cjk \
        fonts-noto-color-emoji \
    && rm -rf /var/lib/apt/lists/*

# Google Chrome from Google's own apt repository.
#
# NOT Ubuntu's `chromium-browser`: on 24.04 that is a 50 KB transitional package that
# pulls in 36 MB of snapd and installs the browser as a snap — and snaps do not run in a
# container. It installs "successfully" and leaves no usable browser behind, which the
# entrypoint would only discover as "No Chrome/Chromium binary found in image".
RUN wget -qO /etc/apt/keyrings/google-chrome.asc https://dl.google.com/linux/linux_signing_key.pub \
    && echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/google-chrome.asc] https://dl.google.com/linux/chrome/deb/ stable main" \
       > /etc/apt/sources.list.d/google-chrome.list \
    && apt-get update && apt-get install -y --no-install-recommends google-chrome-stable \
    && rm -rf /var/lib/apt/lists/*

# noVNC ships its client under /usr/share/novnc; expose vnc.html as the index so
# a plain http open of the endpoint also works, not only the RFB WebSocket.
RUN if [ -f /usr/share/novnc/vnc.html ] && [ ! -e /usr/share/novnc/index.html ]; then \
        ln -s /usr/share/novnc/vnc.html /usr/share/novnc/index.html; \
    fi

COPY entrypoint-vnc.sh /usr/local/bin/entrypoint-vnc
RUN chmod +x /usr/local/bin/entrypoint-vnc

EXPOSE 9222 5900 6080
ENTRYPOINT ["/usr/local/bin/entrypoint-vnc"]
