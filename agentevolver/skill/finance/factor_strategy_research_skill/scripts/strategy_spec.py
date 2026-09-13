"""Check versioned strategy definitions without executing a policy or judging its merit."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re


def text(record, key):
    value = record.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Strategy spec requires nonempty {key}")
    return value


def strings(record, key, *, nonempty=False):
    value = record.get(key)
    if (not isinstance(value, list) or (nonempty and not value)
            or any(not isinstance(v, str) or not v.strip() for v in value)
            or len(value) != len(set(value))):
        raise ValueError(f"Strategy spec requires unique strings in {key}")
    return value


def implementation_file(record):
    if not isinstance(record, dict):
        raise ValueError("Implementation files require path and sha256")
    path = Path(text(record, "path"))
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("Implementation paths must be relative to the strategy version directory")
    if not re.fullmatch(r"[0-9a-f]{64}", text(record, "sha256")):
        raise ValueError("implementation.sha256 must be a lowercase SHA-256 digest")


def exact_id(value):
    parts = value.split("@")
    return (len(parts) == 2 and parts[1].lower() != "latest"
            and all(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", part) for part in parts))


def validate(spec, *, require_implementation=False):
    """Validate the archive contract. Numerical/causality checks remain engine-owned."""
    if not isinstance(spec, dict) or type(spec.get("schema")) is not int or spec["schema"] not in (1, 2):
        raise ValueError("Expected strategy spec schema=2 (schema=1 is readable for historical archives)")
    # Reject nonfinite JSON, including values nested in open parameter/extension structures.
    json.dumps(spec, allow_nan=False)
    for key in ("strategy_id", "version"):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", text(spec, key)):
            raise ValueError(f"Invalid strategy {key}; use a stable ID, not a display name/path")
    if spec["version"].lower() == "latest":
        raise ValueError("Strategy versions must be exact, never latest")
    if spec.get("id") != f"{spec['strategy_id']}@{spec['version']}":
        raise ValueError("Strategy id must equal strategy_id@version")
    for key in ("name", "description", "family", "hypothesis", "falsification", "created_round"):
        text(spec, key)
    parents = strings(spec, "parent_ids")
    if any(not exact_id(parent) for parent in parents):
        raise ValueError("Parent strategies require exact version IDs")
    if spec["id"] in parents:
        raise ValueError("A strategy cannot be its own parent")
    if "baseline" in spec and type(spec["baseline"]) is not bool:
        raise ValueError("baseline must be a boolean")
    if spec["schema"] == 2:
        role = spec.get("research_role")
        if role not in ("candidate", "ablation", "benchmark"):
            raise ValueError("research_role must be candidate, ablation or benchmark")
        controls = strings(spec, "control_for", nonempty=role == "ablation")
        if any(not exact_id(cid) or cid == spec["id"] for cid in controls):
            raise ValueError("control_for requires exact full-strategy IDs, never self")
        if role != "ablation" and controls:
            raise ValueError("Only ablations declare control_for")
        if "baseline" in spec and spec["baseline"] != (role != "candidate"):
            raise ValueError("baseline conflicts with research_role; omit the legacy flag")
    change = spec.get("change")
    if not isinstance(change, dict):
        raise ValueError("Strategy spec requires change metadata")
    for key in ("kind", "summary", "reason"):
        text(change, key)
    strings(change, "evidence_ids")
    if (not parents) != (change["kind"] == "initial"):
        raise ValueError("Initial strategies have no parents; derived versions need parents and a non-initial change kind")
    design = spec.get("design")
    if not isinstance(design, dict):
        raise ValueError("Strategy spec requires a design object")
    for key in ("objective", "mechanism", "combination", "fit_policy", "pseudocode"):
        text(design, key)
    for key in ("assumptions", "failure_modes"):
        strings(design, key, nonempty=True)
    rules = design.get("rules")
    if not isinstance(rules, dict):
        raise ValueError("Strategy design requires rules")
    for key in ("entry", "exit", "sizing", "rebalance", "neutral", "risk", "execution"):
        text(rules, key)
    bindings = spec.get("factor_bindings")
    if not isinstance(bindings, list) or (spec["schema"] == 1 and not bindings and not spec.get("baseline", False)):
        raise ValueError("Non-baseline strategies require factor_bindings")
    ids = []
    for binding in bindings:
        if not isinstance(binding, dict):
            raise ValueError("Each factor binding must be an object")
        for key in ("factor_id", "role", "purpose"):
            text(binding, key)
        fid = binding["factor_id"]
        if not exact_id(fid):
            raise ValueError("Factor bindings require exact version IDs, never latest")
        ids.append(fid)
    if len(set(ids)) != len(ids):
        raise ValueError("Duplicate factor binding; declare one exact role per factor version")
    if spec["schema"] == 2:
        identities = {fid.split("@")[0] for fid in ids}
        if len(identities) != len(ids):
            raise ValueError("Bind only one version of each factor identity in a policy")
        if role == "candidate" and len(identities) < 2:
            raise ValueError("A formal candidate requires at least two distinct factors; single-factor policies are controls")
    if not isinstance(spec.get("parameters"), dict):
        raise ValueError("Strategy parameters must be an object, possibly empty")
    if "implementation" not in spec:
        raise ValueError("Declare implementation as null for a proposal or a pinned file record")
    implementation = spec["implementation"]
    if implementation is None:
        if require_implementation:
            raise ValueError("An executable strategy requires a pinned implementation")
    else:
        if not isinstance(implementation, dict):
            raise ValueError("implementation must be an object or null")
        implementation_file(implementation)
        text(implementation, "entrypoint")
        dependencies = implementation.get("dependencies", [])
        if not isinstance(dependencies, list):
            raise ValueError("Implementation dependencies must be a list of pinned files")
        paths = {implementation["path"]}
        for dependency in dependencies:
            implementation_file(dependency)
            if dependency["path"] in paths:
                raise ValueError("Duplicate implementation file path")
            paths.add(dependency["path"])
    return spec


def digest(spec):
    """Content identity for a definition embedded unchanged in an evaluation JSON."""
    raw = json.dumps(spec, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def check_file(path, *, require_implementation=False):
    path = Path(path).resolve()
    spec = validate(json.loads(path.read_text()), require_implementation=require_implementation)
    if spec["implementation"] is not None:
        implementation = spec["implementation"]
        for item in [implementation, *implementation.get("dependencies", [])]:
            code = (path.parent / item["path"]).resolve()
            if hashlib.sha256(code.read_bytes()).hexdigest() != item["sha256"]:
                raise ValueError(f"Strategy implementation hash mismatch: {item['path']}")
    return {"ok": True, "id": spec["id"], "name": spec["name"], "spec_path": str(path),
            "research_role": spec.get("research_role") if spec["schema"] == 2 else "legacy_unclassified",
            "spec_sha256": digest(spec), "implementation_hashes_verified": spec["implementation"] is not None}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("spec", type=Path)
    parser.add_argument("--require-implementation", action="store_true")
    args = parser.parse_args()
    try:
        print(json.dumps(check_file(args.spec, require_implementation=args.require_implementation)))
    except (ValueError, TypeError, OSError) as error:
        parser.exit(1, f"Strategy definition is not ready: {error}\n")


if __name__ == "__main__":
    main()
