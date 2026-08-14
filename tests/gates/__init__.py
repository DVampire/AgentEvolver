"""Consistency gates.

Ordinary tests check that a unit does what it says. These check that facts duplicated
across the codebase still agree — the failure mode that produced almost every bug found
while this subsystem was built:

- six serializers each had to learn `ToolMessage`; none had, and the one path that
  produced the type was off by default, so nothing failed until it was switched on
- `TokenUsage.from_raw` had to know four spellings of the cache counters; it knew two,
  and a name it does not know is indistinguishable from a real zero
- twenty-seven templates, a stylesheet, a renderer, a splitter, and two documents all
  had to agree on the capability block layout; each was edited by hand

Every one is "N places must agree, and nothing checks that they do". A unit test does not
catch it, because each place is individually correct. What catches it is a gate that
enumerates the places from the code itself and fails when one is missing — including a
place added later, which is the part a hand-written list cannot do.

That last property is the point. Each gate below discovers its own subjects (every
`Message` subclass, every provider package, every template carrying a catalog) rather
than listing them, so adding a provider or a message type without covering it fails here
instead of in a run three weeks later.
"""
