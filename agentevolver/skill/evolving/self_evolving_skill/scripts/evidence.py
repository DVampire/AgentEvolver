"""List compact completed-call receipts from this session's trace; never certify a pass."""
import argparse
from collections import deque
import json
from pathlib import Path
import re


def receipts(path, *, name=None, after_call=None, limit=20):
    """Stream completed actions, tolerating an incomplete live trailing line.

    Null step numbers are legitimate lifecycle data. Completion order, not a
    guessed step or sorted call ID, determines evidence chronology.
    """
    if limit < 1:
        raise ValueError("limit must be positive")
    rows = deque(maxlen=limit)
    found = after_call is None
    with Path(path).open() as handle:
        for number, line in enumerate(handle, 1):
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                if not line.endswith("\n"):
                    break
                raise ValueError(f"Invalid trace JSON at line {number}") from None
            if not isinstance(event, dict) or event.get("event_type") not in ("tool_call", "skill_call"):
                continue
            meta = event.get("metadata") or {}
            call_id = meta.get("call_id")
            if not call_id:
                continue
            if call_id == after_call:
                found = True
            if not found or (name and event.get("action_name") != name):
                continue
            message = str(event.get("error") or event.get("output") or event.get("message") or "")
            row = {key: event.get(key) for key in (
                "seq_no", "step_number", "action_type", "action_name", "success", "duration_ms")}
            row.update(call_id=call_id, summary=message[:400])
            # Historical Bash traces carry the exit status in their final footer.
            # Inspect only that footer, never a traceback or quoted log in stdout.
            if event.get("action_name") == "bash_tool":
                body, marker, archive = message.rpartition("\n\n[📄 full output archived at ")
                if marker and archive.endswith("]"):
                    row["archive_path"] = archive[:-1]
                    code = re.search(r"(?:Exit code: |Command completed with exit code: )(-?\d+)\s*$", body)
                    if code:
                        row["shell_exit_code"] = int(code[1])
                        if row["shell_exit_code"] != 0:
                            row["diagnostic"] = body.rsplit("STDERR:\n", 1)[-1][-600:]
            rows.append(row)
    if not found:
        raise ValueError(f"No completed call {after_call!r} in this trace; check the session and call ID")
    return list(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("trace", type=Path, help="Current session log/trace/<session>.jsonl")
    parser.add_argument("--name", help="Exact native action name")
    parser.add_argument("--after-call", help="Include this completed call and later receipts")
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args()
    try:
        rows = receipts(args.trace, name=args.name, after_call=args.after_call, limit=args.limit)
        print(json.dumps({"calls": rows, "note": "Transport success is not acceptance; inspect outputs and shell exit codes."}, indent=2))
    except (OSError, ValueError) as error:
        parser.exit(1, f"Cannot read evidence: {error}\n")


if __name__ == "__main__":
    main()
