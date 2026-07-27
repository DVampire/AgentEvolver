# Full VS Code in the browser (openvscode-server), editing a session workspace.
#
# The frontend embeds this in an iframe. It is served at the ROOT path of a
# per-session host (``<sid>.ide.localhost``) so VS Code's absolute asset paths
# (``/stable-<commit>/static/...``) resolve without any base-path patching —
# see agentevolver/ide/README.md for the routing chain.
#
# Ports:
#   3000  openvscode-server (HTTP + the workbench WebSocket, same port)
#
# Deliberately a thin layer over the upstream image: no apt install, so the
# build needs no network beyond the base pull. The stock image already carries
# git/curl/bash, and VS Code bundles its own ripgrep for search. Heavy project
# toolchains (conda, project deps) belong in the base container — this image is
# an editor, not the agent's execution environment.
#
# Runs as ROOT so it can write workspace files owned by the host user. That
# matches the rest of the framework: the base container also runs as root, and
# scripts/serve-ui.sh periodically chowns output/ back to the host owner.
#
# Build (the IDE manager auto-builds this when missing; or run it yourself):
#   docker build -t agentevolver/vscode:latest docker/vscode/
FROM gitpod/openvscode-server:latest

USER root

COPY entrypoint-vscode.sh /usr/local/bin/entrypoint-vscode
RUN chmod +x /usr/local/bin/entrypoint-vscode

EXPOSE 3000
ENTRYPOINT ["/usr/local/bin/entrypoint-vscode"]
