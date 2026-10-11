# Pricing and Hardening Runbook

## Scope
- Actor: `markdown-rag-chunker`
- Goal: set concrete Apify Store prices and keep margins positive.

## Exact pricing to set in Apify admin UI
In Apify Console, open your Actor and go to `Monetization` -> `Pay per event`.

### Event types

Only **synthetic events** (fired and named by the Apify platform) are billed. The Actor code does not call `Actor.charge()`, so there is nothing to configure beyond the synthetic event prices below.

| Event name | Type | Trigger | FREE | BRONZE | SILVER | GOLD/PLATINUM/DIAMOND |
| --- | --- | --- | --- | --- | --- | --- |
| `apify-actor-start` | Synthetic | Auto-charged at run start by Apify | default (`$0.00005`) | default | default | default |
| `apify-default-dataset-item` | Synthetic | Auto-charged on **every pushed dataset item** — since v1.1 the Actor pushes one item per chunk | `$0.0008` | `$0.0008` | `$0.0007` | `$0.0006` |

Equivalent to **$0.80 per 1,000 chunks** at the FREE/BRONZE tier — competitive against `labrat011/rag-content-chunker` ($0.50/1k, text-only) and `hedelka/pdf-to-markdown-rag` ($1.00/1k, PDF-only). Justified by the multi-format conversion this Actor offers (PDF, DOCX, XLSX, PPTX, HTML, EPUB, etc.).

> **v1.1 note:** before v1.1 the Actor pushed a single wrapped item per run, so users paid
> ~$0.0008 per *run* regardless of chunk count — undercharging vs. the published per-chunk
> price (and below cost on large runs). The per-item push in `src/main.py` makes the
> published per-chunk pricing literally true. Run metrics moved to the `OUTPUT` record
> (`Actor.set_value("OUTPUT", ...)`), which does not fire the dataset-item event.

> **Proxy billing (v1.1):** `proxy_url` (user's own proxy) is free — no Apify charges.
> `use_apify_proxy` routes the fetch through Apify Proxy, billed to the *user* as standard
> platform usage on their plan, exactly like any other Actor's proxy traffic. Neither
> option creates creator-side cost, so neither needs an event price.

### Rules for synthetic events
- `apify-actor-start`: **do not change the price**. Apify subsidises the first 5 seconds of compute for each run when this event is active. Deleting it forfeits that subsidy. The SDK blocks any attempt to manually charge it.
- `apify-default-dataset-item`: **cannot be renamed** — the name is hardcoded in the Apify platform. Keep it enabled at the prices above; it is the only revenue event for this Actor.
- Both synthetic event names start with `apify-` — the SDK (≥ March 2026) will reject any `Actor.charge(event_name=...)` call that uses a name starting with `apify-`.

### Custom events
None. The Actor code does not call `Actor.charge()`. If you previously had a `document-chunked` event configured in Apify Console, **delete it** — it is no longer fired by the Actor and would waste a slot in the Monetization UI.

## Runtime hardening settings
- `defaultMemoryMbytes`: `256`
- `minMemoryMbytes`: `128`
- `maxMemoryMbytes`: `512`
- `max_input_chars` default: `500000`
- `max_chunks` default: `5000`

## Margin sanity check (reference)
Conservative large run envelope (60 chunks per run, 512 MB / 30 s):
- Compute: `0.0041667 CU × $0.30/CU` = `$0.00125`
- Dataset write cost: `60 items × $0.000005` = `$0.0003`
- Total platform cost: `~$0.00155`

Revenue at FREE-tier pricing (`$0.0008` per chunk):
- Revenue: `60 × $0.0008` = `$0.048`
- Creator share: `0.8 × $0.048` = `$0.0384`
- Estimated net margin: `$0.0384 - $0.00155` ≈ `$0.0369` per run

Break-even chunk count per run:
- Required revenue to cover platform cost: `$0.00155 / 0.8` = `$0.0019`
- Required chunks: `$0.0019 / $0.0008` ≈ `3 chunks` per run

Small runs (1–2 chunks on 256 MB) cost ~$0.0004 in compute, so even tiny documents are
roughly break-even, and everything above ~3 chunks is profitable. Per-chunk revenue
scales with document size, so the worst case for margin is now the smallest runs, not
the largest.

## Rollout checklist
1. Publish the synthetic event prices above in Apify admin UI.
2. **Delete the `document-chunked` event** from the Monetization UI if it was previously configured.
3. Monitor 7-14 days:
   - average chunks per run
   - average CU per run
   - margin per run
   - failures by `input_mode`
4. Adjust prices once if measured margins differ materially.
