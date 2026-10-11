"""Estimate backfill cost: msg count in a window via msg-id bisection (t.me/s).

Usage: python estimate_backfill.py <channel> <start_utc> [<mid_utc>]
Prints latest id, boundary id at start, count, and (if mid given) threat fraction of the mid page.
"""
import re, sys
from datetime import datetime, timezone

sys.path.insert(0, __file__.rsplit("\\", 1)[0])
from scrape_tme import fetch, dt, REQS
import scrape_tme

THREAT = re.compile(
    r"shahed|шахед|бпла|ракет|курс|каб\b|бал[іи]ст|дрон|геран|missile|uav|запуск|ціль|угроза|загроза",
    re.IGNORECASE,
)


def boundary_id(channel, target):
    """Bisect: smallest before-id whose page still reaches `target`; return first msg id >= target."""
    latest = fetch(channel)
    hi = latest[-1]["msg_id"] + 1
    lo = 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        page = fetch(channel, mid)
        newest = dt(page[-1]) if page else None
        if newest and newest >= target:
            hi = mid
        else:
            lo = mid
    page = fetch(channel, hi)
    first = next((r for r in page if dt(r) and dt(r) >= target), None)
    return (first["msg_id"] if first else page[-1]["msg_id"]), page


def main():
    channel = sys.argv[1]
    start = datetime.fromisoformat(sys.argv[2]).replace(tzinfo=timezone.utc)
    mid = datetime.fromisoformat(sys.argv[3]).replace(tzinfo=timezone.utc) if len(sys.argv) > 3 else None

    latest = fetch(channel)
    max_id = latest[-1]["msg_id"]
    print(f"[{channel}] latest id={max_id} ({latest[-1]['datetime_utc']})")

    bid, _ = boundary_id(channel, start)
    count = max_id - bid + 1
    print(f"[{channel}] id at {start.date()} ~= {bid}  =>  msgs in window ~= {count}")

    if mid:
        _, page = boundary_id(channel, mid)
        hits = [r for r in page if THREAT.search(r["text"])]
        print(f"[{channel}] mid page ({mid.date()}): {len(hits)}/{len(page)} threat-ish")
        for r in hits[:3]:
            print("   e.g.", r["text"][:120].replace("\n", " "))
    print(f"[{channel}] total requests so far: {scrape_tme.REQS}")


if __name__ == "__main__":
    main()
