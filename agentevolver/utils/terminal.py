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

#: How many lines of scrolled-off output a terminal that outlives one call keeps. What a
#: persistent shell showed three commands ago is routinely the thing worth going back to,
#: and the screen itself holds only 24 lines.
PTY_SCROLLBACK_LINES = 1000

#: How long a `tty: true` command is left alone before its keystrokes are sent. Keys are
#: input to a program that is ready for input, and a full-screen program is not ready until
#: it has drawn: sending `q` at once quits it before it paints, and the screen that comes
#: back is empty — which reads as "this program displays nothing". Observed exactly that,
#: on a program that had 34,420 bytes of drawing to show. Capped against short timeouts so
#: the keys are always actually sent.
PTY_KEYSTROKE_DELAY = 1.0


def _row_to_text(row, cols: int) -> str:
    """One emulator row as the characters standing on it."""
    return "".join(row[x].data for x in range(cols)).rstrip()


def open_pty() -> tuple:
    """Open a pseudo-terminal pair sized the way a real one is. Returns (master, slave).

    The size is the whole reason this is not a bare `pty.openpty()`: a program that asks
    the terminal how big it is and hears 0x0 either lays itself out into nothing or
    refuses to start, and that reads as a defect in the program rather than in how it was
    handed its terminal. A size that cannot be set is not worth failing over — the
    terminal still works, the program just picks its own default.
    """
    import fcntl
    import pty
    import struct
    import termios

    master, slave = pty.openpty()
    try:
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", PTY_ROWS, PTY_COLS, 0, 0))
    except OSError:
        pass
    return master, slave


class LiveScreen:
    """A terminal screen that stays open, fed byte by byte as a program writes.

    `render_terminal` below answers "what did this command display", replaying a finished
    stream and keeping the fullest frame it ever showed. That is the wrong question for a
    terminal that outlives one call: there the answer is "what is on screen *now*", and
    the fullest-frame rule would resurrect the output of a command three prompts ago every
    time the screen was cleared.

    So the emulator is kept rather than rebuilt. Feeding it incrementally is also what
    makes the state correct at all — a shell's screen is the product of every byte since
    it started, and re-rendering only the newest chunk would lose the cursor position,
    the scroll region, and anything drawn before it.

    Not thread-safe on its own; the caller feeding it from a reader thread owns the lock.
    """

    def __init__(self, rows: int = PTY_ROWS, cols: int = PTY_COLS,
                 scrollback: int = PTY_SCROLLBACK_LINES) -> None:
        import pyte

        self.rows, self.cols = rows, cols
        self._screen = pyte.HistoryScreen(cols, rows, history=scrollback)
        self._stream = pyte.ByteStream(self._screen)

    def feed(self, data: bytes) -> None:
        self._stream.feed(data)

    def frame(self) -> tuple:
        """(scrolled-off lines, the 24 lines currently on screen).

        Returned apart rather than concatenated because they age differently: a line in
        history is settled and will never change again, while every line on screen can be
        overwritten by the next byte. A caller working out what is new since it last
        looked needs that distinction — the two halves are compared in different ways.
        """
        history = [_row_to_text(row, self.cols) for row in self._screen.history.top]
        display = [line.rstrip() for line in self._screen.display]
        return history, display

    def lines(self) -> list:
        """Everything the terminal holds, oldest first, with trailing blanks dropped."""
        history, display = self.frame()
        lines = history + display
        while lines and not lines[-1]:
            lines.pop()
        return lines


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
        return _row_to_text(row, PTY_COLS)

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
    "LiveScreen",
    "open_pty",
    "PTY_ROWS",
    "PTY_COLS",
    "PTY_DEFAULT_TERM",
    "PTY_KEYSTROKE_DELAY",
    "PTY_SCROLLBACK_LINES",
]
