# A general Linux desktop the agent drives with mouse/keyboard (the "computer"
# environment). Built on the OpenSandbox chrome image so the OpenSandbox agent
# and a browser (for web apps like Bilibili) are already present; we add a full
# X11 desktop (XFCE), a VNC->websockify bridge for the live view, and the input
# / capture / accessibility tools the environment needs.
#
# IMPORTANT: the ENTRYPOINT is intentionally NOT overridden. The OpenSandbox
# agent (its default entrypoint) must keep running so the sandbox's run_command
# channel works — that is how the environment injects xdotool input and takes
# screenshots. The desktop itself is started on demand by /usr/local/bin/
# start-desktop (called via run_command once the sandbox is up), not by the
# entrypoint.
#
# Ports (mapped to ephemeral host ports by the opensandbox proxy):
#   5900  raw VNC (internal)
#   6080  websockify (RFB over WebSocket) — the noVNC live-view endpoint
FROM opensandbox/chrome:latest

USER root

# X server + XFCE desktop, VNC bridge (x11vnc + websockify/noVNC), input/capture
# (xdotool + scrot), accessibility for grounding (AT-SPI), and CJK fonts so apps
# like Bilibili/WeChat render text.
RUN apt-get update && apt-get install -y --no-install-recommends \
        xvfb \
        x11vnc \
        websockify \
        novnc \
        xfce4 \
        xfce4-terminal \
        dbus-x11 \
        xdotool \
        scrot \
        at-spi2-core \
        python3-pyatspi \
        fonts-liberation \
        fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

RUN if [ -f /usr/share/novnc/vnc.html ] && [ ! -e /usr/share/novnc/index.html ]; then \
        ln -s /usr/share/novnc/vnc.html /usr/share/novnc/index.html; \
    fi

COPY start-desktop.sh /usr/local/bin/start-desktop
COPY a11y-dump.py /usr/local/bin/a11y-dump
RUN chmod +x /usr/local/bin/start-desktop /usr/local/bin/a11y-dump

EXPOSE 5900 6080
