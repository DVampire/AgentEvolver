---
name: text_stats_skill
description: Computes basic statistics (word count, character count, line count) and an estimated reading time for a piece of text or a text file. Use when an agent needs a quick, deterministic summary of a text's size before further processing or reporting.
type: worker
version: 1.0.2
---

# text_stats_skill

Compute basic, deterministic statistics for a piece of text or a text file, including an estimated reading time.

## Instructions

1. Obtain the input text (either passed inline or read from a file).
2. Compute the **word count**: split the text on whitespace and count the resulting tokens.
3. Compute the **character count**: count all characters, including whitespace.
4. Compute the **line count**: count the number of lines (newline-separated).
5. Estimate the **reading time**: divide the word count by 200 (average reading speed of ~200 words per minute) and round the result to one decimal place. Report it in minutes.
6. Report the four metrics using the Output Format below.

## Workflow

1. Read or receive the text.
2. Calculate words, characters, and lines.
3. Calculate reading time = round(words / 200, 1) minutes.
4. Emit the result in the specified format.

## Output Format

```
words: <int>
characters: <int>
lines: <int>
reading_time: <float> min
```

## Example

Given a 400-word text spanning 2500 characters across 30 lines:

```
words: 400
characters: 2500
lines: 30
reading_time: 2.0 min
```

(reading_time = round(400 / 200, 1) = 2.0 minutes)
