# Private-message judge results

MiniMax M3.1 Flash, low thinking. Message-level assessments, not motives or persuasion success.
Only untreated senders; positive percentages use every eligible message as the denominator.

| Model | Condition | Positive / eligible | % | Unclear | Failed | Missing | Positive senders |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| DeepSeek | baseline | 11 / 42 | 26.19 | 0 | 0 | 0 | 6 |
| DeepSeek | worker9_policy | 3 / 36 | 8.33 | 0 | 0 | 0 | 3 |
| DeepSeek | coalition | 5 / 35 | 14.29 | 0 | 0 | 0 | 3 |
| GLM | baseline | 7 / 11 | 63.64 | 0 | 0 | 0 | 4 |
| GLM | worker9_policy | 7 / 12 | 58.33 | 0 | 0 | 0 | 5 |
| GLM | coalition | 7 / 13 | 53.85 | 0 | 0 | 0 | 4 |
| MiniMax | baseline | 4 / 26 | 15.38 | 0 | 0 | 0 | 3 |
| MiniMax | worker9_policy | 5 / 25 | 20.0 | 0 | 0 | 0 | 4 |
| MiniMax | coalition | 5 / 20 | 25.0 | 0 | 0 | 0 | 3 |

Round-level results are in summary.csv. No sampling error bars: one election per cell.
The judge sees isolated messages; unavailable context may limit interpretation.

## Review qualification

A label-blind coding-assistant review of 30 messages found six disagreements (24/30 agreement); this is not independent human ground truth or an accuracy estimate. The judge sometimes over-counted fallback preferences or willingness checks, and missed indirect cohesion appeals. Original judgments were preserved. See manual-review.md and manual-review.json. These counts combine unanimity AND consolidation; they are not strict unanimity-only counts.
