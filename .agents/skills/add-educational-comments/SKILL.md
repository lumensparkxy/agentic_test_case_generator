---
name: add-educational-comments
description: When explicitly requested, add educational comments to specified code for the intended reader. Do not add tutorial comments during ordinary implementation or review.
---

# Educational comments

Identify the requested code and infer the reader's level from the request. If
neither a file nor a code region can be identified, ask for the missing target.

Explain the non-obvious reasoning, domain assumptions, and useful tradeoffs.
Keep comments near the code they explain. Use as many as are helpful; do not aim
for a line count, percentage increase, or fixed comment quota. Preserve encoding,
formatting, and runtime behavior. Update existing explanations instead of
repeating them, and avoid comments that merely restate the code.

Keep implementation changes outside this commenting task unless requested.
Verify the diff and language syntax if comments can affect it; do not add tests
that mirror the new prose. Follow [AGENTS.md](../../../AGENTS.md) for delivery.
