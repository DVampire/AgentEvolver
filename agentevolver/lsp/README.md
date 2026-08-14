---
name: lsp
description: "Asks a language server what a symbol is — its definition, its references, its type — so an edit rests on what the code means rather than on where its name happens to appear."
version: 1.0.0
type: module
category: infrastructure
requirements: []
metadata: {}
---
# LSP

Asks a language server what a symbol is — its definition, its references, its type — so an
edit rests on what the code means rather than on where its name happens to appear.

| Path | Responsibility |
|---|---|
| `types.py` | The vocabulary: four operations, three result shapes, the error codes, and the provider contract |
| `server.py` | `lsp_manager` — routes a query to a provider by file extension, and reaps what providers started |
| `stdio.py` | The default provider: LSP framing, one child process per session and workspace, wire shapes to seam shapes |

## Why it exists

The agent finds a symbol by grepping. Grep answers a different question than the one it is
being asked: it finds the string, and the string is not the definition. `def close` matches
seven files; `close(` matches ninety; the one that matters is the method on the class the
caller actually holds, which no pattern can express. So the agent reads three of the seven,
picks one, and edits it.

The three things a safe edit needs are exactly the three a language server knows and a
regex does not: where this name is *defined*, everywhere it is *used*, and what its *type*
is. Without them the agent is guessing with good evidence, and the failure mode is not a
crash — it is an edit to the wrong `close`, which passes review because the diff looks
right.

## The seam

A tool never names a provider, a command, or a language server. It asks `lsp_manager` for
a definition in a file; the manager decides that `.py` routes to the Python provider and
that the language id is `python`, and the provider owns the subprocess.

That indirection is not tidiness. The model-visible schema is generated from the tool, and
the tool is generated from nothing but the four operations — so a Go server registered
tomorrow changes no description, no parameter, and no tool name. The prompt prefix is
unchanged, and the cached prefix survives. A capability that appears and disappears with
the machine it runs on would move a tool definition in and out of every request, and
invalidate the cache from the first changed token onwards.

For the same reason the tool never disappears. With no provider registered, a query is
answered — with `LSP_UNAVAILABLE`, naming what is missing, how to install it, and what to
use instead. An agent told "the tool is gone" learns nothing; an agent told "no language
server handles .rs; use grep_search_tool" carries on.

## The contract

- **Four operations, no passthrough.** `definition`, `references`, `hover`, `symbols`. There
  is no "send this JSON-RPC method", because `workspace/executeCommand` runs code and edits
  files, and it would reach the model through a tool that declares `mutates=False`. For the
  same reason the client refuses `workspace/applyEdit` when a server asks for it.
- **References always include the declaration.** A caller weighing a rename wants every
  site. A flag would let it ask for an answer that omits the one site it is about to change.
- **The document is opened for one query and closed in a `finally`.** Keeping it open would
  be faster and would require this module to know about every write every other tool makes.
  It does not, so it re-reads.
- **Positions are the protocol's — zero-based, UTF-16.** The one-based convention a person
  expects is converted once, at the tool. A conversion per provider is a per-language
  off-by-one.
- **A server is owned, or it is a leak.** Every process is registered under
  `(session, workspace)`, and `forget(session_id)` runs at the end of a run from
  `Agent._release_session_resources`, beside jobs and terminals. `atexit` is the last
  resort, not the plan: in a gateway that stays up for a week it never fires.
- **An idle server may be evicted; a busy one may not.** This is where the rule differs
  from `terminal/`, deliberately. A terminal holds state the agent put there, so the cap
  refuses rather than evicts. A language server holds only an index it can rebuild, so
  closing the least recently used idle one costs the next query its warm-up and loses
  nothing. One with a query in flight is never touched — a caller is waiting on it.
- **A structured code, not a message to parse.** Every failure carries an `LspErrorCode`.
  `LSP_UNAVAILABLE` means nothing can answer; `LSP_UNSUPPORTED_OPERATION` means the server
  can be reached but does not do this; `LSP_MALFORMED_RESPONSE` means it answered
  something this client will not guess at. They lead to different next moves.

## The server it ships with

One: `pylsp` (`pip install python-lsp-server`), for `.py` and `.pyi`. It installs into the
interpreter the framework already runs on, needs no node or rust toolchain, and is built on
`jedi`, which is already a dependency here. It is registered lazily on the first query, and
only if it is on PATH — looking for an executable is not something importing a module
should do, and a process that never queries should never own a language server.

Any other server is a `StdioLspProvider` with a command and an extension table, registered
by whoever configures it. This module is a host, not a catalog: a list of language servers
that nobody keeps current is a list of confident wrong answers.

## What it is not

Not a replacement for search. Grep answers "where does this text appear", which is the
right question most of the time and costs no subprocess. This is for the moment before a
change, when the text is ambiguous and the answer has to be the real one.

Not a way to edit. Rename, code actions, and formatting are mutations, and they need
preview, permission, and the write path — a different tool, not another operation on this
one.

Not a promise about coverage. A server may answer with nothing because it is still
indexing, because the position is not on a symbol, or because it does not know the symbol.
An empty result means the server had nothing to say, and never that nothing is there.
