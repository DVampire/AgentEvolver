"""Portable compaction must respect one aggregate input budget.

Long traces can contain hundreds of individually small records.  The packer therefore
budgets separators and records together instead of applying a per-record minimum that
silently expands the request beyond the configured ceiling.
"""

import pytest

from agentevolver.memory.default.tiered import TieredMemory


def test_summary_refuses_to_omit_records_to_fit_the_budget():
    memory = TieredMemory(compact_input_tokens=128)
    items = [f"[source_seq={index}] " + ("x" * 300) for index in range(500)]

    with pytest.raises(ValueError, match="No history was omitted"):
        memory._pack_summary_items(items)
    assert len(items) == 500


def test_summary_preserves_every_character_when_source_fits():
    memory = TieredMemory(compact_input_tokens=128)
    items = ["first\n完整内容", "second\nimportant middle\nlast"]
    assert memory._pack_summary_items(items) == items
