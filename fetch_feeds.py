"""
Menarik artikel terbaru dari Jon Loomer (Meta Ads) dan Search Engine Land
(Google Ads, PPC & SEO), lalu menyimpannya ke data.json.

Yang disimpan hanya judul, tanggal, cuplikan pendek, kategori, dan link —
bukan isi artikel lengkap.
"""

import datetime
import html
import json
import os
import re

import feedparser

FEEDS = [
    {
        "key": "meta",
        "source": "Jon Loomer",
        "site": "https://www.jonloomer.com",
        "url": "https://www.jonloomer.com/feed/",
    },
    {
        "key": "google",
        "source": "Search Engine Land",
        "site": "https://searchengineland.com",
        "url": "https://searchengineland.com/feed",
    },
]

MAX_ITEMS = 40          # jumlah artikel yang disimpan per sumber
EXCERPT_LEN = 220       # panjang maksimal cuplikan (karakter)
DATA_FILE = "data.json"
USER_AGENT = "Mozilla/5.0 (compatible; AdsUpdateTracker/1.0)"


def clean_text(raw, limit=EXCERPT_LEN):
    """Buang tag HTML & teks 'The post ... appeared first on ...', lalu potong."""
    text = re.sub(r"<[^>]+>", " ", raw or "")
    text = html.unescape(text)
    text = re.sub(r"The post .*? appeared first on .*?\.", " ", text)
    text = re.sub(r"Read more\s*»?", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0].rstrip(".,;:") + "…"


def label_for(feed_key, tags):
    """Label kecil untuk tiap artikel (dipakai buat filter di halaman)."""
    lowered = [t.lower() for t in tags]
    if feed_key == "meta":
        if any("chatgpt" in t for t in lowered):
            return "ChatGPT Ads"
        return "Meta Ads"
    if any(t in ("google ads", "ppc", "microsoft ads", "paid search") for t in lowered):
        return "PPC"
    if any("seo" in t for t in lowered):
        return "SEO"
    return "Lainnya"


def to_iso(entry):
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if not parsed:
        return None
    dt = datetime.datetime(*parsed[:6], tzinfo=datetime.timezone.utc)
    return dt.isoformat()


def load_previous():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            pass
    return {"sources": {}}


def main():
    previous = load_previous()
    output = {
        "updated": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "sources": {},
    }

    for feed in FEEDS:
        parsed = feedparser.parse(feed["url"], agent=USER_AGENT)
        items = []
        for entry in parsed.entries[:MAX_ITEMS]:
            tags = [t.get("term", "") for t in entry.get("tags", []) if t.get("term")]
            items.append({
                "title": html.unescape(entry.get("title", "")).strip(),
                "link": entry.get("link", ""),
                "date": to_iso(entry),
                "excerpt": clean_text(entry.get("summary", "")),
                "label": label_for(feed["key"], tags),
            })

        if items:
            status = "ok"
        else:
            # Gagal narik: pakai data lama biar halaman tetap ada isinya
            items = previous.get("sources", {}).get(feed["key"], {}).get("items", [])
            status = "gagal update, menampilkan data sebelumnya"
            print(f"[peringatan] {feed['source']}: feed kosong/gagal diambil")

        output["sources"][feed["key"]] = {
            "source": feed["source"],
            "site": feed["site"],
            "status": status,
            "items": items,
        }
        print(f"{feed['source']}: {len(items)} artikel ({status})")

    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
