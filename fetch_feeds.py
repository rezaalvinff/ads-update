"""
Menarik artikel terbaru dari Jon Loomer (Meta Ads) dan Search Engine Land
(Google Ads, PPC & SEO), membuat ringkasan singkat berbahasa Indonesia
pakai Gemini API (gratis), lalu menyimpan semuanya ke data.json.

Yang disimpan hanya judul, tanggal, cuplikan pendek, ringkasan AI,
kategori, dan link — bukan isi artikel lengkap.
"""

import datetime
import html
import json
import os
import re
import time
import urllib.error
import urllib.request

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

# --- Pengaturan ringkasan AI ---
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
# Dicoba berurutan; kalau satu model tidak tersedia, pakai yang berikutnya.
GEMINI_MODELS = [m.strip() for m in os.environ.get(
    "GEMINI_MODELS",
    "gemini-3.5-flash-lite,gemini-3.5-flash,gemini-3.1-flash-lite,gemini-flash-latest",
).split(",") if m.strip()]
MAX_NEW_SUMMARIES = 30  # maksimal artikel baru yang diringkas per sekali jalan
DELAY_SECONDS = 5       # jeda antar permintaan biar aman dari batas kuota gratis
ARTICLE_CHAR_LIMIT = 12000

PROMPT = """Kamu membantu seorang praktisi digital marketing di Indonesia memantau update Meta Ads, Google Ads, dan SEO.

Ringkas artikel berikut dalam Bahasa Indonesia yang santai tapi jelas:
- Tulis tepat 3 poin, tiap poin diawali "- " dan maksimal 25 kata.
- Poin 1: apa yang baru / inti artikelnya.
- Poin 2: detail paling penting (fitur, angka, atau perubahan konkret).
- Poin 3: dampak atau tindakan praktis buat advertiser/marketer.
- Pakai kata-katamu sendiri, jangan menyalin kalimat dari artikel.
- Istilah teknis (Advantage+, Performance Max, AI Overviews, dll.) biarkan dalam bahasa Inggris.
- Jangan menambahkan informasi yang tidak ada di artikel.
- Tulis hanya 3 poin itu, tanpa judul atau pengantar.

Judul: {title}

Artikel:
{content}"""


def strip_html(raw):
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", raw or "", flags=re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"The post .*? appeared first on .*?\.", " ", text)
    text = re.sub(r"Read more\s*»?", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def clean_text(raw, limit=EXCERPT_LEN):
    """Cuplikan pendek dari ringkasan feed."""
    text = strip_html(raw)
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


def full_text(entry):
    """Isi artikel dari feed (hanya dipakai sebagai bahan ringkasan, tidak disimpan)."""
    if entry.get("content"):
        raw = " ".join(c.get("value", "") for c in entry["content"])
    else:
        raw = entry.get("summary", "")
    return strip_html(raw)[:ARTICLE_CHAR_LIMIT]


def load_previous():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            pass
    return {"sources": {}}


# ---------------------------------------------------------------- Gemini ---

_working_model = None


def call_gemini(model, prompt):
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{model}:generateContent")
    body = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.3, "maxOutputTokens": 400},
    }).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Content-Type": "application/json",
        "x-goog-api-key": GEMINI_API_KEY,
    })
    with urllib.request.urlopen(req, timeout=60) as res:
        data = json.load(res)
    parts = data["candidates"][0]["content"]["parts"]
    return "".join(p.get("text", "") for p in parts)


def parse_points(text):
    points = []
    for line in text.splitlines():
        line = line.strip()
        if re.match(r"^([-*•]|\d+[.)])\s+", line):
            point = re.sub(r"^([-*•]|\d+[.)])\s+", "", line)
            point = point.replace("**", "").strip()
            if point:
                points.append(point)
    return points[:3]


def summarize(title, content):
    """Mengembalikan list 3 poin, atau None kalau gagal."""
    global _working_model
    models = [_working_model] if _working_model else GEMINI_MODELS
    prompt = PROMPT.format(title=title, content=content)
    for model in models:
        for attempt in range(3):
            try:
                points = parse_points(call_gemini(model, prompt))
                if points:
                    _working_model = model
                    return points
                break
            except urllib.error.HTTPError as e:
                if e.code == 429:            # kena batas kuota: tunggu lalu coba lagi
                    time.sleep(30 * (attempt + 1))
                    continue
                if e.code in (400, 403):     # API key salah / tidak diizinkan
                    print(f"[ringkasan] Error {e.code}: cek GEMINI_API_KEY")
                    return None
                print(f"[ringkasan] Model {model} tidak bisa dipakai ({e.code}), coba model lain")
                break                        # 404 dll: coba model berikutnya
            except (urllib.error.URLError, KeyError, IndexError, ValueError, TimeoutError) as e:
                print(f"[ringkasan] Gagal ({e}), coba lagi")
                time.sleep(5)
    return None


# ------------------------------------------------------------------ main ---

def main():
    previous = load_previous()
    old_summaries = {
        item["link"]: item["summary"]
        for src in previous.get("sources", {}).values()
        for item in src.get("items", [])
        if item.get("summary")
    }

    output = {
        "updated": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "sources": {},
    }
    pending = []   # artikel yang belum punya ringkasan: (item, isi artikel)

    for feed in FEEDS:
        parsed = feedparser.parse(feed["url"], agent=USER_AGENT)
        items = []
        for entry in parsed.entries[:MAX_ITEMS]:
            tags = [t.get("term", "") for t in entry.get("tags", []) if t.get("term")]
            item = {
                "title": html.unescape(entry.get("title", "")).strip(),
                "link": entry.get("link", ""),
                "date": to_iso(entry),
                "excerpt": clean_text(entry.get("summary", "")),
                "label": label_for(feed["key"], tags),
            }
            if item["link"] in old_summaries:
                item["summary"] = old_summaries[item["link"]]
            else:
                pending.append((item, full_text(entry)))
            items.append(item)

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

    # Ringkas artikel terbaru dulu
    if not GEMINI_API_KEY:
        print("[ringkasan] GEMINI_API_KEY belum diisi, ringkasan dilewati")
    else:
        pending.sort(key=lambda p: p[0]["date"] or "", reverse=True)
        done = 0
        for item, content in pending[:MAX_NEW_SUMMARIES]:
            if len(content) < 200:
                continue
            points = summarize(item["title"], content)
            if points:
                item["summary"] = points
                done += 1
            time.sleep(DELAY_SECONDS)
        left = max(0, len(pending) - MAX_NEW_SUMMARIES)
        print(f"[ringkasan] {done} artikel baru diringkas"
              + (f", {left} sisanya menyusul di jadwal berikutnya" if left else ""))

    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
