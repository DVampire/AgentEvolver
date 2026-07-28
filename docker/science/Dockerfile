# The Science workstation: JupyterLab plus a full scientific stack, on GPUs.
#
# A peer container, one per gateway session — the same shape as the Code view's
# VS Code container. It is NOT the base environment: the agent and its tools
# (bash, code_interpreter) run in base, which stays deliberately lean. This is
# where the heavy, open-ended work happens — training a model, running a sweep,
# writing the paper — and it is expected to keep growing, which is exactly why
# it is not in base.
#
# Built ON TOP OF agentevolver/base so the ~21GB of conda, CUDA torch, kernels
# and the project itself are SHARED layers already on the host; only the delta
# below costs disk. It also means a notebook here imports agentevolver and sees
# the same Python the agent uses.
#
# GPUs: torch ships its own CUDA userspace, so only the host driver and the
# nvidia container runtime are needed. The launcher passes --gpus directly to
# Docker (see agentevolver/sandbox/default/science.py) because opensandbox has
# no device option — that is the whole reason this sandbox bypasses it.
#
# Ports:
#   8888  JupyterLab (HTTP + its WebSocket, same port)
#
# Runs as ROOT, like base and vscode, so it can write workspace files owned by
# the host user; scripts/serve-ui.sh chowns output/ back periodically.
#
# Build (the science manager auto-builds this when missing; or run it yourself):
#   docker build -t agentevolver/science:latest -f docker/science/Dockerfile .
FROM agentevolver/base:latest

USER root

# LaTeX + pandoc: "write the paper" has to end in a PDF. nbconvert shells out to
# xelatex, and pandoc is what converts notebooks and markdown into a manuscript.
# texlive-xetex rather than texlive-full — the full distribution is ~5GB, most of
# it fonts and languages nothing here reaches for.
RUN apt-get update && apt-get install -y --no-install-recommends \
        pandoc \
        texlive-xetex \
        texlive-fonts-recommended \
        texlive-latex-recommended \
        texlive-plain-generic \
        graphviz \
        ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# JupyterLab itself. jupyter-server-proxy lets a service started inside a
# notebook (a TensorBoard, a Gradio demo) be reached through the same port,
# which matters because only 8888 is forwarded out of this container.
RUN pip install --no-cache-dir \
        jupyterlab notebook jupyterlab-widgets ipywidgets \
        jupyter-server-proxy jupyterlab-git jupytext nbconvert nbformat

# Deep learning. torch is already in base (cu126); this adds the rest of the
# stack around it. Same index so the vision/audio wheels match that CUDA build
# instead of pulling a second, conflicting torch.
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cu126 \
        torchvision torchaudio

RUN pip install --no-cache-dir \
        transformers datasets accelerate peft trl safetensors sentencepiece \
        tokenizers evaluate timm einops

# Experiment tracking and tuning — what turns a script into an experiment.
RUN pip install --no-cache-dir \
        tensorboard wandb mlflow optuna

# Numerics, data and plotting beyond what base carries. base has
# numpy/pandas/scipy/scikit-learn/matplotlib/seaborn/lightgbm/xgboost; these are
# the ones a workstation reaches for that a tool-runner does not.
RUN pip install --no-cache-dir \
        statsmodels sympy networkx numba \
        polars pyarrow duckdb h5py zarr xarray netCDF4 \
        plotly altair bokeh holoviews datashader \
        scikit-image opencv-python-headless pillow \
        tqdm rich pyyaml openpyxl xlsxwriter

# Paper-writing and reproducibility helpers: bibliography handling, unit-aware
# numbers, and the table/figure formatting a manuscript needs.
RUN pip install --no-cache-dir \
        pybtex bibtexparser pint uncertainties tabulate

COPY docker/science/entrypoint-science.sh /usr/local/bin/entrypoint-science
RUN chmod +x /usr/local/bin/entrypoint-science

EXPOSE 8888
ENTRYPOINT ["/usr/local/bin/entrypoint-science"]
