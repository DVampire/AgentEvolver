---
name: docker
description: "Provides the Docker backend integration point through `docker_manager`."
version: 0.1.0
type: module
category: docker
requirements: []
metadata:
  tracks_package_version: true
---
# Docker

Provides the Docker backend integration point through `docker_manager`.

This module is currently a scaffold. The implemented generic isolation contract lives in
`sandbox/`; Docker-specific behavior should remain an adapter to that contract rather than
creating a second execution API.
