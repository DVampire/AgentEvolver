# Adds a plain R runtime on top of the opensandbox code-interpreter image, so
# the "code_interpreter" sandbox (src/sandbox/default/code_interpreter.py) can
# run R code — via shell-exec (Rscript), not the Jupyter-kernel path the other
# six languages use. See docker/code-interpreter/README.md for why.

FROM opensandbox/code-interpreter:v1.1.0

RUN apt-get update && apt-get install -y --no-install-recommends \
    r-base-core \
    && rm -rf /var/lib/apt/lists/*
