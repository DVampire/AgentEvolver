"""Portable compaction must respect one aggregate input budget.

Long traces can contain hundreds of individually small records.  The packer therefore
budgets separators and records together instead of applying a per-record minimum that
silently expands the request beyond the configured ceiling.
"""

from agentevolver.memory.default.tiered import TieredMemory


def test_summary_item_packing_obeys_the_total_budget_for_many_records():
    memory = TieredMemory(compact_input_tokens=128)
    items = [f"[source_seq={index}] " + ("x" * 300) for index in range(500)]

    packed = memory._pack_summary_items(items)

    assert len(packed) == len(items)
    assert len("\n".join(packed)) <= 128 * 4
