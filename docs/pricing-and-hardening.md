# Pricing and Hardening Runbook

## Scope
- Actor: `markdown-rag-chunker`
- Goal: set concrete Apify Store prices and keep every run type profitable.

## Who pays what

Under pay-per-event, **the creator pays all platform usage** of users' runs (compute,
storage, data transfer, Apify Proxy), deducted from the payout:

`profit = 0.8 × event revenue − platform usage`

Only paid-plan users count; Apify covers runs by free-plan users. So the event prices
must cover the platform cost of every run type, including runs that push no chunks.

## Exact pricing to set in Apify admin UI
In Apify Console: Actor → `Publishing` → `Monetization` → `Change` → Pay per event.

| Event name | Trigger | FREE | BRONZE | SILVER | GOLD/PLATINUM/DIAMOND |
| --- | --- | --- | --- | --- | --- |
| `apify-actor-start` | Auto-charged at run start, one event per GB of memory (default 1024 MB: one event; max 4096 MB: four) | `$0.001` | `$0.001` | `$0.001` | `$0.001` |
| `apify-default-dataset-item` | Auto-charged per pushed dataset item — one item per chunk | `$0.0008` | `$0.0008` | `$0.0007` | `$0.0006` |

Users see **$0.80 per 1,000 chunks + $0.001 per run** at the FREE/BRONZE tier.
Leave the "Pay per event + usage" option off: it hurts the Actor's quality score.

### Rules for synthetic events
- Both events are charged by the platform; the code never calls `Actor.charge()`, and the
  SDK rejects manual charges of names starting with `apify-`. Never add custom events.
- `apify-actor-start` may be priced above its `$0.00005` default; Apify still waives the
  first 5 s of compute while the event exists. **Never delete it** (that forfeits the
  waiver).
- `apify-default-dataset-item` cannot be renamed. It is the per-chunk revenue event.
- If a `document-chunked` event exists from an old configuration, delete it.

## Measured platform cost (2026-10-20, build 1.3.3, 1024 MB)

Measured while the Actor was still FREE, so the 5 s compute waiver (worth about
`$0.0003` per run at 1 GB) is **not** subtracted below.

| Run type | Platform cost |
| --- | --- |
| Small text run, 2–7 s | `$0.00031–0.00052` |
| Typical web page, 10–14 s, 33–237 chunks | `$0.00039–0.0021` |
| Zero-chunk run: Skip if unchanged hit (1.9 s) | `$0.00021` |
| Zero-chunk run: failed input or 404 (3–9 s) | `$0.00020–0.00061` |
| Per pushed chunk (dataset write) | `$0.000005` |
| 300-page PDF, 219 s, 738 chunks | `$0.0117` |
| Ceiling: 600 s default timeout at 1 GB | `~$0.035` |

At 256 MB the same PDF took 825 s (CPU scales with memory, ~1 core per 4 GB), so
1024 MB costs about the same compute for CPU-bound work and finishes ~4× faster.
For pricing, assume **1.5× the measured costs**: `$0.0009` for the worst zero-chunk
run, `$0.0025` for a heavy run (60 s at 1 GB).

## Margin check (Gold tier, the lowest per-chunk price)

| Run | Creator revenue | Cost (measured, no waiver) | Profit |
| --- | --- | --- | --- |
| 0 chunks, Skip if unchanged hit | `$0.0008` | `$0.00021` | `+$0.00059` |
| 0 chunks, failed input (worst measured) | `$0.0008` | `$0.00061` | `+$0.00019` |
| 2 chunks, small text | `$0.00176` | `$0.00052` | `+$0.0012` |
| 197 chunks, Giant panda page | `$0.0954` | `$0.0016` | `+$0.094` |
| 738 chunks, 300-page PDF | `$0.355` | `$0.0117` | `+$0.343` |

With the 5 s waiver active under pay-per-event, the zero-chunk margin grows by about
`$0.0003`. The only loss case is a run that converts for minutes and then fails
(e.g. `max_chunks` exceeded), capped by the 600 s timeout at about `$0.035`.

The original plan (start event at the `$0.00005` default) lost money on every
zero-chunk run: one user checking 100 documents hourly with Skip if unchanged
(72k runs/month) would have cost the creator `~$22/month`. At `$0.001` the same
user earns `~$42/month`.

## Change-detection cache economics

`skip_if_unchanged` keeps one record per source (`document_hash`, `source_hash`,
split-config fingerprint, `chunk_count`, `input_chars`, `input_file_type`) in a named
key-value store created inside the run, so it belongs to the **run's user**.

Since v1.3, a remote file whose raw bytes and content type are unchanged skips
conversion entirely: a cache hit costs about as much as the download. Pages whose
bytes change per fetch (e.g. dynamic HTML) fall back to comparing the converted
Markdown hash, which still skips splitting and pushing.

## Proxy costs

- `proxy_url`: the user's own proxy, no Apify cost.
- `use_apify_proxy`: Apify Proxy (datacenter group) is platform usage paid by the
  creator. Downloads are streamed with a hard 25 MB limit on the decompressed size, which
  bounds the traffic per run; datacenter proxy traffic showed no separate usage line in
  the 2026-10 test runs.

## Runtime hardening settings
- `defaultMemoryMbytes`: `1024` (CPU scales with memory, ~1 core per 4 GB; at 256 MB a 300-page PDF converted for 825 s)
- `minMemoryMbytes`: `256`
- `maxMemoryMbytes`: `4096`
- Console default run timeout: `600` s
- `max_input_chars` default: `2000000`
- `max_chunks` default: `5000`

## Rollout checklist
1. Deploy the build whose README states these prices, then verify it end to end.
2. Publish the event prices above (price changes are then locked for 15 days).
3. Monitor 7–14 days: chunks per run, cost per run, zero-chunk run share, proxy usage,
   failures by `input_mode`.
4. Adjust once if measured margins differ materially.
