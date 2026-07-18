# code-interpreter image

Adds a plain R runtime (`r-base-core`) on top of `opensandbox/code-interpreter:v1.1.0`,
so the existing `code_interpreter` sandbox (`agentevolver/sandbox/default/code_interpreter.py`)
can also run `language="r"`.

No registry/CI pipeline for this image yet — build it locally:

```bash
docker build -t agentevolver/code-interpreter:latest docker/code-interpreter
```

Smoke-test:

```bash
docker run --rm agentevolver/code-interpreter:latest Rscript -e 'cat(R.version.string, "\n")'
```

## Why R runs differently from the other six languages here

`code_interpreter`'s Python/Bash/JS/TS/Go/Java support is a persistent Jupyter-kernel
protocol, and the language dispatch for that path is enforced **server-side** in the
vendor's `execd` binary to a fixed set of six languages — adding a seventh means
forking and rebuilding that binary, which isn't worth it here.

Instead, `CodeInterpreterSandbox.run_code()` special-cases `language in ("r", "rscript")`
to go through the generic shell-exec path (`OpenSandbox.run_command`/`write_file`,
the same mechanism `deploy_tool` already uses against plain images) instead of the
Jupyter kernel: it writes the code to a temp `.R` file and runs it with `Rscript`,
one fresh process per call. That means **no cross-call variable persistence for R**
(unlike the other six languages, which share a persistent kernel per session) — every
call starts clean. Add R packages to this Dockerfile as needed for your use case.
