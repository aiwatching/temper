# Extraction quality: forti-k2 vs claude-haiku-4-5

Measured 2026-05-13 against the 8-sentence corpus in
`scripts/test/extract.py`. Both runs used Ollama (`nomic-embed-text`)
for embeddings; only the extraction LLM differed. Same FalkorDB
namespace, episodes cleaned up between runs.

Test harness:

    # forti-k2 (current default)
    uvicorn memory_service.main:app ...    # with default .env
    python3 scripts/test/extract.py --json --cleanup > k2.json

    # claude-haiku-4-5
    LLM_PROVIDER=anthropic \
    LLM_API_KEY=sk-ant-oat01-... \
    LLM_MODEL=claude-haiku-4-5 \
    LLM_BASE_URL= \
      uvicorn memory_service.main:app ...
    python3 scripts/test/extract.py --json --cleanup > claude.json

## Headline numbers

|                              | forti-k2 (Qwen) | claude-haiku-4-5 |
|------------------------------|----------------:|------------------:|
| Total entities (8 sentences) |              18 |                13 |
| **Total facts**              |           **8** |            **15** |
| Avg latency / sentence       |          2.8s   |          4.8s     |
| Zero-fact sentences          |           4 / 8 |             4 / 8 |

Same zero-fact rate (50%) because the same four sentences have no
relations to extract — "Hi.", "Who is my English teacher?",
"My English teacher is Yalena." (one-entity, no relation), and
"Last week Yalena was my Spanish teacher, but now she teaches me
English." — which surprisingly tripped both.

## Where Claude wins

**Sentence 6** — "Jerry switched English teachers; the new one is Sarah
from Toronto":

- forti-k2 (2 facts): `Jerry switched English teachers; the new one is
  Sarah.`, `Sarah from Toronto.`
- claude-haiku-4-5 (4 facts): `Jerry's new English teacher is Sarah`,
  `Sarah is from Toronto`, `Jerry switched English teachers; the new
  one is Sarah.`, `Sarah lives in Toronto`

forti-k2 missed the core fact that English-learning recall needs —
"Jerry's new teacher is Sarah." Claude got it cleanly.

## Where Claude annoys

**Duplicates.** Same fact emitted 3-5 times in one extraction pass:

- Sentence 4 "Sarah lives in Toronto" → Claude returned the same fact
  4 times plus one near-variant.
- Sentence 8 "Jerry uses Duolingo for vocabulary" → emitted 3 times.

Graphiti dedups at write time (same fact text + same endpoint UUIDs
→ one edge), so the FalkorDB graph ends up clean. But the
intermediate LLM calls happen anyway — part of why Claude is 1.7x
slower wallclock and uses more tokens than headline numbers suggest.

## Latency

Per-sentence latency is roughly:

|                         | k2  | claude |
|-------------------------|----:|-------:|
| short greeting          | 0.9s | 4.9s |
| single-clue (Sarah lives in Toronto) | 2.8s | 4.5s |
| multi-clue (Jerry switched...)       | 3.7s | 5.7s |
| 3-sentence paragraph                 | 7.0s | 6.4s |

Notably the long paragraph is the *only* place Claude isn't slower —
it parallelizes better when there's more to chew on. The greeting
case is dominated by Anthropic's network RTT.

## Recommendation

For **relation quality**, Claude is clearly better — it caught the
"who is whose teacher" relation that k2 missed, which is the exact
kind of fact `/v1/search` recall depends on.

For **operational fit on the Fortinet network**, forti-k2 stays:

- 1.7x faster end-to-end
- no external egress (gateway is on-prem)
- no token / rate-limit headaches

Concrete tradeoff guidance:

- If you're building agent UX where recall accuracy is the bottleneck
  → swap to claude-haiku-4-5 (or better, claude-sonnet-4-6 with a real
  `sk-ant-api03-...` key once you've got one).
- If you're storing high-volume agent logs where most episodes never
  get queried → stay with forti-k2; the 1.7x speed advantage adds up,
  and the dedup catches up.

## Caveats

- Only 8 sentences. Real corpora (chat transcripts, meeting notes,
  ticket text) would shift the numbers — k2 might do better on
  technical text, Claude might do worse on long noisy text.
- The OAuth token used (`sk-ant-oat01-…`, Claude Code subscription)
  is rate-limited to claude-haiku-4-5. Opus/Sonnet returned 429.
  A real API key (`sk-ant-api03-…`) on Anthropic Direct would unlock
  the higher tiers.
- forti-k2 occasionally drops the closing punctuation in extracted
  facts; Claude is more consistent on formatting.

## Rerunning

```bash
# Stop the existing service, then:
LLM_PROVIDER=<one>  LLM_API_KEY=...  LLM_MODEL=...  LLM_BASE_URL=... \
  uvicorn memory_service.main:app --port 8000 ...
python3 scripts/test/extract.py --json --cleanup > /tmp/extract-<one>.json
```

Then diff the JSON files for per-sentence comparison. The two
existing files (`/tmp/extract-k2.json` and `/tmp/extract-claude.json`)
are not committed but easy to reproduce.
