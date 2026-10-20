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
| `apify-actor-start` | Auto-charged once per run (one event per GB of memory; max memory is 512 MB, so always one) | `$0.001` | `$0.001` | `$0.001` | `$0.001` |
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

## Measured platform cost (2026-10, 21 production runs, 256 MB)

| Run type | Platform cost |
| --- | --- |
| Typical run, 6–12 s | `$0.00020–0.00039` |
| Zero-chunk run (cache hit or failed input), 2–12 s | `$0.00009–0.00028` |
| Per pushed chunk (dataset write) | `$0.000005` |
| Ceiling: 300 s run timeout | `~$0.004` (`~$0.008` at 512 MB) |

About half of a typical run's cost is fixed key-value store writes (INPUT, OUTPUT,
cache state), which the 5 s compute waiver does not cover. For pricing, assume
**1.5× the measured costs**: `$0.00035` fixed per typical run, `$0.0025` for a heavy
run (60 s at 512 MB).

## Margin check (Gold tier, the lowest per-chunk price)

| Run | Creator revenue | Cost (1.5× measured) | Profit |
| --- | --- | --- | --- |
| 0 chunks (skip hit, failed input) | `$0.0008` | `$0.00035` | `+$0.00045` |
| 1 chunk | `$0.00128` | `$0.00036` | `+$0.00092` |
| 20 chunks | `$0.0104` | `$0.0005` | `+$0.0099` |
| Heavy run, 60 s at 512 MB, 5 chunks | `$0.0032` | `$0.0025` | `+$0.0007` |

Break-even is below one chunk for normal runs. The only loss case is a heavy run
(long conversion) that produces almost no chunks, capped by the run timeout.

The original plan (start event at the `$0.00005` default) lost `~$0.0003` on every
zero-chunk run: one user checking 100 documents hourly with Skip if Unchanged
(72k runs/month) would have cost the creator `~$22/month`. At `$0.001` the same
user earns `~$32/month`.

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
  creator. Downloads are capped by the `Content-Length` guard
  (`max_input_chars × 4` bytes, 2 MB at the default), which bounds the traffic per run.

## Runtime hardening settings
- `defaultMemoryMbytes`: `256`
- `minMemoryMbytes`: `128`
- `maxMemoryMbytes`: `512`
- `max_input_chars` default: `500000`
- `max_chunks` default: `5000`

## Rollout checklist
1. Deploy the build whose README states these prices, then verify it end to end.
2. Publish the event prices above (price changes are then locked for 15 days).
3. Monitor 7–14 days: chunks per run, cost per run, zero-chunk run share, proxy usage,
   failures by `input_mode`.
4. Adjust once if measured margins differ materially.
