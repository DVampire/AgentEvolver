FROM agentevolver/godot:4.7-b5fa8cb-input2
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    python3 x11vnc xdotool novnc websockify \
    && rm -rf /var/lib/apt/lists/*
COPY prepare.py start.sh /opt/godot-play/
COPY public /opt/godot-play/public/
RUN chmod +x /opt/godot-play/start.sh \
    && ln -s /usr/share/novnc /opt/godot-play/public/novnc
ENV DISPLAY=:99 XDG_DATA_HOME=/tmp/preview-data XDG_CONFIG_HOME=/tmp/preview-config \
    XDG_CACHE_HOME=/tmp/preview-cache PORT=6080
WORKDIR /app
ENTRYPOINT ["/opt/godot-play/start.sh"]
