# AgentEvolver project sandbox image (Model X): the container the whole agent runs
# in — the framework, every agent, and all tool execution (bash / file edits / git /
# experiment code). The host only launches it (scripts/run-in-sandbox.sh); the repo
# is bind-mounted in so source is live and outputs land back on the host. Service
# peers (browser / deploy) are spawned as sibling containers via the mounted Docker
# socket, so this image does NOT need opensandbox's execd/kernel runtime — it is a
# plain, general dev environment.
#
# conda base (parity with the host's agentos env) with the languages an agent may
# run locally: Python, Node/JS, R. Plus git, a CPU data-science stack, and the
# agentevolver framework itself.
#
# Build FROM THE REPO ROOT (context must be the repo, this installs the framework):
#   docker build -f docker/base/Dockerfile -t agentevolver/base:latest .
#
# Smoke-test:
#   docker run --rm agentevolver/base:latest python -c "import agentevolver, lightgbm; print('ok')"
#   docker run --rm agentevolver/base:latest bash -lc "node -v && Rscript -e 'cat(R.version.string)'"

FROM continuumio/miniconda3:latest

# System tools: git (git_tool), build-essential (native wheels), curl/ca-certs.
# libgl1 + libglib2.0-0: opencv (pulled in by camelot/markitdown) loads libGL.so.1
# and libgthread at import; the slim conda base doesn't ship them.
RUN apt-get update && apt-get install -y --no-install-recommends \
        git \
        build-essential \
        curl \
        ca-certificates \
        libgl1 \
        libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Languages the agent may run locally inside the sandbox: Node/JS and R (Python is
# the conda base). Installed from conda-forge into the base env so they're on PATH.
RUN conda install -y -c conda-forge nodejs r-base \
    && conda clean -afy

# CPU data-science stack for experiments (numpy/pandas/scikit-learn/etc. also come
# in as framework deps; lightgbm/xgboost/matplotlib/seaborn are listed explicitly).
RUN pip install --no-cache-dir \
        numpy pandas scipy scikit-learn matplotlib seaborn lightgbm xgboost

# GPU deep-learning: torch built for CUDA 12.6. The default PyPI wheel targets
# CUDA 13, which is too new for the host driver (575 / CUDA 12.9) and would report
# cuda_available=False; the cu126 wheel matches. Only the host driver is required —
# the CUDA userspace ships inside the wheel. The launcher passes --gpus.
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cu126

# The agentevolver framework + sandbox extra (opensandbox / opensandbox-server /
# docker) so the in-container agent can spawn peer containers through the mounted
# Docker socket. Editable install off a COPY bakes deps into the image; at runtime
# the launcher bind-mounts the host repo over /AgentEvolver so source edits are picked up live.
COPY . /AgentEvolver
RUN pip install --no-cache-dir -e "/AgentEvolver[sandbox]"

# The host repo is bind-mounted at runtime (different owner uid); mark it safe so
# git_tool works. WORKDIR is the mounted project.
RUN git config --global --add safe.directory /AgentEvolver
WORKDIR /AgentEvolver

# Entrypoint runs the command as root (pip install + Docker socket access) and, on
# exit, chowns the run's outputs back to the mounted project's host owner so they are
# not left root-owned on the host.
COPY docker/base/entrypoint.sh /usr/local/bin/entrypoint
RUN chmod +x /usr/local/bin/entrypoint
ENTRYPOINT ["/usr/local/bin/entrypoint"]
