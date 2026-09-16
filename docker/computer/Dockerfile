# A general Linux desktop the agent drives with mouse/keyboard (the "computer"
# environment): a full X11 desktop (XFCE), a VNC->websockify bridge for the live view,
# and the input / capture / accessibility tools the environment needs.
#
# Plain Ubuntu. Nothing is inherited from another project's image.
#
# There is no OpenSandbox agent in here, and none is needed. OpenSandbox launches the
# container with its *own* command and ignores the image's ENTRYPOINT entirely (see the
# note in sandbox/default/chrome_vnc.py), so the agent binary this image used to carry
# was never executed — `run_command` works through the runtime, not through anything
# baked into the image. The desktop is started on demand by /usr/local/bin/start-desktop,
# called via run_command once the sandbox is up.
#
# Ports (mapped to ephemeral host ports by the opensandbox proxy):
#   5900  raw VNC (internal)
#   6080  websockify (RFB over WebSocket) — the noVNC live-view endpoint
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

# Mutter as the window manager — the shape Anthropic's own computer-use reference
# container uses, and it is the right one for a reason worth writing down.
#
# The obvious move is `ubuntu-desktop-minimal`, and it does not work here: GNOME's
# *session* asks logind for a seat, logind is part of systemd, and systemd as PID 1 in a
# container needs privileges this host would not give it — the shell comes up as "Oh no!
# Something has gone wrong." and says nothing more useful. Mutter is GNOME's window
# manager and runs perfectly well on its own, so windows are drawn the way GNOME draws
# them without any of the session machinery behind it. With Yaru and the Ubuntu font on
# top, this looks like Ubuntu because its parts *are* Ubuntu's.
#
# What mutter does NOT give you is a desktop: no menu, no icons, no way to start
# anything. The reference container does not care, because the only thing driving it is
# a model typing xdotool. A person looking at the live view sees an empty wallpaper and
# reasonably concludes it is broken. So the desktop *surface* comes from XFCE's two
# standalone pieces — xfdesktop (wallpaper, icons, right-click menu) and xfce4-panel
# (Applications menu, window list, tray, clock). Both are plain X clients: they work
# under any window manager and neither asks for a session manager or a seat.
#
# Alongside it: the VNC bridge (x11vnc + websockify/noVNC), input/capture (xdotool +
# scrot), accessibility for grounding (AT-SPI), and a browser for web apps.
RUN apt-get update && apt-get install -y --no-install-recommends \
        xvfb \
        x11vnc \
        websockify \
        novnc \
        mutter \
        xfdesktop4 \
        xfce4-panel \
        xfce4-settings \
        xfce4-appfinder \
        xfconf \
        xfce4-terminal \
        thunar \
        gnome-text-editor \
        yaru-theme-gtk \
        yaru-theme-icon \
        ubuntu-wallpapers \
        fonts-ubuntu \
        dbus-x11 \
        x11-xserver-utils \
        xdotool \
        scrot \
        at-spi2-core \
        python3-pyatspi \
        python3 \
        ca-certificates \
        wget \
        gnupg \
        adwaita-icon-theme \
        fonts-liberation \
        fonts-dejavu-core \
        fonts-noto-cjk \
        fonts-noto-color-emoji \
    && rm -rf /var/lib/apt/lists/*

# Google Chrome from Google's own apt repository.
#
# NOT Ubuntu's `chromium-browser`: on 24.04 that is a 50 KB transitional package that
# pulls in 36 MB of snapd and installs the browser as a snap — and snaps do not run in a
# container. It installs "successfully" and leaves no usable browser behind.
RUN wget -qO /etc/apt/keyrings/google-chrome.asc https://dl.google.com/linux/linux_signing_key.pub \
    && echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/google-chrome.asc] https://dl.google.com/linux/chrome/deb/ stable main" \
       > /etc/apt/sources.list.d/google-chrome.list \
    && apt-get update && apt-get install -y --no-install-recommends google-chrome-stable \
    && rm -rf /var/lib/apt/lists/*

# Chrome refuses to run as root unless --no-sandbox is passed, and this container is
# root by design (xdotool, scrot and run_command all are). Launched from the menu or a
# desktop icon it therefore exited immediately, printing its objection to a stderr
# nobody was reading — a double-click that did nothing at all, with no error dialog.
#
# Patched into the launcher rather than left to each caller: the desktop entry IS how a
# person starts Chrome here, and every other route already passes the flag. All three
# Exec lines, because the right-click actions are separate entries that would otherwise
# still fail. --disable-dev-shm-usage alongside it: the default /dev/shm in a container
# is 64 MB, which Chrome exhausts and reports as tabs crashing for no visible reason.
RUN sed -i 's|^Exec=/usr/bin/google-chrome-stable|Exec=/usr/bin/google-chrome-stable --no-sandbox --disable-dev-shm-usage|' \
        /usr/share/applications/google-chrome.desktop \
    && grep -c -- '--no-sandbox' /usr/share/applications/google-chrome.desktop

# The panel's layout, in place before it first runs (see the file for why).
COPY xfce4-panel.xml /root/.config/xfce4/xfconf/xfce-perchannel-xml/xfce4-panel.xml

# Icons on the desktop, copied from the entries the applications themselves install —
# so they launch with whatever flags the packager intended and cannot drift out of sync
# with a hand-written duplicate. Executable, or xfdesktop asks before every launch.
#
# Thunar is not here only to browse files. xfdesktop does not exec a desktop icon
# itself; it hands it to the org.xfce.FileManager D-Bus service, and with no provider
# for that name every icon fails with "requires a file manager service to be present".
# Nautilus does not claim the name, so the icons sat there looking functional and
# refused every double-click.
RUN mkdir -p /root/Desktop \
    && for f in google-chrome.desktop xfce4-terminal.desktop \
                thunar.desktop org.gnome.TextEditor.desktop; do \
           [ -f "/usr/share/applications/$f" ] && cp "/usr/share/applications/$f" /root/Desktop/; \
       done; \
       chmod +x /root/Desktop/*.desktop

# noVNC ships its client as vnc.html; the live view is served from the directory root.
RUN if [ -f /usr/share/novnc/vnc.html ] && [ ! -e /usr/share/novnc/index.html ]; then \
        ln -s /usr/share/novnc/vnc.html /usr/share/novnc/index.html; \
    fi

# Font rendering. fontconfig enables antialiasing on its own, but nothing tells Xft the
# DPI or the hint style, so toolkits that read X resources rather than fontconfig render
# at a guessed size — which is most of the blur in a screenshot of this desktop.
COPY Xresources /root/.Xresources

COPY start-desktop.sh /usr/local/bin/start-desktop
COPY a11y-dump.py /usr/local/bin/a11y-dump
RUN chmod +x /usr/local/bin/start-desktop /usr/local/bin/a11y-dump

EXPOSE 5900 6080
