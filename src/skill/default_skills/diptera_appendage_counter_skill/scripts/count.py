"""Diptera appendage counter — compute total prolegs and parapodia for larval collections."""

import argparse
import json
import sys
from pathlib import Path

RESOURCES_DIR = Path(__file__).resolve().parent.parent / "resources"


class AppendageCounter:
    def __init__(self, resource_path: Path | None = None):
        self._resource_path = resource_path or RESOURCES_DIR / "appendages.json"
        self._data = self._load_resource()

    def _load_resource(self) -> dict:
        try:
            with open(self._resource_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            print(f"Warning: resource file not found at {self._resource_path}.", file=sys.stderr)
            return {"families": {}, "aliases": {}}

    def _resolve(self, family: str) -> str | None:
        """Resolve alias to canonical family name."""
        if family in self._data["families"]:
            return family
        aliases = self._data.get("aliases", {})
        if family in aliases:
            return aliases[family]
        # Case-insensitive partial match
        lower = family.lower()
        for canonical in self._data["families"]:
            if lower in canonical.lower() or canonical.lower() in lower:
                return canonical
        return None

    def appendages_per_larva(self, family: str) -> int | None:
        canonical = self._resolve(family)
        if canonical is None:
            return None
        return self._data["families"][canonical]["appendages_per_larva"]

    def compute(self, specimens: list[tuple[str, int]]) -> tuple[int, list[dict]]:
        """Return (grand_total, rows) where rows have family/count/appendages/subtotal."""
        rows = []
        grand_total = 0
        for family, count in specimens:
            apl = self.appendages_per_larva(family)
            if apl is None:
                print(f"Warning: unknown family '{family}', skipping.", file=sys.stderr)
                continue
            subtotal = count * apl
            grand_total += subtotal
            rows.append({
                "family": family,
                "count": count,
                "appendages_per_larva": apl,
                "subtotal": subtotal,
            })
        return grand_total, rows

    def list_families(self) -> None:
        for name, data in self._data["families"].items():
            apl = data["appendages_per_larva"]
            notes = data.get("notes", "")
            print(f"  {name:<35s} {apl:>3d} appendages/larva  — {notes}")

    def print_table(self, rows: list[dict], grand_total: int) -> None:
        header = f"{'Family':<35} {'Specimens':>10} {'Append./larva':>14} {'Subtotal':>10}"
        print(header)
        print("-" * len(header))
        for r in rows:
            print(f"{r['family']:<35} {r['count']:>10} {r['appendages_per_larva']:>14} {r['subtotal']:>10}")
        print("-" * len(header))
        print(f"{'TOTAL':<35} {'':>10} {'':>14} {grand_total:>10}")


def parse_family_count_args(args) -> list[tuple[str, int]]:
    """Zip --family and --count lists into pairs."""
    families = args.family or []
    counts = args.count or []
    if len(families) != len(counts):
        print("Error: number of --family and --count arguments must match.", file=sys.stderr)
        sys.exit(1)
    return list(zip(families, counts))


def main():
    parser = argparse.ArgumentParser(description="Diptera larval appendage counter")
    parser.add_argument("--family", action="append", metavar="NAME",
                        help="Family name (repeatable, paired with --count)")
    parser.add_argument("--count", action="append", type=int, metavar="N",
                        help="Specimen count (repeatable, paired with --family)")
    parser.add_argument("--validate", action="store_true",
                        help="Validate computed total against --expected")
    parser.add_argument("--expected", type=int,
                        help="Expected total for validation")
    parser.add_argument("--list-families", action="store_true",
                        help="List all known families and their appendage counts")

    args = parser.parse_args()
    counter = AppendageCounter()

    if args.list_families:
        counter.list_families()
        return

    specimens = parse_family_count_args(args)

    if not specimens:
        parser.print_help()
        return

    grand_total, rows = counter.compute(specimens)
    counter.print_table(rows, grand_total)

    if args.validate:
        if args.expected is None:
            print("Error: --validate requires --expected.", file=sys.stderr)
            sys.exit(1)
        if grand_total == args.expected:
            print(f"\nValidation passed: total {grand_total} matches expected {args.expected}.")
        else:
            print(f"\nValidation FAILED: computed {grand_total}, expected {args.expected}.", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
