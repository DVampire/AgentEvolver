"""What a terminal displays, recovered from the bytes a program wrote to it.

A terminal is not a pipe with a flag set: the bytes a full-screen program writes are
instructions to a device — move here, set this colour, erase to end of line — and what a
person sees is the screen those instructions leave behind. Handing a caller the raw stream
hands over the wire protocol instead of the page.

This lives here rather than beside the local shell tool because it is a pure function of
the bytes: it does not know, and must not know, whether they came from a pty on this
machine, an `ssh -tt` to another one, or a container exec. Every transport that can hand
over a byte stream gets the same rendering, and there is one implementation to fix when it
is wrong — which it has been, repeatedly, in ways that were only visible on real programs.
"""

#: Terminal size reported to a program running under `tty: true`. A full-screen program
#: asks for this and lays itself out accordingly; zero would make many of them misbehave
#: or refuse to start, which would look like a defect in the program rather than in how it
#: was run.
PTY_ROWS, PTY_COLS = 24, 80

#: TERM given to a `tty: true` command when the environment does not set one. A terminal
#: device alone is not enough for anything built on curses: it also looks TERM up in the
#: terminfo database, and with no TERM it reports "Error opening terminal: unknown" and
#: exits — the same refusal as having no terminal at all, which is the thing `tty` exists
#: to get past. `xterm` because it is present in essentially every image.
#:
#: Overridable, and deliberately: how a program behaves under a different TERM — `dumb`,
#: `vt100`, one that does not exist — is itself a behaviour worth comparing. Prefix the
#: command with `TERM=… ` to choose.
PTY_DEFAULT_TERM = "xterm"

#: The sequences that make content disappear: erase-display in its several forms, and
#: leaving the alternate screen. Nothing else loses what is on screen — a program that
#: overwrites in place still has its characters there — so these are the only points worth
#: snapshotting before, and slicing by byte count instead misses a program whose whole
#: output is shorter than one slice.
_ERASE_SEQUENCES = (b"\x1b[2J", b"\x1b[3J", b"\x1b[J", b"\x1b[?1049l", b"\x1b[?47l", b"\x1b[?1047l")

#: How long a `tty: true` command is left alone before its keystrokes are sent. Keys are
#: input to a program that is ready for input, and a full-screen program is not ready until
#: it has drawn: sending `q` at once quits it before it paints, and the screen that comes
#: back is empty — which reads as "this program displays nothing". Observed exactly that,
#: on a program that had 34,420 bytes of drawing to show. Capped against short timeouts so
#: the keys are always actually sent.
PTY_KEYSTROKE_DELAY = 1.0


def _offsets_of(data: bytes, sequence: bytes) -> list:
    """Every position at which `sequence` starts in `data`."""
    found, start = [], data.find(sequence)
    while start != -1:
        found.append(start)
        start = data.find(sequence, start + 1)
    return found


def render_terminal(data: bytes) -> str:
    """Interpret a pty byte stream the way a terminal would and return what it displays.

    A terminal is not a pipe with a flag set: the bytes a full-screen program writes are
    instructions to a device — move here, set this colour, erase to end of line — and the
    thing a person sees is the screen those instructions leave behind. Handing the raw
    stream to a caller hands over the wire protocol instead of the page. Measured on one
    task: a screen holding roughly 500 visible characters arrived as 32,184 bytes of escape
    sequences, which exhausted the output budget and was skimmed past as noise.

    Colour and boldness survive as a summary line rather than being dropped, because for a
    program whose whole job is how it draws, "it is green and bold" is the observation.

    The stream is replayed with a snapshot before every point where content can disappear,
    and the fullest frame wins over the last one — because the last one is routinely near
    empty on purpose. A curses program's exit path clears the screen and hands the terminal
    back the way it found it, and what remains is a line or two from the shell. Reporting
    the end state therefore says "this program displays nothing" about a program that
    displayed plenty, which is worse than the raw bytes: it looks like an answer.
    """
    import pyte

    screen = pyte.HistoryScreen(PTY_COLS, PTY_ROWS, history=200)
    stream = pyte.ByteStream(screen)

    def row_text(row) -> str:
        return "".join(row[x].data for x in range(PTY_COLS)).rstrip()

    def styles_on_screen() -> list:
        """Distinct styles in first-seen order — a handful describes, a hundred is noise."""
        found: list = []
        for row in list(screen.history.top) + [screen.buffer[y] for y in range(PTY_ROWS)]:
            for x in range(PTY_COLS):
                char = row[x]
                if not char.data.strip():
                    continue
                name = char.fg if char.fg != "default" else ""
                if char.bg != "default":
                    name = f"{name or 'default'} on {char.bg}"
                if char.bold:
                    name = f"{name or 'default'} bold"
                if name and name not in found:
                    found.append(name)
        return found

    def frame() -> tuple:
        return ([row_text(row) for row in screen.history.top]
                + [line.rstrip() for line in screen.display], styles_on_screen())

    cuts = sorted({
        offset
        for sequence in _ERASE_SEQUENCES
        for offset in _offsets_of(data, sequence)
    })

    def filled(lines: list) -> int:
        return sum(1 for line in lines if line)

    drawn = None
    previous = 0
    for cut in cuts + [len(data)]:
        stream.feed(data[previous:cut])
        previous = cut
        current = frame()
        if drawn is None or filled(current[0]) > filled(drawn[0]):
            drawn = current

    lines, styles = frame()
    # Fullest frame beats last frame, rather than only rescuing an entirely blank one: what
    # a program drew is routinely followed by a line or two on the restored screen — a shell
    # prompt, an `echo exit=$?` — and one such line was enough to make a screenful of
    # drawing look like it never happened.
    cleared = drawn is not None and filled(drawn[0]) > filled(lines)
    if cleared:
        remained = [line for line in lines if line and line not in drawn[0]]
        lines, styles = drawn[0] + remained, drawn[1]
    while lines and not lines[-1]:
        lines.pop()

    note = f"[terminal {PTY_COLS}x{PTY_ROWS}"
    if styles:
        shown = ", ".join(styles[:6]) + (", …" if len(styles) > 6 else "")
        note += f" · {shown}"
    note += f" · {len(data):,} bytes interpreted"
    if cleared:
        note += " · the screen is as it appeared while running, since the program cleared it on exit"
    note += "]"
    return "\n".join(lines + ["", note])


__all__ = [
    "render_terminal",
    "PTY_ROWS",
    "PTY_COLS",
    "PTY_DEFAULT_TERM",
    "PTY_KEYSTROKE_DELAY",
]
