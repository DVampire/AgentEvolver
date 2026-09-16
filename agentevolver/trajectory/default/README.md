---
name: trajectory_default
description: "Provides standard training-data projections, currently including the VERL export format. Formats transform normalized Trajectory records without altering captured evidence."
version: 1.0.0
type: collection
category: trajectory
requirements: []
metadata: {}
---
# Built-in trajectory formats

Provides standard training-data projections, currently including the VERL export format.
Formats transform normalized Trajectory records without altering captured evidence.

`VerlFormat` is a text-level adapter: one record per trajectory step with prompt,
assistant response, reward, and source provenance. Token ids and response masks are empty
by design because only a tokenizer-aware training provider can create them correctly.
Code that consumes this format must annotate those fields before training rather than
treating empty arrays as a valid tokenized episode.
