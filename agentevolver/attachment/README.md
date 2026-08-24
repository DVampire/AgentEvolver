---
name: attachment
description: "Pins the bytes of an image the agent read and re-attaches it to every later request, so an image enters the model's context and stays there instead of vanishing with the step that read it."
version: 1.0.0
type: module
category: infrastructure
requirements: []
metadata: {}
---
# Attachment

Pins the bytes of an image the agent read and re-attaches it to every later request, so an
image enters the model's context and stays there instead of vanishing with the step that
read it.

| Path | Responsibility |
|---|---|
| `types.py` | `ImageAttachment`, and the file signatures that decide what counts as an image |
| `server.py` | `attachment_manager` — admits an image, stores it content-addressed, holds the live set, renders the message part |

## Why it exists

A tool result in this framework is a string. `Response.message` is `str`, the agent loop
takes `.message` and drops the rest, and `ToolMessage.content` is `str` again — so there is
no way for a tool to hand the model a picture. The message layer has been able to *carry*
one since the beginning: `ContentPartImage` exists and all five provider serializers
already turn it into that provider's image block. The missing piece was never the format.
It was somewhere to put the image between the tool call that read it and the request that
sends it.

That gap has two halves, and only one of them is obvious.

The first is delivery: `read_image_tool` commits the bytes here, and the agent appends the
live set to the turn it is about to send. That is the same route `BrowserAgent` already
takes for screenshots — it appends `ContentPartImage` onto the user message it is
building — generalised so that any tool can use it.

The second is persistence, and it is the reason this is a module rather than three lines in
the tool. This framework rebuilds the entire prompt from memory on every step. An image
appended to one request is simply not in the next one. So an attachment is not a
send-and-forget; it is state the run holds until it drops out of the budget, and something
has to own that state, bound it, and re-encode it each turn.

## Why not `spill/`

Spill parks an oversized tool result and hands back a locator, which sounds close enough.
It is not, on three counts, and each one matters here:

- **It stores text.** `save_text` takes a `str` and the local store opens the file in text
  mode. Base64 through a field documented as "the full text to save" would be a lie about
  what is in the file, and it doubles the size of something already large.
- **It is best-effort.** `spill.save_text` returns `None` when the store fails,
  deliberately: losing a transcript is better than failing a command that already ran. An
  attachment cannot degrade that way. Reporting an image as attached when the bytes were
  never written puts a reference in front of the model to something that is not there, and
  the failure surfaces one call later with nothing pointing back.
- **It has no read side.** A `SpillRef` is documented as opaque — "consumers print it and
  never parse it" — and there is no `load`. An attachment exists to be read back, once per
  step, for as long as it is live.

The path key is the part spill got right and this reuses: `P.ATTACHMENTS` sits beside
`P.SPILL` under `output/.runtime/`, machine-level for the same reason. The owner- and
session-scoped roots need an `owner=` or `session_id=` a tool context does not carry.

## Admission

The file signature decides the media type; the extension is never trusted. A `.png` full of
JPEG bytes is otherwise rejected by the provider, in an error that names neither the file
nor the tool that read it. PNG, JPEG, GIF and WebP are admitted — the intersection of what
the five serializers here can produce.

Two bounds apply. `MAX_IMAGE_BYTES` (5 MB) is the smallest per-image limit among those
providers, so refusing above it converts a failed call into a sentence the model can act
on. `MAX_LIVE_IMAGES` (4) bounds the live set, because every live attachment is re-encoded
into every later request — an unbounded set is an unbounded, permanent surcharge on each
call. The oldest is dropped: the usual reason to read a second image is that the first one
answered its question.

Storage is content-addressed by SHA-256, so reading the same file twice costs one copy and
one live entry.

## Known Limitations and Deferred Work

- The live set is in memory, keyed by the run's context id. It does not survive a process
  restart, so a resumed run loses the images it was looking at while keeping the stored
  bytes. Making it durable needs a session-scoped record, which needs a session id on the
  tool context — see `_AMBIENT_CONTEXT_KEYS` in `protocol/server.py`, which is where that
  decision lives.
- Nothing sweeps `output/.runtime/attachments`. It grows with distinct images read, at the
  same rate and with the same absence of a retention policy as `spill/`.
- Image dimensions are not recorded. Nothing here consumes them: the serializers take the
  encoded bytes, and no decoder is a dependency of this repo.
