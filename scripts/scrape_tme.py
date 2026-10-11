"""Backfill Telegram channel history from public t.me/s web previews (no account).

Usage: python scrape_tme.py <channel> <start_utc> <end_utc> <out.jsonl>
Dates: ISO, e.g. 2025-06-08T21:00  (assumed UTC)
"""
import json, os, re, sys, time
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
    ckpt_path = out_path + ".ckpt"

    # resume: checkpoint holds next `before` cursor; seen rebuilt from output file
    before, kept = None, 0
    if os.path.exists(ckpt_path) and os.path.exists(out_path):
        with open(ckpt_path) as c:
            before = json.load(c)["before"]
        with open(out_path, encoding="utf-8") as f:
            kept = sum(1 for line in f if line.strip())
        print(f"resuming from before={before}, {kept} msgs already saved")

    if before is None:
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
        before = hi

    # walk backward from `before`, appending + checkpointing each page (crash-safe)
    with open(out_path, "a", encoding="utf-8") as out, open(ckpt_path, "w") as c:
        while True:
            page = fetch(channel, before)
            if not page:
                break
            for rec in page:
                d = dt(rec)
                if d and start <= d < end:
                    out.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    kept += 1
            out.flush()
            oldest = dt(page[0])
            print(f"  page ending {page[-1]['msg_id']}: {oldest} .. {dt(page[-1])} | kept {kept}", flush=True)
            if oldest and oldest < start:
                break
            before = page[0]["msg_id"]
            c.seek(0)
            json.dump({"before": before}, c)
            c.truncate()
            c.flush()

    # done: dedup (crash-seam page may be written twice), sort in place, drop checkpoint
    with open(out_path, encoding="utf-8") as f:
        recs = {json.loads(line)["msg_id"]: json.loads(line) for line in f if line.strip()}
    recs = sorted(recs.values(), key=lambda r: r["msg_id"])
    with open(out_path, "w", encoding="utf-8") as f:
        for rec in recs:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    os.remove(ckpt_path)
    print(f"wrote {len(recs)} messages to {out_path} in {REQS} requests")


if __name__ == "__main__":
    main()
