# t.me/s public web preview — scraping notes (verified 2026-10-11)

Verdict: full channel history backfill works WITHOUT an account, plain GETs.

## HTML structure (both kpszsu and eRadarrua identical)
- Message container: `div.tgme_widget_message`, id via `data-post="channel/<msg_id>"` (blocks without `data-post` are service/unsupported — skip).
- Datetime: `a.tgme_widget_message_date time[datetime]` — ISO 8601 with `+00:00` offset (newest pages use `T` separator, older pages use a space — `datetime.fromisoformat` handles both). Plain `<time>` without `datetime` attr exists inside messages (edited/media) — must select the date-link one.
- Text: `.tgme_widget_message_text` (`get_text("\n", strip=True)` preserves line breaks; emojis are inline `<i class="emoji">` with the emoji char inside, so they survive).
- Forwarded marker: `.tgme_widget_message_forwarded_from`; reply marker: `.tgme_widget_message_reply`.
- Media: photo wraps `a.tgme_widget_message_photo_wrap` (URL only in inline `style="background-image:url(...)"`), video `a.tgme_widget_message_video_player` / `<video>`; media-only messages have no text div (text = "").

## Pagination
- `?before=<msg_id>` — EXCLUSIVE: returns the 20 messages with id < N. Plain `/s/<ch>` returns the newest 20.
- 20 posts per page, always.
- No depth limit: `?before=25` on @kpszsu returns msg ids 1-20 (channel start Feb 2019).
- Message ids are sequential per channel and monotonic in time → binary search on `?before=` finds any historical date in ~log2(latest_id) requests (17 requests to land on 2025-06-09 from id ~85181).

## Rate limits (polite sleep 1.2 s between GETs)
- 20+ sequential requests (bisect 16 months back + 3 pages): zero 429s, zero errors.
- No `Retry-After` observed; prototype honors it if present (fallback 10 s, up to 4 tries).

## Verified historical window
- Target: night of 2025-06-08→09 mass Shahed attack. Bisection landed at `before=36047` in 17 requests; 3 backward pages (20 requests total, ~1 min) covered 2025-06-08 11:51 → 2025-06-09 12:30 UTC, 51 messages in window.
- Morning strike summary recovered: msg 36042, 2025-06-09T07:44:49Z, "ЗНЕШКОДЖЕНО 479 ЗАСОБІВ ПОВІТРЯНОГО НАПАДУ! … У ніч на 9 червня (із 20.00 8 червня)…" — matches the documented attack.

## Prototype
- `scripts/scrape_tme.py <channel> <start_utc> <end_utc> <out.jsonl>` — bisect + backward walk, JSONL fields: channel, msg_id, datetime_utc, text, forwarded, reply, has_media.
- Sample: `data/raw/kpszsu_sample.jsonl` (51 msgs).

## Blockers / caveats
- None blocking. Caveats: `before` is exclusive (off-by-one); some `<time>` tags lack `datetime`; text format of datetime attr varies by page age; view counts live in `.tgme_widget_message_views` (not extracted); "media not supported" bubbles exist for some content types.
