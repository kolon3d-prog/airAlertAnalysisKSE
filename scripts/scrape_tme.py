"""Backfill Telegram channel history from public t.me/s web previews (no account).

Usage: python scrape_tme.py <channel> <start_utc> <end_utc> <out.jsonl>
Dates: ISO, e.g. 2025-06-08T21:00  (assumed UTC)
"""
import json, re, sys, time
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"}
SLEEP = 1.2
REQS = 0  # request counter for reporting


def get(url, tries=4):
    global REQS
    for i in range(tries):
        REQS += 1
        r = requests.get(url, headers=UA, timeout=30)
        if r.status_code == 429:
            wait = int(r.headers.get("Retry-After", 10))
            print(f"  429, sleeping {wait}s", file=sys.stderr)
            time.sleep(wait)
            continue
        r.raise_for_status()
        return r
    raise RuntimeError(f"giving up on {url}")


def parse_page(html, channel):
    """Return list of records oldest-first."""
    soup = BeautifulSoup(html, "lxml")
    out = []
    for m in soup.select(".tgme_widget_message"):
        post = m.get("data-post")
        if not post:
            continue  # service / unsupported blocks
        t = m.select_one(".tgme_widget_message_date time") or m.select_one("time[datetime]")
        txt = m.select_one(".tgme_widget_message_text")
        body = m.select_one(".tgme_widget_message_bubble")
        out.append({
            "channel": channel,
            "msg_id": int(post.split("/")[1]),
            "datetime_utc": t.get("datetime") if t else None,
            "text": (txt.get_text("\n", strip=True) if txt else ""),
            "forwarded": bool(m.select_one(".tgme_widget_message_forwarded_from")),
            "reply": bool(m.select_one(".tgme_widget_message_reply")),
            "has_media": bool(body and body.select_one("a.tgme_widget_message_photo_wrap, video, a.tgme_widget_message_video_player")),
        })
    out.sort(key=lambda r: r["msg_id"])
    return out


def fetch(channel, before=None):
    url = f"https://t.me/s/{channel}" + (f"?before={before}" if before else "")
    time.sleep(SLEEP)
    return parse_page(get(url).text, channel)


def dt(rec):
    return datetime.fromisoformat(rec["datetime_utc"]) if rec["datetime_utc"] else None


def main():
    channel, start_s, end_s, out_path = sys.argv[1:5]
    start = datetime.fromisoformat(start_s).replace(tzinfo=timezone.utc)
    end = datetime.fromisoformat(end_s).replace(tzinfo=timezone.utc)

    latest = fetch(channel)
    if not latest:
        sys.exit("no messages")
    print(f"latest id={latest[-1]['msg_id']} ({latest[-1]['datetime_utc']})")

    # binary search: smallest before-id whose page still reaches `end` (msg ids increase with time)
    lo, hi = 1, latest[-1]["msg_id"] + 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        page = fetch(channel, mid)
        newest = dt(page[-1]) if page else None
        if newest and newest >= end:
            hi = mid
        else:
            lo = mid
    print(f"landed at before={hi} after {REQS} requests")

    # walk backward from hi collecting everything >= start
    seen, results = set(), []
    before = hi
    while True:
        page = fetch(channel, before)
        if not page:
            break
        for rec in page:
            d = dt(rec)
            if rec["msg_id"] not in seen and d and start <= d < end:
                seen.add(rec["msg_id"])
                results.append(rec)
        oldest = dt(page[0])
        print(f"  page ending {page[-1]['msg_id']}: {oldest} .. {dt(page[-1])} | kept {len(results)}")
        if oldest and oldest < start:
            break
        before = page[0]["msg_id"]

    results.sort(key=lambda r: r["msg_id"])
    with open(out_path, "w", encoding="utf-8") as f:
        for rec in results:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(f"wrote {len(results)} messages to {out_path} in {REQS} requests")


if __name__ == "__main__":
    main()
