#!/usr/bin/env python3
"""
Global Market News Dashboard

"""

import http.server
import urllib.request
import urllib.parse
import urllib.error
import json
import concurrent.futures
import threading
import webbrowser
import time
import sys
import gzip
import ssl
import re

import os
PORT = int(os.environ.get('PORT', 8080))
CACHE_TTL = 300          # seconds before re-fetching a feed
FETCH_TIMEOUT = 10       # seconds per feed request
STARTUP_WORKERS = 6      # parallel threads during startup prefetch

# SSL context that tolerates self-signed / older certs
SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/124.0.0.0 Safari/537.36'
    ),
    'Accept': 'application/rss+xml, application/xml, text/xml, */*',
    'Accept-Encoding': 'gzip, deflate',
    'Accept-Language': 'en-US,en;q=0.9',
    'Cache-Control': 'no-cache',
}

# ── Feed definitions ──────────────────────────────────────────
# Each source has a list of URLs tried in order until one works.
REGIONS = [
    {
        'id': 'usa', 'flag': '🇺🇸', 'name': 'United States',
        'sources': [
            {'name': 'Bloomberg Markets', 'urls': [
                'https://feeds.bloomberg.com/markets/news.rss',
                'https://feeds.bloomberg.com/economics/news.rss',
                'https://feeds.bloomberg.com/politics/news.rss',
            ]},
            {'name': 'CNBC Markets', 'urls': [
                'https://www.cnbc.com/id/100003114/device/rss/rss.html',
                'https://www.cnbc.com/id/15839069/device/rss/rss.html',
            ]},
            {'name': 'MarketWatch',  'urls': [
                'https://feeds.marketwatch.com/marketwatch/topstories/',
                'https://feeds.marketwatch.com/marketwatch/marketpulse/',
            ]},
            {'name': 'NPR Business', 'urls': [
                'https://feeds.npr.org/1006/rss.xml',
                'https://feeds.npr.org/1017/rss.xml',
            ]},
        ]
    },
    {
        'id': 'war', 'flag': '🔥', 'name': 'War Zone',
        'sources': [
            {'name': 'Times of Israel', 'urls': [
                'https://www.timesofisrael.com/feed/',
                'https://www.timesofisrael.com/blogs/feed/',
            ]},
            {'name': 'Al Jazeera', 'urls': [
                'https://www.aljazeera.com/xml/rss/all.xml',
                'https://www.aljazeera.com/rss/all.xml',
            ]},
            {'name': 'Middle East Eye', 'urls': [
                'https://www.middleeasteye.net/rss',
                'https://www.middleeasteye.net/rss.xml',
            ]},
            {'name': 'Press TV', 'urls': [
                'https://www.presstv.ir/homepagearticles.rss',
                'https://www.presstv.ir/rss.xml',
                'https://www.presstv.ir/rss',
            ]},
        ]
    },
    {
        'id': 'europe', 'flag': '🇪🇺', 'name': 'Europe',
        'sources': [
            {'name': 'BBC Business', 'urls': [
                'https://feeds.bbci.co.uk/news/business/rss.xml',
                'https://feeds.bbci.co.uk/news/rss.xml',
            ]},
            {'name': 'DW Business',  'urls': [
                'https://rss.dw.com/rdf/rss-en-bus',
                'https://rss.dw.com/rdf/rss-en-all',
            ]},
            {'name': 'The Guardian', 'urls': [
                'https://www.theguardian.com/uk/business/rss',
                'https://www.theguardian.com/business/rss',
            ]},
        ]
    },
    {
        'id': 'korea', 'flag': '🇰🇷', 'name': 'South Korea',
        'sources': [
            {'name': 'Korea JoongAng', 'urls': [
                'https://koreajoongangdaily.joins.com/rss/feeds/all',
                'https://koreajoongangdaily.joins.com/feed',
                'https://world.kbs.co.kr/rss/rss_news.htm?lang=e',
                'https://www.arirang.com/rss/News.xml',
            ]},
            {'name': '한국경제 (Hankyung)', 'translate': 'ko', 'urls': [
                'https://www.hankyung.com/feed/economy',
                'https://www.hankyung.com/feed/finance',
                'https://www.hankyung.com/feed/all-news',
            ]},
            {'name': '매일경제 (Maeil Business)', 'translate': 'ko', 'urls': [
                'https://www.mk.co.kr/rss/40300001/',
                'https://www.mk.co.kr/rss/30000001/',
                'https://www.mk.co.kr/rss/40100041/',
            ]},
        ]
    },
    {
        'id': 'china', 'flag': '🇨🇳🇯🇵', 'name': 'China & Japan',
        'sources': [
            {'name': 'SCMP', 'urls': [
                'https://www.scmp.com/rss/91/feed',
                'https://www.scmp.com/rss/4/feed',
                'https://www.scmp.com/rss/2/feed',
            ]},
            {'name': 'Nikkei Asia', 'urls': [
                'https://asia.nikkei.com/rss/feed/nar',
                'https://asia.nikkei.com/rss/feed/china',
                'https://asia.nikkei.com/rss',
            ]},
        ]
    },
    {
        'id': 'latam', 'flag': '🌎', 'name': 'Latin America',
        'sources': [
            {'name': 'MercoPress',   'urls': [
                'https://en.mercopress.com/rss.xml',
                'https://en.mercopress.com/economia/rss.xml',
                'https://www.ticotimes.net/feed',
                'https://www.infobae.com/feeds/rss/economia/',
                'https://www.telam.com.ar/rss/economia.xml',
            ]},
            {'name': 'BA Times',     'urls': [
                'https://batimes.com.ar/feed',
                'https://batimes.com.ar/feed/',
            ]},
            {'name': 'Rio Times',    'urls': [
                'https://riotimesonline.com/feed/',
                'https://riotimesonline.com/feed',
            ]},
        ]
    },
]

# ── Feed cache ────────────────────────────────────────────────
# { url: {'raw': bytes, 'ts': float} }
_cache = {}
_cache_lock = threading.Lock()

# ── Multi-currency 10Y yield OHLC cache (Yahoo Finance) ────────
_yields_cache = {'data': None, 'ts': 0}
_yields_lock  = threading.Lock()
YIELDS_TTL    = 3600

# ── 10Y Yield data — multi-source (no API keys, works on Render) ─────────────
import datetime as _dt

def _make_ohlc(close_pts):
    """Convert [(date_str, close), …] → synthetic OHLC candles.
    open = previous close; high/low = directional range."""
    rows, prev = [], None
    for ds, c in sorted(close_pts, key=lambda x: x[0]):
        o = prev if prev is not None else c
        rows.append({'date': ds, 'o': round(o,4), 'h': round(max(o,c),4),
                     'l': round(min(o,c),4), 'c': round(c,4)})
        prev = c
    return rows

def _fred(series_id, days=400):
    """Fetch close series from FRED. Uses cosd= to avoid downloading 60yr history."""
    start = (_dt.date.today() - _dt.timedelta(days=days)).strftime('%Y-%m-%d')
    # cosd filters server-side so only recent data is downloaded
    url = (f'https://fred.stlouisfed.org/graph/fredgraph.csv'
           f'?id={series_id}&cosd={start}')
    req = urllib.request.Request(url, headers={'User-Agent': HEADERS['User-Agent']})
    with urllib.request.urlopen(req, timeout=30, context=SSL_CTX) as r:
        lines = r.read().decode().strip().splitlines()
    pts = []
    for line in lines[1:]:
        parts = line.split(',')
        if len(parts) < 2: continue
        ds, vs = parts[0].strip(), parts[1].strip()
        if vs in ('.', ''): continue
        try: pts.append((ds, float(vs)))
        except ValueError: pass
    return pts

def _ecb_eur(days=400):
    """Fetch EUR 10Y yield from ECB Data Warehouse (daily)."""
    start = (_dt.date.today() - _dt.timedelta(days=days)).strftime('%Y-%m-%d')
    url = ('https://data-api.ecb.europa.eu/service/data/YC/'
           'B.U2.EUR.4F.G_N_A.SV_C_YM.SR_10Y'
           f'?format=csvdata&startPeriod={start}')
    req = urllib.request.Request(url, headers={
        'User-Agent': HEADERS['User-Agent'], 'Accept': 'text/csv'})
    with urllib.request.urlopen(req, timeout=15, context=SSL_CTX) as r:
        lines = r.read().decode().strip().splitlines()
    if not lines: return []
    hdr = lines[0].split(',')
    try: di, vi = hdr.index('TIME_PERIOD'), hdr.index('OBS_VALUE')
    except ValueError: return []
    pts = []
    for line in lines[1:]:
        p = line.split(',')
        if len(p) <= max(di, vi): continue
        ds, vs = p[di].strip(), p[vi].strip()
        if not vs: continue
        try: pts.append((ds, float(vs)))
        except ValueError: pass
    return pts

def _boc_cad(days=400):
    """Fetch CAD 10Y yield from Bank of Canada Valet API (daily).
    Series BD.CDN.10YR.DED = Government of Canada benchmark bond yield, 10-year."""
    start = (_dt.date.today() - _dt.timedelta(days=days)).strftime('%Y-%m-%d')
    series = 'BD.CDN.10YR.DED'
    url = (f'https://www.bankofcanada.ca/valet/observations/{series}/json'
           f'?start_date={start}')
    req = urllib.request.Request(url, headers={'User-Agent': HEADERS['User-Agent']})
    with urllib.request.urlopen(req, timeout=20, context=SSL_CTX) as r:
        data = json.loads(r.read())
    pts = []
    for obs in data.get('observations', []):
        ds = obs.get('d', '')
        v  = obs.get(series, {}).get('v')
        if v and v not in ('', 'Bank holiday'):
            try: pts.append((ds, float(v)))
            except ValueError: pass
    return pts

# USD daily (FRED), EUR daily (ECB), CAD daily (BoC),
# GBP/JPY/AUD monthly (FRED — best free fallback)
YIELD_FETCHERS = {
    'USD': (_fred,    'DGS10',            'FRED daily'),
    'EUR': (_ecb_eur, None,               'ECB daily'),
    'GBP': (_fred,    'IRLTLT01GBM156N',  'FRED monthly'),
    'JPY': (_fred,    'IRLTLT01JPM156N',  'FRED monthly'),
    'CAD': (_boc_cad, None,               'BoC daily'),
    'AUD': (_fred,    'IRLTLT01AUM156N',  'FRED monthly'),
}

def fetch_all_yields():
    result = {}
    for ccy, (fn, arg, label) in YIELD_FETCHERS.items():
        try:
            pts = fn(arg) if arg else fn()
            rows = _make_ohlc(pts)
            print(f'  ✅ {ccy} ({label}) → {len(rows)} pts')
            result[ccy] = rows
        except Exception as e:
            print(f'  ❌ {ccy} ({label}) → {e}')
            result[ccy] = []
    return result

def get_yields_cached():
    with _yields_lock:
        if _yields_cache['data'] and time.time() - _yields_cache['ts'] < YIELDS_TTL:
            return _yields_cache['data']
    print('Fetching 10Y yields (FRED/ECB/BoC)…')
    data = fetch_all_yields()
    with _yields_lock:
        _yields_cache['data'] = data
        _yields_cache['ts']   = time.time()
    return data

def fetch_url(url):
    """Fetch a single URL and return raw bytes. Raises on failure."""
    from urllib.parse import urlparse
    domain = urlparse(url).netloc
    h = dict(HEADERS)
    h['Referer'] = f'https://{domain}/'
    h['Origin']  = f'https://{domain}'
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT, context=SSL_CTX) as resp:
        raw = resp.read()
        enc = resp.info().get('Content-Encoding', '')
        if enc == 'gzip':
            raw = gzip.decompress(raw)
    return raw

def is_rss(raw):
    """Return True if raw bytes look like RSS/Atom XML."""
    snippet = raw[:600].decode('utf-8', 'ignore').lstrip()
    return any(t in snippet for t in ['<rss', '<feed', '<item', '<?xml', 'xmlns'])

def is_korean(text):
    """Return True if text contains Korean characters."""
    return any('\uAC00' <= c <= '\uD7A3' for c in text)

def needs_translation(text):
    """Return True if text contains Korean or Chinese characters."""
    return any(
        '\uAC00' <= c <= '\uD7A3' or   # Korean Hangul
        '\u4E00' <= c <= '\u9FFF' or   # CJK Unified Ideographs (Chinese/Japanese)
        '\u3400' <= c <= '\u4DBF'      # CJK Extension A
        for c in text
    )

def translate_headline(text, src_lang='ko'):
    """Translate a single headline to English using the free MyMemory API."""
    try:
        # Google Translate unofficial API — no key, no daily limit
        gt_lang = {'ko': 'ko', 'zh': 'zh-CN'}.get(src_lang, src_lang)
        q = urllib.parse.quote(text[:500])
        url = (f'https://translate.googleapis.com/translate_a/single'
               f'?client=gtx&sl={gt_lang}&tl=en&dt=t&q={q}')
        req = urllib.request.Request(url, headers={'User-Agent': HEADERS['User-Agent']})
        with urllib.request.urlopen(req, timeout=6, context=SSL_CTX) as resp:
            data = json.loads(resp.read())
        # Response: [[[translated, original, ...], ...], ...]
        translated = ''.join(seg[0] for seg in (data[0] or []) if seg[0]).strip()
        if translated:
            return translated
    except Exception:
        pass
    return text  # fallback: return original

def translate_rss_titles(raw, src_lang='ko'):
    """Translate all item <title> and <description> fields in parallel."""
    xml = raw.decode('utf-8', 'ignore')

    # Match both <title> and <description> inside <item> blocks
    field_re = re.compile(
        r'(<(?:title|description)>)(.*?)(</(?:title|description)>)',
        re.DOTALL
    )

    # Only operate inside <item> blocks
    item_re = re.compile(r'<item>.*?</item>', re.DOTALL)

    def strip_tags(text):
        """Remove HTML tags and decode basic entities from text."""
        text = re.sub(r'<[^>]+>', ' ', text)
        text = text.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>').replace('&nbsp;', ' ')
        return ' '.join(text.split()).strip()

    def translate_item(item_xml):
        matches = list(field_re.finditer(item_xml))
        originals = []
        for m in matches:
            inner = m.group(2)
            cdata = re.match(r'<!\[CDATA\[(.*?)\]\]>', inner.strip(), re.DOTALL)
            raw_text = cdata.group(1).strip() if cdata else inner.strip()
            # Strip HTML tags so Google Translate gets clean text
            originals.append(strip_tags(raw_text))

        def maybe_translate(text):
            if text and needs_translation(text):
                return translate_headline(text, src_lang)
            return text

        with concurrent.futures.ThreadPoolExecutor(max_workers=15) as ex:
            translated = list(ex.map(maybe_translate, originals))

        for m, t in zip(reversed(matches), reversed(translated)):
            replacement = f'{m.group(1)}<![CDATA[{t}]]>{m.group(3)}'
            item_xml = item_xml[:m.start()] + replacement + item_xml[m.end():]
        return item_xml

    # Replace each item block with its translated version
    result = item_re.sub(lambda m: translate_item(m.group(0)), xml)
    return result.encode('utf-8')

def scrape_headlines(raw, source_name, base_url):
    """Extract article headlines from an HTML page and return RSS XML bytes."""
    try:
        html = raw.decode('utf-8', 'ignore')
    except Exception:
        html = raw.decode('latin-1', 'ignore')

    # Strip <script> and <style> blocks FIRST so we never match JS template strings
    html = re.sub(r'<script[^>]*>.*?</script>', ' ', html, flags=re.IGNORECASE | re.DOTALL)
    html = re.sub(r'<style[^>]*>.*?</style>',  ' ', html, flags=re.IGNORECASE | re.DOTALL)
    html = re.sub(r'<!--.*?-->', ' ', html, flags=re.DOTALL)  # strip HTML comments too

    parsed = urllib.parse.urlparse(base_url)
    domain_root = f"{parsed.scheme}://{parsed.netloc}"

    # Extract <a href="...">text</a> pairs
    pattern = re.compile(
        r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
        re.IGNORECASE | re.DOTALL
    )

    # JS/code artifact characters that should never appear in headlines (unused — filter below)
    JS_CHARS = {'{', '}', ';', '()', '_HTML_', 'var ', 'function', '=>', '+=', '/*', '*/'}

    articles = []
    seen = set()
    # Skip URLs that look like navigation/non-article
    skip_url = ['javascript:', 'mailto:', 'twitter.com', 'facebook.com',
                '/login', '/sign', '/subscribe', '/search', '/tag/', '/author/',
                '/category/', 'instagram.com', 'youtube.com', '.pdf', '/rss',
                '/about', '/contact', '/privacy', '/terms', '/help', '/home',
                '/section', '/menu', '/nav', '/footer', '/header']
    # Skip titles that look like navigation labels
    skip_title = ['home', 'about', 'contact', 'menu', 'search', 'login',
                  'sign in', 'sign up', 'subscribe', 'more', 'read more',
                  'click here', 'back to', 'return to', 'go to', 'see all',
                  'view all', 'load more', 'show more', 'follow us', 'share',
                  'next', 'previous', 'prev', 'close', 'open', 'submit',
                  'business', 'economy', 'politics', 'sports', 'tech',
                  'entertainment', 'world', 'national', 'opinion', 'markets']

    for m in pattern.finditer(html):
        href = m.group(1).strip()
        text = re.sub(r'<[^>]+>', '', m.group(2)).strip()
        text = ' '.join(text.split())

        # Must be a real headline: 5+ words, 30-250 chars
        words = text.split()
        if len(words) < 5 or len(text) < 30 or len(text) > 250:
            continue
        if text.lower() in seen:
            continue
        if text.lower().strip() in skip_title:
            continue
        if any(s in href.lower() for s in skip_url):
            continue
        # Skip if all-caps (likely a nav label like "ECONOMY")
        if text.isupper():
            continue
        # Skip if text contains JS/code artifacts (quotes, braces, underscores pattern)
        if any(c in text for c in ('{', '}', '();', '_HTML_', 'var ', ' += ', '=>')):
            continue
        # Skip if text has unusually high punctuation density (JS fragments)
        punct_count = sum(1 for c in text if c in "{}();'`_=+|\\")
        if punct_count > 2:
            continue

        if href.startswith('http'):
            url = href
        elif href.startswith('/'):
            url = domain_root + href
        else:
            continue

        # Keep only same-domain links
        if parsed.netloc not in url:
            continue

        seen.add(text.lower())
        articles.append({'title': text, 'url': url})
        if len(articles) >= 12:
            break

    # Convert to RSS XML so the frontend parser works unchanged
    items = '\n'.join(
        f'<item><title><![CDATA[{a["title"]}]]></title>'
        f'<link>{a["url"]}</link><description></description></item>'
        for a in articles
    )
    rss = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<rss version="2.0"><channel>'
        f'<title>{source_name}</title><link>{base_url}</link>'
        f'{items}</channel></rss>'
    )
    return rss.encode('utf-8')

def fetch_with_fallback(src):
    """Try each URL for a source until one works. Returns (url, raw) or raises.
    If the response is HTML (not RSS), scrape it for headlines and return RSS XML.
    If the source has 'translate', translate item titles to English."""
    last_err = None
    lang = src.get('translate')  # e.g. 'ko'
    for url in src['urls']:
        try:
            raw = fetch_url(url)
            if is_rss(raw):
                if lang:
                    print(f"  🌐  {src['name']} translating titles ({lang}→en)…")
                    raw = translate_rss_titles(raw, lang)
                return url, raw
            else:
                # HTML page — scrape headlines and wrap as RSS
                rss_raw = scrape_headlines(raw, src['name'], url)
                if b'<item>' in rss_raw:
                    if lang:
                        print(f"  🌐  {src['name']} translating scraped titles ({lang}→en)…")
                        rss_raw = translate_rss_titles(rss_raw, lang)
                    print(f"  📰  {src['name']} (scraped HTML → {rss_raw.count(b'<item>')} items)")
                    return url, rss_raw
                else:
                    last_err = Exception('No headlines found on page')
        except Exception as e:
            last_err = e
    raise last_err or Exception('All URLs failed')

def get_cached(url):
    with _cache_lock:
        entry = _cache.get(url)
        if entry and (time.time() - entry['ts']) < CACHE_TTL:
            return entry['raw']
    return None

def set_cached(url, raw):
    with _cache_lock:
        _cache[url] = {'raw': raw, 'ts': time.time()}

# ── Startup prefetch ──────────────────────────────────────────
def prefetch_all():
    """Run at startup: test every feed, cache hits, report to terminal."""
    print('\n  Testing feeds…\n')
    sem = threading.Semaphore(STARTUP_WORKERS)
    results = {}
    threads = []

    def test_src(region, src):
        with sem:
            try:
                url, raw = fetch_with_fallback(src)
                set_cached(url, raw)
                src['_active_url'] = url
                src['_ok'] = True
                print(f"  ✅  {region['flag']} {src['name']}")
            except Exception as e:
                src['_ok'] = False
                src['_active_url'] = None
                short = str(e)[:55]
                print(f"  ❌  {region['flag']} {src['name']}  →  {short}")

    for region in REGIONS:
        for src in region['sources']:
            t = threading.Thread(target=test_src, args=(region, src), daemon=True)
            threads.append(t)
            t.start()

    for t in threads:
        t.join()

    ok  = sum(1 for r in REGIONS for s in r['sources'] if s.get('_ok'))
    bad = sum(1 for r in REGIONS for s in r['sources'] if not s.get('_ok'))
    print(f'\n  {ok} feeds OK  ·  {bad} unavailable\n')

# ── Build REGIONS JSON for the browser ───────────────────────
def regions_json():
    """Return only the sources that loaded successfully."""
    out = []
    for region in REGIONS:
        good_sources = [s for s in region['sources'] if s.get('_ok')]
        if not good_sources:
            # Keep region but note no sources
            good_sources = region['sources'][:1]  # show at least one tab with error
        out.append({
            'id':      region['id'],
            'flag':    region['flag'],
            'name':    region['name'],
            'sources': [{'name': s['name'], 'url': s['_active_url'] or s['urls'][0]}
                        for s in good_sources],
        })
    return json.dumps(out)

# ── Embedded HTML ─────────────────────────────────────────────
HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
  <title>Global Market News Dashboard</title>
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
  <script src="https://cdn.plot.ly/plotly-2.27.0.min.js"></script>
  <style>
    *,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
    :root{
      --bg:#f4f6fa;--surface:#fff;--border:#e2e8f0;
      --accent:#2563eb;--al:#eff6ff;
      --text:#1e293b;--muted:#64748b;--faint:#94a3b8;
      --r:12px;--sh:0 1px 4px rgba(0,0,0,.07);--shh:0 4px 16px rgba(0,0,0,.10);
    }
    body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
      background:var(--bg);color:var(--text);min-height:100vh;}
    header{background:var(--surface);border-bottom:1px solid var(--border);
      padding:13px 28px;display:flex;align-items:center;
      justify-content:space-between;position:sticky;top:0;z-index:100;box-shadow:var(--sh);}
    .hl{display:flex;align-items:center;gap:11px;}
    .logo{width:34px;height:34px;border-radius:8px;
      background:linear-gradient(135deg,#2563eb,#7c3aed);
      display:flex;align-items:center;justify-content:center;font-size:17px;}
    .hl h1{font-size:16px;font-weight:700;}
    .hl p{font-size:11px;color:var(--faint);margin-top:1px;}
    .hr{display:flex;align-items:center;gap:9px;}
    #live{display:flex;align-items:center;gap:5px;background:#dcfce7;color:#15803d;
      padding:3px 9px;border-radius:99px;font-size:11px;font-weight:600;}
    #live .dot{width:6px;height:6px;border-radius:50%;background:#16a34a;
      animation:blink 1.6s ease-in-out infinite;}
    @keyframes blink{0%,100%{opacity:1}50%{opacity:.3}}
    #upd{font-size:11px;color:var(--faint);}
    #refbtn{padding:6px 15px;background:var(--surface);color:var(--accent);
      border:1.5px solid var(--accent);border-radius:8px;font-size:12px;
      font-weight:600;cursor:pointer;transition:all .2s;}
    #refbtn:hover{background:var(--al);}
    #refbtn:disabled{opacity:.5;cursor:not-allowed;}
    .langbar{display:flex;gap:4px;align-items:center;}
    .langbtn{padding:4px 10px;border:1.5px solid var(--border);border-radius:6px;
      font-size:11px;font-weight:700;cursor:pointer;background:var(--surface);
      color:var(--muted);transition:all .15s;min-width:36px;text-align:center;}
    .langbtn.on{background:var(--accent);color:#fff;border-color:var(--accent);}
    .langbtn:hover:not(.on):not(:disabled){background:var(--al);color:var(--accent);border-color:var(--accent);}
    .langbtn:disabled{opacity:.5;cursor:not-allowed;}
    .translating-banner{display:none;position:fixed;bottom:18px;left:50%;transform:translateX(-50%);
      background:#1e293b;color:#fff;padding:8px 18px;border-radius:99px;font-size:12px;
      font-weight:600;z-index:999;box-shadow:0 4px 16px rgba(0,0,0,.25);gap:8px;align-items:center;}
    .translating-banner.show{display:flex;}
    /* ── Main tab bar ── */
    #maintabs{background:var(--surface);border-bottom:1px solid var(--border);
      padding:0 28px;display:flex;gap:4px;}
    .mtab{padding:11px 18px;font-size:13px;font-weight:600;color:var(--muted);
      cursor:pointer;border-bottom:2.5px solid transparent;background:none;
      border-top:none;border-left:none;border-right:none;transition:all .15s;}
    .mtab:hover{color:var(--accent);}
    .mtab.on{color:var(--accent);border-bottom-color:var(--accent);}
    /* ── Market view ── */
    #fx-view{display:none;padding:20px 28px 36px;}
    /* ── AI Outlook view ── */
    #outlook-view{display:none;padding:20px 28px 36px;}
    .outlook-header{display:flex;align-items:center;gap:12px;margin-bottom:20px;flex-wrap:wrap;}
    .outlook-week{font-size:12px;color:var(--muted);background:var(--surface);border:1px solid var(--border);padding:4px 10px;border-radius:20px;}
    .outlook-refresh{padding:5px 13px;border-radius:8px;font-size:12px;font-weight:600;border:1.5px solid var(--accent);background:transparent;color:var(--accent);cursor:pointer;}
    .outlook-refresh:hover{background:var(--accent);color:#fff;}
    .outlook-body{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:28px 32px;max-width:860px;line-height:1.8;font-size:14px;}
    .outlook-body h1{font-size:20px;font-weight:700;margin:0 0 8px;}
    .outlook-body h2{font-size:15px;font-weight:700;margin:22px 0 8px;color:var(--accent);}
    .outlook-body p{margin:0 0 12px;}
    .outlook-body ul,.outlook-body ol{margin:0 0 12px;padding-left:20px;}
    .outlook-body li{margin-bottom:5px;}
    .outlook-body blockquote{border-left:3px solid var(--accent);margin:0 0 12px;padding:6px 14px;color:var(--muted);font-style:italic;background:var(--card);border-radius:0 6px 6px 0;}
    .outlook-empty{text-align:center;padding:60px 20px;color:var(--muted);}
    .outlook-empty .big{font-size:40px;margin-bottom:12px;}
    .mkt-title{font-size:14px;font-weight:700;color:var(--muted);
      margin-bottom:16px;letter-spacing:.04em;}
    .mkt-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:14px;}
    .mkt-card{background:var(--surface);border:1px solid var(--border);
      border-radius:var(--r);overflow:hidden;box-shadow:var(--sh);}
    .mkt-label{padding:8px 12px 0;font-size:11px;font-weight:700;color:var(--muted);}
    /* Rates sub-tabs */
    .rates-toolbar{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:18px;}
    .rates-stab{padding:6px 16px;border-radius:8px;font-size:12px;font-weight:600;
      border:1.5px solid var(--border);background:var(--surface);color:var(--muted);cursor:pointer;}
    .rates-stab.on{background:var(--accent);color:#fff;border-color:var(--accent);}
    .rng-btn{padding:4px 11px;border-radius:99px;font-size:11px;font-weight:700;
      border:1.5px solid var(--border);background:var(--surface);color:var(--muted);cursor:pointer;}
    .rng-btn.on{background:var(--text);color:#fff;border-color:var(--text);}
    .rates-gap{flex:1;}
    .rates-note{font-size:10px;color:var(--faint);}
    .chart-wrap{background:var(--surface);border:1px solid var(--border);
      border-radius:var(--r);padding:18px;box-shadow:var(--sh);position:relative;height:380px;}
    .chart-wrap canvas{max-height:340px;}
    #rates-loading{text-align:center;padding:40px;color:var(--muted);font-size:13px;}
    .spreads-grid{display:grid;grid-template-columns:1fr 1fr;gap:14px;}
    @media(max-width:700px){.spreads-grid{grid-template-columns:1fr;}}
    @media(max-width:600px){
      #maintabs{padding:0 10px;}
      .mtab{padding:9px 11px;font-size:12px;}
      #fx-view{padding:14px 10px 28px;}
      #outlook-view{padding:14px 10px 28px;}
      .outlook-body{padding:18px 16px;}
      .mkt-grid{grid-template-columns:1fr 1fr;}
      .mkt-card iframe{height:220px!important;}
      .tradingview-widget-container{height:220px!important;}
    }
    #stats{display:grid;grid-template-columns:repeat(5,1fr);gap:11px;padding:18px 28px 0;}
    .sc{background:var(--surface);border:1px solid var(--border);border-radius:var(--r);
      padding:11px 13px;display:flex;align-items:center;gap:9px;box-shadow:var(--sh);}
    .sc .sf{font-size:21px;}.sc .sn{font-size:21px;font-weight:700;line-height:1;}
    .sc .sl{font-size:10px;color:var(--faint);margin-top:2px;}
    #fbar{padding:13px 28px;display:flex;align-items:center;gap:7px;flex-wrap:wrap;}
    .flbl{font-size:12px;color:var(--muted);font-weight:500;}
    .chip{padding:4px 12px;border-radius:99px;font-size:12px;font-weight:600;
      border:1.5px solid var(--border);background:var(--surface);
      color:var(--muted);cursor:pointer;transition:all .15s;}
    .chip:hover,.chip.on{background:var(--accent);color:#fff;border-color:var(--accent);}
    .chip.all.on{background:var(--text);border-color:var(--text);}
    .gap{flex:1;}
    #sortsel{padding:5px 10px;border:1.5px solid var(--border);border-radius:8px;
      font-size:12px;color:var(--muted);background:var(--surface);outline:none;cursor:pointer;}
    #grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(340px,1fr));
      gap:17px;padding:0 28px 32px;}
    .card{background:var(--surface);border:1px solid var(--border);border-radius:var(--r);
      overflow:hidden;display:flex;flex-direction:column;
      box-shadow:var(--sh);transition:box-shadow .2s;}
    .card:hover{box-shadow:var(--shh);}
    .card-head{padding:11px 15px;border-bottom:1px solid var(--border);
      display:flex;align-items:center;gap:8px;}
    .c-flag{font-size:20px;}.c-name{font-size:13px;font-weight:700;}
    .c-sub{font-size:10px;color:var(--faint);margin-top:1px;}
    .c-cnt{margin-left:auto;font-size:10px;font-weight:700;
      background:var(--al);color:var(--accent);padding:2px 8px;border-radius:99px;}
    .tabs{display:flex;border-bottom:1px solid var(--border);overflow-x:auto;scrollbar-width:none;}
    .tabs::-webkit-scrollbar{display:none;}
    .tab{padding:7px 12px;font-size:11px;font-weight:600;color:var(--faint);
      cursor:pointer;white-space:nowrap;border:none;
      border-bottom:2px solid transparent;background:none;transition:all .15s;}
    .tab:hover{color:var(--text);}
    .tab.on{color:var(--accent);border-bottom-color:var(--accent);}
    .tab .tbadge{font-size:10px;opacity:.65;margin-left:2px;}
    .nlist{flex:1;}
    .ni{display:block;padding:10px 15px;border-bottom:1px solid #f1f5f9;
      cursor:pointer;transition:background .12s;}
    .ni:last-child{border-bottom:none;}
    .ni:hover{background:#f8fafc;}
    .nt{font-size:12px;font-weight:600;color:var(--text);line-height:1.45;margin-bottom:5px;
      display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;}
    .nm{display:flex;align-items:center;gap:5px;flex-wrap:wrap;}
    .nsrc{font-size:10px;font-weight:700;color:var(--accent);
      background:var(--al);padding:1px 6px;border-radius:4px;}
    .ntime{font-size:10px;color:var(--faint);}
    .stag{font-size:10px;font-weight:700;padding:1px 6px;border-radius:4px;}
    .stag.pos{background:#dcfce7;color:#15803d;}
    .stag.neg{background:#fee2e2;color:#b91c1c;}
    .stag.neu{background:#f1f5f9;color:#64748b;}
    .nd{font-size:11px;color:var(--muted);margin-top:4px;line-height:1.5;
      display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;}
    .loading{padding:26px 15px;text-align:center;}
    .spin{width:24px;height:24px;border-radius:50%;
      border:3px solid var(--border);border-top-color:var(--accent);
      animation:spin .7s linear infinite;margin:0 auto 9px;}
    @keyframes spin{to{transform:rotate(360deg)}}
    .loading p{font-size:12px;color:var(--muted);}
    .empty{padding:26px 15px;text-align:center;}
    .empty .ei{font-size:28px;margin-bottom:7px;}
    .empty .et{font-size:12px;font-weight:600;margin-bottom:3px;}
    .empty .ed{font-size:11px;color:var(--muted);line-height:1.5;}
    #ov{display:none;position:fixed;inset:0;background:rgba(0,0,0,.45);
      z-index:300;align-items:center;justify-content:center;padding:20px;}
    #ov.open{display:flex;}
    #mb{background:#fff;border-radius:16px;max-width:600px;width:100%;
      max-height:80vh;overflow-y:auto;padding:24px;position:relative;
      box-shadow:0 20px 60px rgba(0,0,0,.2);}
    #mc{position:absolute;top:13px;right:13px;background:none;border:none;
      font-size:19px;cursor:pointer;color:var(--faint);}
    #mc:hover{color:var(--text);}
    #mr{font-size:11px;font-weight:700;color:var(--accent);margin-bottom:7px;
      text-transform:uppercase;letter-spacing:.5px;}
    #mt{font-size:18px;font-weight:700;line-height:1.35;margin-bottom:11px;}
    #mm{display:flex;gap:7px;flex-wrap:wrap;margin-bottom:13px;align-items:center;}
    #md{font-size:13px;color:var(--muted);line-height:1.7;margin-bottom:17px;}
    #ml{display:inline-flex;align-items:center;gap:5px;padding:9px 18px;
      background:var(--accent);color:#fff;border-radius:8px;
      text-decoration:none;font-size:12px;font-weight:600;}
    #ml:hover{background:#1d4ed8;}
    /* SUMMARY SECTION */
    #summary{padding:16px 28px 0;}
    .sum-wrap{background:var(--surface);border:1px solid var(--border);
      border-radius:var(--r);box-shadow:var(--sh);overflow:hidden;}
    .sum-header{padding:11px 16px;border-bottom:1px solid var(--border);
      display:flex;align-items:center;gap:8px;}
    .sum-header span{font-size:13px;font-weight:700;color:var(--text);}
    .sum-header small{font-size:11px;color:var(--faint);margin-left:4px;}
    .sum-grid{display:grid;grid-template-columns:repeat(5,1fr);}
    .sum-cell{padding:13px 15px;border-right:1px solid var(--border);}
    .sum-cell:last-child{border-right:none;}
    .sum-flag{font-size:18px;margin-bottom:5px;}
    .sum-region{font-size:11px;font-weight:700;color:var(--text);margin-bottom:6px;}
    .sum-tags{display:flex;flex-wrap:wrap;gap:4px;margin-bottom:7px;}
    .sum-tag{font-size:10px;font-weight:600;padding:2px 7px;border-radius:99px;
      background:var(--al);color:var(--accent);}
    .sum-tag.neg{background:#fee2e2;color:#b91c1c;}
    .sum-tag.pos{background:#dcfce7;color:#15803d;}
    .sum-tag.neu{background:#f1f5f9;color:#64748b;}
    .sum-text{font-size:11px;color:var(--muted);line-height:1.55;}
    .sum-loading{padding:20px;text-align:center;font-size:12px;color:var(--faint);}
    @media(max-width:1100px){.sum-grid{grid-template-columns:repeat(3,1fr);}}
    @media(max-width:700px){.sum-grid{grid-template-columns:repeat(2,1fr);}}
    @media(max-width:900px){
      #stats{grid-template-columns:repeat(3,1fr);}
      header,#stats,#summary,#fbar,#grid{padding-left:14px;padding-right:14px;}
    }
    @media(max-width:580px){#stats{grid-template-columns:repeat(2,1fr);}}

    /* ── Mobile ─────────────────────────────────────── */
    @media(max-width:600px){
      /* Header: stack into 2 rows */
      header{flex-direction:column;align-items:stretch;gap:8px;padding:10px 14px;}
      .hl{justify-content:space-between;}
      .hr{justify-content:space-between;flex-wrap:wrap;gap:6px;}
      /* Hide timestamp on mobile — saves space */
      #upd{display:none;}
      /* Language buttons: smaller, fit in one row */
      .langbar{flex:1;}
      .langbtn{flex:1;padding:5px 4px;font-size:10px;min-width:0;}
      #refbtn{padding:5px 12px;font-size:11px;}
      #live{font-size:10px;padding:3px 7px;}
      /* Cards full width */
      #grid{grid-template-columns:1fr;padding:0 10px 24px;}
      /* Stats 2 col */
      #stats{grid-template-columns:repeat(2,1fr);padding:12px 10px 0;}
      /* Summary single col */
      .sum-grid{grid-template-columns:1fr!important;}
      .sum-cell{border-right:none;border-bottom:1px solid var(--border);}
      .sum-cell:last-child{border-bottom:none;}
      /* Filter bar wraps nicely */
      #fbar{padding:10px 10px;gap:5px;}
      .chip{font-size:11px;padding:4px 9px;}
      /* Summary section padding */
      #summary{padding:12px 10px 0;}
    }
  </style>
</head>
<body>
<header>
  <div class="hl">
    <div class="logo">🌐</div>
    <div>
      <h1>Global Market News Dashboard</h1>
      <p>Live RSS · USA · Korea · China · Europe · LATAM · Middle East</p>
    </div>
  </div>
  <div class="hr">
    <div id="live"><span class="dot"></span> LIVE</div>
    <span id="upd"></span>
    <div class="langbar">
      <button class="langbtn on" onclick="setLang('en',this)">English</button>
      <button class="langbtn" onclick="setLang('ko',this)">한국어</button>
      <button class="langbtn" onclick="setLang('zh',this)">中文</button>
      <button class="langbtn" onclick="setLang('es',this)">Español</button>
    </div>
    <button id="refbtn" onclick="loadAll()">↻ Refresh</button>
  </div>
</header>
<div id="maintabs">
  <button class="mtab on" onclick="switchMainTab('news',this)">📰 News</button>
  <button class="mtab" onclick="switchMainTab('fx',this)">💱 FX</button>
  <button class="mtab" onclick="switchMainTab('outlook',this)">🐟 AI Outlook</button>
</div>
<div class="translating-banner" id="trans-banner">
  <div class="spin" style="width:14px;height:14px;border-width:2px;margin:0"></div>
  Translating headlines…
</div>
<div id="stats">
  <div class="sc"><span class="sf">🇺🇸</span><div><div class="sn" id="s-usa">–</div><div class="sl">USA</div></div></div>
  <div class="sc"><span class="sf">🔥</span><div><div class="sn" id="s-war">–</div><div class="sl">War Zone</div></div></div>
  <div class="sc"><span class="sf">🇪🇺</span><div><div class="sn" id="s-europe">–</div><div class="sl">Europe</div></div></div>
  <div class="sc"><span class="sf">🇰🇷</span><div><div class="sn" id="s-korea">–</div><div class="sl">Korea</div></div></div>
  <div class="sc"><span class="sf">🇨🇳🇯🇵</span><div><div class="sn" id="s-china">–</div><div class="sl">China & Japan</div></div></div>
  <div class="sc"><span class="sf">🌎</span><div><div class="sn" id="s-latam">–</div><div class="sl">LATAM</div></div></div>
</div>
<div id="summary">
  <div class="sum-wrap">
    <div class="sum-header">
      <span id="sum-title">📋 Regional Focus Summary</span>
      <small id="sum-sub">Auto-generated from loaded headlines</small>
    </div>
    <div class="sum-grid" id="sum-grid">
      <div class="sum-loading" id="sum-loading">Loading headlines…</div>
    </div>
  </div>
</div>
<div id="fbar">
  <span class="flbl">Region:</span>
  <button class="chip all on" onclick="setFilter('all',this)">All</button>
  <button class="chip" onclick="setFilter('usa',this)">🇺🇸 USA</button>
  <button class="chip" onclick="setFilter('war',this)">🔥 War Zone</button>
  <button class="chip" onclick="setFilter('europe',this)">🇪🇺 Europe</button>
  <button class="chip" onclick="setFilter('korea',this)">🇰🇷 Korea</button>
  <button class="chip" onclick="setFilter('china',this)">🇨🇳🇯🇵 China & Japan</button>
  <button class="chip" onclick="setFilter('latam',this)">🌎 LATAM</button>
  <div class="gap"></div>
  <select id="sortsel" onchange="renderAll()">
    <option value="newest">Newest first</option>
    <option value="oldest">Oldest first</option>
  </select>
  <button onclick="exportMiroFish()" style="padding:5px 13px;border-radius:8px;font-size:12px;font-weight:600;border:1.5px solid #7c3aed;background:#7c3aed;color:#fff;cursor:pointer;">🐟 Export to MiroFish</button>
</div>
<div id="grid"></div>

<!-- ── FX Tab ── -->
<div id="fx-view">
  <div class="rates-toolbar" style="margin-bottom:16px;">
    <span class="mkt-title" style="margin:0">G10 FX</span>
    <div style="flex:1"></div>
    <button class="rng-btn on" onclick="setFXRange('1d',this)">1D</button>
    <button class="rng-btn"    onclick="setFXRange('1m',this)">1M</button>
    <button class="rng-btn"    onclick="setFXRange('3m',this)">3M</button>
  </div>
  <div class="mkt-grid" id="fx-grid"></div>
</div>

<div id="outlook-view">
  <div class="outlook-header">
    <span class="mkt-title" style="margin:0">🐟 AI Outlook</span>
    <span class="outlook-week" id="outlook-week">Loading...</span>
    <div style="flex:1"></div>
    <button class="outlook-refresh" onclick="loadOutlook()">↻ Refresh</button>
  </div>

  <div id="geo-content" class="outlook-body" style="margin-bottom:20px;max-width:860px;"></div>

  <div id="outlook-content">
    <div class="outlook-empty"><div class="big">🐟</div>Fetching latest AI outlook...</div>
  </div>
</div>

<div id="ov" onclick="if(event.target===this)closeMod()">
  <div id="mb">
    <button id="mc" onclick="closeMod()">✕</button>
    <div id="mr"></div><div id="mt"></div>
    <div id="mm"></div><div id="md"></div>
    <a id="ml" href="#" target="_blank" rel="noopener">Read full article ↗</a>
  </div>
</div>
<script>
const REGIONS = __REGIONS_JSON__;
let data={}, filter='all', activeSrc={};
let currentLang='en', transCache={};

// ── TRANSLATION ────────────────────────────────────────────────
// Google Translate lang codes differ slightly from our internal codes
const GT_LANG={'ko':'ko','zh':'zh-CN','es':'es','en':'en'};

async function translateText(text, toLang){
  if(!text||toLang==='en') return text;
  const key=toLang+'|'+text;
  if(transCache[key]) return transCache[key];
  try{
    const tl=GT_LANG[toLang]||toLang;
    const url='https://translate.googleapis.com/translate_a/single'
      +'?client=gtx&sl=en&tl='+tl+'&dt=t&q='
      +encodeURIComponent(text.slice(0,500));
    const r=await fetch(url);
    const d=await r.json();
    // Response: [[[translated, original, ...], ...], ...]
    const t=(d[0]||[]).map(seg=>seg[0]||'').join('').trim()||text;
    transCache[key]=t;
    return t;
  }catch{return text;}
}

async function translateAll(toLang){
  if(toLang==='en') return;
  // Collect all unique titles + descriptions not yet cached
  const toXlate=new Set();
  for(const region of REGIONS){
    const rd=data[region.id]||{};
    for(const src of region.sources){
      for(const art of (rd[src.name]?.articles||[])){
        if(art.title && !transCache[toLang+'|'+art.title]) toXlate.add(art.title);
        if(art.description && !transCache[toLang+'|'+art.description]) toXlate.add(art.description);
      }
    }
  }
  // Translate fully in parallel — Google Translate handles concurrency fine
  await Promise.all([...toXlate].map(t=>translateText(t,toLang)));
}

function hasNonLatin(text){
  return text && /[\uAC00-\uD7A3\u3400-\u9FFF\u4E00-\u9FFF]/.test(text);
}
function getT(text){
  if(!text) return text;
  if(currentLang==='en'){
    // If text is Korean/Chinese and we have a cached English translation, use it
    if(hasNonLatin(text)) return transCache['en|'+text]||text;
    return text;
  }
  return transCache[currentLang+'|'+text]||text;
}
async function autoTranslateNative(){
  // After load, find any Korean/Chinese titles/descriptions and translate to English
  const toXlate=new Set();
  REGIONS.forEach(r=>{
    Object.values(data[r.id]||{}).forEach(sd=>{
      (sd.articles||[]).forEach(a=>{
        if(hasNonLatin(a.title)&&!transCache['en|'+a.title]) toXlate.add(a.title);
        if(a.description&&hasNonLatin(a.description)&&!transCache['en|'+a.description]) toXlate.add(a.description);
      });
    });
  });
  if(!toXlate.size) return;
  await Promise.all([...toXlate].map(async t=>{
    try{
      const url='https://translate.googleapis.com/translate_a/single'
        +'?client=gtx&sl=auto&tl=en&dt=t&q='+encodeURIComponent(t.slice(0,500));
      const r=await fetch(url);
      const d=await r.json();
      const translated=(d[0]||[]).map(seg=>seg[0]||'').join('').trim();
      if(translated) transCache['en|'+t]=translated;
    }catch(e){}
  }));
  renderAll();renderSummaries();
}

async function setLang(lang, el){
  document.querySelectorAll('.langbtn').forEach(b=>{b.classList.remove('on');b.disabled=true;});
  el.classList.add('on');
  currentLang=lang;
  if(lang!=='en'){
    document.getElementById('trans-banner').classList.add('show');
    await translateAll(lang);
    document.getElementById('trans-banner').classList.remove('show');
  }
  document.querySelectorAll('.langbtn').forEach(b=>b.disabled=false);
  renderAll();renderSummaries();
  if(_outlookLoaded) await refreshOutlookLang(lang);
}

// ── PASTE YOUR MIROFISH REPORT HERE (plain English markdown) ──────────────
const STATIC_OUTLOOK_REPORT = `## Future Forecast Report: Market Response to Geopolitical Risks

Significant price swings are expected in the consumer staples and energy sectors as institutional investors and retail traders turn to defensive stocks amid heightened geopolitical risks.

---

## Market Status and Trends

Against the background of intensified geopolitical risks, market status and trends have shown significant changes. Investor sentiment has been affected, with institutional investors, retail traders and hedge funds reacting differently, and the overall market is tilting towards defensive assets.

In the current market environment, institutional investors generally turn to defensive stocks, such as Coca-Cola (KO) and Procter & Gamble (PG), to cope with uncertainty. Analysts believe that these companies provide relatively stable investment options during economic fluctuations.

At the same time, stocks in the energy sector, such as Exxon Mobil (XOM) and Chevron (CVX), have also become the focus of market attention and are expected to experience significant fluctuations due to changes in the geopolitical situation.

### Investor Reaction

- **Institutional Investors:** Tend to adjust portfolios and focus on defensive stocks to reduce risk.
- **Retail Traders:** May follow the strategies of institutional investors and increase investment in consumer goods.
- **Hedge Funds:** Focus on small-cap stocks like Radian Group (RDN) and Hovnanian Enterprises (HOV) as a strategy to fight inflation.

### Expected Price Fluctuations (Next 1-5 Trading Days)

- **Exxon Mobil (XOM)** - High: Expect greater market volatility due to geopolitical tensions.
- **Chevron (CVX)** - High: Significant fluctuations expected; investors should monitor closely.
- **Coca-Cola (KO)** - Medium: Defensive stock, expected to remain relatively stable.
- **Procter & Gamble (PG)** - Medium: Consumer products giant likely to attract investor attention.
- **Radian Group (RDN)** - Low: Small-cap opportunity amid volatility, but carries higher risk.

---

## Responses from Various Agents

### Institutional Investors

Institutional investors are actively adjusting their portfolios toward defensive stocks. MarketWatch noted that strategically shifting toward Coca-Cola and Procter & Gamble may provide greater safety as geopolitical tensions rise. The United Nations also recommended that institutional investors consider Coca-Cola as a safer investment option.

### Retail Traders

Retail traders are often influenced by institutional behavior and are beginning to follow their strategies. This bandwagon effect could lead to increased trading volume and price volatility in consumer staples in the short term.

### Hedge Funds

Hedge funds are displaying a more sophisticated strategy, looking for opportunities in small-cap stocks in addition to defensive positions. Radian Group (RDN) and Hovnanian Enterprises (HOV) are considered small-cap names that could perform well amid current economic uncertainty. One hedge fund manager noted: "We are focusing on small-cap stocks, especially companies that can survive in a high-inflation environment."

---

## Future Trends and Risks

### Investor Sentiment and Market Dynamics

As geopolitical tensions rise, institutional demand for defensive stocks will intensify, further driving prices higher. Retail bandwagon behavior may cause sharp increases in demand for consumer stocks, amplifying price volatility.

Hedge funds will adopt diversified strategies, balancing defensive holdings with small-cap exposure, to remain flexible in volatile markets.

### Price Outlook Summary

- **Exxon Mobil (XOM)** - High: Greater volatility expected from geopolitical exposure.
- **Chevron (CVX)** - High: Significant fluctuations anticipated; close monitoring required.
- **Coca-Cola (KO)** - Medium: Defensive positioning; relative stability expected.
- **Procter & Gamble (PG)** - Medium: Likely to attract safe-haven interest.
- **Radian Group (RDN)** - Low: Small-cap upside potential with elevated risk.

Overall, market trends in the coming days will be significantly shaped by investor behavior. Defensive stocks and selective small-cap names will be the focus. Investors should remain vigilant and monitor geopolitical developments and their downstream impact on energy and consumer sectors closely.`;
// ─────────────────────────────────────────────────────────────────────────

// Geopolitical briefing — stored as markdown so it can be translated
const GEO_BRIEFING_EN = `## 🌍 Geopolitical Briefing — U.S. / Iran

Based on the current geopolitical climate and the recent ultimatum issued by Trump to Iran, the following predictions can be made:

1. **Increased Tensions:** The ultimatum may escalate tensions between the U.S. and Iran, potentially leading to a more aggressive stance from both sides. Iran could respond with defiance or retaliatory actions, further straining relations.

2. **Military Mobilization:** The U.S. might increase its military presence in the region as a show of force, which could provoke Iran to take more assertive actions, including military posturing or proxy engagements in neighboring countries.

3. **Diplomatic Efforts:** There may be attempts from other nations to mediate and de-escalate the situation, especially from allies in the region who are concerned about the potential for conflict.

4. **Market Reactions:** Financial markets, especially in energy and defense sectors, may react to the heightened uncertainty. Companies in these sectors could see increased volatility as investors respond to news and developments.

5. **Long-term Implications:** If the situation escalates into conflict, it could have long-term implications for regional stability, global oil prices, and international relations, potentially drawing in other nations.

Overall, the situation is fluid and developments will depend on the responses from both the U.S. and Iran, as well as the reactions from the international community. Investors and stakeholders should remain vigilant and monitor the situation closely.`;

let _outlookMdEn = '';

async function _translateLines(lines, tl){
  return Promise.all(lines.map(async line => {
    if(!line.trim()) return line;
    try {
      const url = 'https://translate.googleapis.com/translate_a/single'
        + '?client=gtx&sl=en&tl=' + tl + '&dt=t&q=' + encodeURIComponent(line.slice(0,500));
      const r = await fetch(url);
      const d = await r.json();
      return (d[0]||[]).map(s=>s[0]||'').join('').trim() || line;
    } catch { return line; }
  }));
}

async function refreshOutlookLang(lang){
  const geo = document.getElementById('geo-content');
  const content = document.getElementById('outlook-content');
  if(lang === 'en'){
    if(geo) geo.innerHTML = mdToHtml(GEO_BRIEFING_EN);
    if(_outlookMdEn) content.innerHTML = '<div class="outlook-body">' + mdToHtml(_outlookMdEn) + '</div>';
    return;
  }
  const GT_MAP = {'ko':'ko','zh':'zh-CN','es':'es'};
  const tl = GT_MAP[lang] || lang;
  // translate geo briefing
  if(geo){
    const geoTranslated = await _translateLines(GEO_BRIEFING_EN.split('\n'), tl);
    geo.innerHTML = mdToHtml(geoTranslated.join('\n'));
  }
  // translate report
  if(_outlookMdEn){
    const reportTranslated = await _translateLines(_outlookMdEn.split('\n'), tl);
    content.innerHTML = '<div class="outlook-body">' + mdToHtml(reportTranslated.join('\n')) + '</div>';
  }
}

// ── THEME ENGINE ──────────────────────────────────────────────
const THEMES = [
  {label:'Interest Rates', tone:'neu', keys:['rate','rates','fed','ecb','central bank','monetary policy','boe','boj','rate hike','rate cut','pivot','fed funds','tightening','easing']},
  {label:'Inflation',      tone:'neg', keys:['inflation','cpi','deflation','consumer price','price surge','price rise','cost of living','pce']},
  {label:'Bonds & Yields', tone:'neu', keys:['bond','yield','treasury','gilt','sovereign debt','credit spread','fixed income','maturity']},
  {label:'Stock Markets',  tone:'neu', keys:['stock','equity','share','nasdaq','s&p','dow','kospi','hang seng','dax','ftse','nikkei','index','rally','selloff']},
  {label:'Trade & Tariffs',tone:'neg', keys:['trade','tariff','export','import','sanction','trade war','wto','trade deal','supply chain']},
  {label:'GDP & Growth',   tone:'neu', keys:['gdp','growth','recession','economy','economic growth','slowdown','contraction','expansion','output']},
  {label:'Earnings',       tone:'neu', keys:['earnings','profit','revenue','net income','quarterly','results','beat','miss','guidance']},
  {label:'Currency/FX',   tone:'neu', keys:['dollar','yuan','rmb','yen','euro','won','currency','forex','exchange rate','devaluation','appreciation']},
  {label:'Energy/Oil',    tone:'neu', keys:['oil','crude','energy','gas','brent','opec','petroleum','lng','renewable','coal']},
  {label:'Banking',       tone:'neu', keys:['bank','banking','lender','loan','credit','mortgage','liquidity','capital','basel','financial']},
  {label:'Tech & AI',     tone:'pos', keys:['ai','artificial intelligence','semiconductor','chip','tech','technology','digital','cyber']},
  {label:'Geopolitics',   tone:'neg', keys:['war','conflict','sanction','geopolit','tension','ukraine','taiwan','middle east','nato','dispute']},
];

// Pre-translated theme labels (no API needed for fixed labels)
const THEME_TRANS = {
  ko: {'Interest Rates':'금리','Inflation':'인플레이션','Bonds & Yields':'채권·금리','Stock Markets':'주식시장','Trade & Tariffs':'무역·관세','GDP & Growth':'GDP·성장','Earnings':'실적','Currency/FX':'환율','Energy/Oil':'에너지·원유','Banking':'금융','Tech & AI':'기술·AI','Geopolitics':'지정학'},
  zh: {'Interest Rates':'利率','Inflation':'通货膨胀','Bonds & Yields':'债券·收益率','Stock Markets':'股票市场','Trade & Tariffs':'贸易·关税','GDP & Growth':'GDP·增长','Earnings':'业绩','Currency/FX':'汇率','Energy/Oil':'能源·石油','Banking':'银行','Tech & AI':'科技·AI','Geopolitics':'地缘政治'},
  es: {'Interest Rates':'Tasas de Interés','Inflation':'Inflación','Bonds & Yields':'Bonos·Rendimientos','Stock Markets':'Bolsa','Trade & Tariffs':'Comercio·Aranceles','GDP & Growth':'PIB·Crecimiento','Earnings':'Resultados','Currency/FX':'Divisas','Energy/Oil':'Energía·Petróleo','Banking':'Banca','Tech & AI':'Tecnología·IA','Geopolitics':'Geopolítica'},
};
function getThemeLabel(label){
  if(currentLang==='en') return label;
  return THEME_TRANS[currentLang]?.[label]||label;
}

// Pre-translated UI strings
const UI = {
  en: {summary:'📋 Regional Focus Summary', subhead:'Auto-generated from loaded headlines', nodata:'No data loaded.', stories:(n,themes)=>`${n} stories covering ${themes}.`, latest:'Latest', loading:'Loading headlines…', sentiment:{pos:'▲ Bullish tone',neg:'▼ Bearish tone',neu:'● Mixed tone'}},
  ko: {summary:'📋 지역별 핵심 요약', subhead:'로드된 헤드라인에서 자동 생성', nodata:'데이터 없음.', stories:(n,themes)=>`${n}개 기사 · 주요 테마: ${themes}.`, latest:'최신', loading:'헤드라인 로딩 중…', sentiment:{pos:'▲ 강세', neg:'▼ 약세', neu:'● 혼조'}},
  zh: {summary:'📋 地区重点摘要', subhead:'根据已加载标题自动生成', nodata:'暂无数据。', stories:(n,themes)=>`${n}篇报道，涵盖${themes}。`, latest:'最新', loading:'正在加载头条…', sentiment:{pos:'▲ 看涨', neg:'▼ 看跌', neu:'● 震荡'}},
  es: {summary:'📋 Resumen Regional', subhead:'Generado automáticamente', nodata:'Sin datos.', stories:(n,themes)=>`${n} artículos sobre ${themes}.`, latest:'Último', loading:'Cargando titulares…', sentiment:{pos:'▲ Alcista', neg:'▼ Bajista', neu:'● Mixto'}},
};
function uiStr(){ return UI[currentLang]||UI.en; }

function summariseRegion(regionId) {
  const rd = data[regionId] || {};
  const articles = Object.values(rd).flatMap(s => s.articles || []);
  if (!articles.length) return null;

  const corpus = articles.map(a => (a.title+' '+(a.description||'')).toLowerCase());

  // Score themes AND attach matching articles to each theme
  const scored = THEMES.map(t => {
    const matches = articles.filter(a =>
      t.keys.some(k => (a.title+' '+(a.description||'')).toLowerCase().includes(k))
    );
    return { ...t, score: matches.length, examples: matches };
  }).filter(t => t.score > 0).sort((a,b) => b.score - a.score);

  const topThemes = scored.slice(0, 4);

  // Overall sentiment
  const posCount = articles.filter(a => sentiment(a.title+' '+a.description)==='pos').length;
  const negCount = articles.filter(a => sentiment(a.title+' '+a.description)==='neg').length;
  const overall = posCount > negCount*1.3 ? 'pos' : negCount > posCount*1.3 ? 'neg' : 'neu';
  const ui = uiStr();
  const sentimentLabel = ui.sentiment[overall];

  // Most recent article
  const withDates = articles.filter(a => a.publishedAt);
  withDates.sort((a,b) => new Date(b.publishedAt) - new Date(a.publishedAt));
  const latest = withDates[0] || articles[0];

  function clip(str, n) {
    if (!str) return '';
    return str.length > n ? str.slice(0, n).trimEnd() + '…' : str;
  }

  // Build brief using translated template strings + translated theme labels + translated headline
  const themeNames = topThemes.slice(0, 3).map(t => getThemeLabel(t.label));
  let brief = themeNames.length === 0
    ? `${articles.length} stories.`
    : ui.stories(articles.length, themeNames.join(', '));
  if (latest?.title) {
    brief += ` ${ui.latest}: "${clip(getT(latest.title), 85)}".`;
  }

  return { topThemes, overall, sentimentLabel, brief, count: articles.length };
}

function renderSummaries() {
  const ui = uiStr();
  // Update header text
  const hdr = document.getElementById('sum-title');
  if(hdr) hdr.textContent = ui.summary;
  const sub = document.getElementById('sum-sub');
  if(sub) sub.textContent = ui.subhead;

  const grid = document.getElementById('sum-grid');
  const cells = REGIONS.map(region => {
    const s = summariseRegion(region.id);
    if (!s) return `<div class="sum-cell">
      <div class="sum-flag">${region.flag}</div>
      <div class="sum-region">${region.name}</div>
      <div class="sum-text" style="color:var(--faint)">${ui.nodata}</div>
    </div>`;

    const tags = s.topThemes.slice(0,3).map(t =>
      `<span class="sum-tag ${t.tone}">${esc(getThemeLabel(t.label))}</span>`
    ).join('');

    return `<div class="sum-cell">
      <div class="sum-flag">${region.flag}</div>
      <div class="sum-region">${region.name} <span class="sum-tag ${s.overall}" style="margin-left:2px">${s.sentimentLabel}</span></div>
      <div class="sum-tags">${tags}</div>
      <div class="sum-text">${esc(s.brief)}</div>
    </div>`;
  }).join('');
  grid.innerHTML = cells;
}

async function fetchFeed(src) {
  const res = await fetch('/api/feed?url=' + encodeURIComponent(src.url));
  if (!res.ok) {
    const e = await res.json().catch(()=>({error:'HTTP '+res.status}));
    throw new Error(e.error||'HTTP '+res.status);
  }
  return parseXML(await res.text(), src.name);
}

function parseXML(xml, srcName) {
  const doc = new DOMParser().parseFromString(xml, 'text/xml');
  if (doc.querySelector('parsererror')) throw new Error('Invalid XML');
  return Array.from(doc.querySelectorAll('item,entry')).slice(0,10).map(el => {
    const g = t => el.querySelector(t)?.textContent?.trim()||'';
    const link = el.querySelector('link')?.textContent?.trim()
              || el.querySelector('link')?.getAttribute('href')||'';
    return {
      title: g('title'),
      description: stripHtml(g('description')||g('summary')||g('content')),
      url: link,
      publishedAt: g('pubDate')||g('published')||g('updated')||'',
      source: srcName,
    };
  }).filter(a=>a.title);
}

function stripHtml(h){
  if(!h)return'';
  const d=document.createElement('div');
  d.innerHTML=h;
  return(d.textContent||'').replace(/\s+/g,' ').trim().slice(0,240);
}

async function loadAll(){
  transCache={};  // clear translation cache on refresh
  const btn=document.getElementById('refbtn');
  btn.disabled=true;btn.textContent='↻ Loading…';
  renderSkeleton();
  await Promise.allSettled(REGIONS.map(async region=>{
    data[region.id]={};
    if(!activeSrc[region.id])activeSrc[region.id]=region.sources[0].name;
    await Promise.allSettled(region.sources.map(async src=>{
      try{
        const articles=await fetchFeed(src);
        data[region.id][src.name]={articles,error:null};
      }catch(e){
        data[region.id][src.name]={articles:[],error:e.message};
      }
    }));
    const good=region.sources.find(s=>data[region.id][s.name]?.articles?.length>0);
    if(good)activeSrc[region.id]=good.name;
  }));
  updateStats();renderAll();renderSummaries();
  document.getElementById('upd').textContent='Updated '+new Date().toLocaleTimeString();
  btn.disabled=false;btn.textContent='↻ Refresh';
  autoTranslateNative();
}

function renderSkeleton(){
  document.getElementById('grid').innerHTML=REGIONS.map(r=>`
    <div class="card">
      <div class="card-head">
        <span class="c-flag">${r.flag}</span>
        <div><div class="c-name">${r.name}</div></div>
      </div>
      <div class="loading"><div class="spin"></div><p>Loading…</p></div>
    </div>`).join('');
}

function renderAll(){
  const shown=filter==='all'?REGIONS:REGIONS.filter(r=>r.id===filter);
  document.getElementById('grid').innerHTML=shown.map(renderCard).join('');
}

function renderCard(region){
  const rd=data[region.id]||{};
  const sort=document.getElementById('sortsel').value;
  const cur=activeSrc[region.id]||region.sources[0].name;
  const sd=rd[cur]||{articles:[],error:'Not loaded'};

  const tabs=region.sources.map(s=>{
    const cnt=rd[s.name]?.articles?.length||0;
    const hasErr=rd[s.name]?.error&&!cnt;
    return`<button class="tab${s.name===cur?' on':''}${hasErr?' err':''}"
      onclick="switchSrc('${region.id}','${esc(s.name)}');event.stopPropagation()">
      ${esc(s.name)}<span class="tbadge">(${cnt})</span></button>`;
  }).join('');

  let articles=[...(sd.articles||[])];
  if(sort==='newest')articles.sort((a,b)=>new Date(b.publishedAt)-new Date(a.publishedAt));
  else articles.sort((a,b)=>new Date(a.publishedAt)-new Date(b.publishedAt));

  const total=Object.values(rd).reduce((n,s)=>n+(s.articles?.length||0),0);

  let body;
  if(sd.error&&!articles.length){
    body=`<div class="empty">
      <div class="ei">⚠️</div>
      <div class="et">Feed unavailable</div>
      <div class="ed">${esc(sd.error)}</div>
    </div>`;
  }else if(!articles.length){
    body=`<div class="empty"><div class="ei">📭</div><div class="et">No articles</div></div>`;
  }else{
    body='<div class="nlist">'+articles.slice(0,6).map((art,i)=>{
      const s=sentiment(art.title+' '+art.description);
      const title=getT(art.title);
      return`<div class="ni" onclick="openMod('${region.id}','${esc(cur)}',${i})">
        <div class="nt">${esc(title)}</div>
        <div class="nm">
          <span class="nsrc">${esc(art.source)}</span>
          <span class="ntime">${timeAgo(art.publishedAt)}</span>
          <span class="stag ${s}">${sentLbl(s)}</span>
        </div>
        ${art.description?`<div class="nd">${esc(getT(art.description))}</div>`:''}
      </div>`;
    }).join('')+'</div>';
  }

  return`<div class="card" data-region="${region.id}">
    <div class="card-head">
      <span class="c-flag">${region.flag}</span>
      <div><div class="c-name">${region.name}</div></div>
      <span class="c-cnt">${total} articles</span>
    </div>
    <div class="tabs">${tabs}</div>${body}
  </div>`;
}

function switchSrc(rid,name){activeSrc[rid]=name;renderAll();}

function setFilter(val,el){
  filter=val;
  document.querySelectorAll('.chip').forEach(c=>c.classList.remove('on'));
  el.classList.add('on');renderAll();
}

function updateStats(){
  REGIONS.forEach(r=>{
    const n=Object.values(data[r.id]||{}).reduce((t,s)=>t+(s.articles?.length||0),0);
    const el=document.getElementById('s-'+r.id);
    if(el)el.textContent=n||'–';
  });
}

async function openMod(rid,srcName,idx){
  const region=REGIONS.find(r=>r.id===rid);
  const sd=data[rid]?.[srcName];if(!sd?.articles)return;
  const sort=document.getElementById('sortsel').value;
  let arts=[...sd.articles];
  if(sort==='newest')arts.sort((a,b)=>new Date(b.publishedAt)-new Date(a.publishedAt));
  else arts.sort((a,b)=>new Date(a.publishedAt)-new Date(b.publishedAt));
  const art=arts[idx];if(!art)return;
  const s=sentiment(art.title+' '+art.description);
  document.getElementById('mr').textContent=`${region.flag}  ${region.name}`;
  document.getElementById('mt').textContent=getT(art.title);
  document.getElementById('mm').innerHTML=`
    <span class="nsrc">${esc(art.source)}</span>
    <span class="ntime">${art.publishedAt?new Date(art.publishedAt).toLocaleString():''}</span>
    <span class="stag ${s}">${sentLbl(s)}</span>`;
  // Show description immediately (original), then replace with translation if needed
  const descEl=document.getElementById('md');
  const origDesc=art.description||'No preview available.';
  descEl.textContent=getT(origDesc)||origDesc;
  if(currentLang!=='en'&&art.description&&!transCache[currentLang+'|'+art.description]){
    descEl.textContent='…';
    descEl.textContent=await translateText(art.description,currentLang)||origDesc;
  }
  const ml=document.getElementById('ml');
  ml.href=art.url||'#';ml.style.display=art.url?'':'none';
  document.getElementById('ov').classList.add('open');
  document.body.style.overflow='hidden';
}
function closeMod(){
  document.getElementById('ov').classList.remove('open');
  document.body.style.overflow='';
}
document.addEventListener('keydown',e=>{if(e.key==='Escape')closeMod();});

const POS=['rally','rise','gain','surge','boost','strong','recover','growth','bullish','upgrade','record','high'];
const NEG=['fall','drop','decline','risk','default','downgrade','crisis','fear','bearish','cut','recession','crash','weak','loss'];
function sentiment(txt){
  const t=(txt||'').toLowerCase();
  return POS.filter(w=>t.includes(w)).length > NEG.filter(w=>t.includes(w)).length ? 'pos'
       : NEG.filter(w=>t.includes(w)).length > POS.filter(w=>t.includes(w)).length ? 'neg' : 'neu';
}
function sentLbl(s){return s==='pos'?'▲ Bullish':s==='neg'?'▼ Bearish':'● Neutral';}
function timeAgo(d){
  if(!d)return'';
  const s=Math.floor((Date.now()-new Date(d))/1000);
  if(s<60)return s+'s ago';
  if(s<3600)return Math.floor(s/60)+'m ago';
  if(s<86400)return Math.floor(s/3600)+'h ago';
  return Math.floor(s/86400)+'d ago';
}
function esc(s){
  return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;')
    .replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');
}
async function exportMiroFish(){
  const now = new Date();
  const dateStr = now.toISOString().slice(0,10);
  const timeStr = now.toLocaleTimeString();
  let lines = [];
  lines.push('# Global News Seed — MiroFish Simulation Input');
  lines.push('# Generated: ' + dateStr + ' ' + timeStr);
  lines.push('# Source: Global Pulse Dashboard');
  lines.push('');
  lines.push('## INSTRUCTIONS FOR MIROFISH');
  lines.push('Upload this document as seed material. Ask MiroFish to simulate');
  lines.push('how these global news events interact and predict near-term outcomes');
  lines.push('in financial markets, geopolitics, and public sentiment.');
  lines.push('');
  lines.push('---');
  lines.push('');

  // ── FX Snapshot ──────────────────────────────────────────────
  lines.push('## 💱 G10 FX SNAPSHOT (Timeframe: ' + _fxRange.toUpperCase() + ')');
  lines.push('');
  const FX_DISPLAY = [
    ['EUR/USD','EUR',true], ['GBP/USD','GBP',true], ['USD/JPY','JPY',false],
    ['USD/CHF','CHF',false], ['AUD/USD','AUD',true], ['USD/CAD','CAD',false],
    ['NZD/USD','NZD',true],  ['USD/SEK','SEK',false], ['USD/NOK','NOK',false],
    ['USD/DKK','DKK',false], ['USD/KRW','KRW',false], ['USD/CNH','CNY',false],
  ];
  try {
    const fxResp = await fetch('https://api.frankfurter.app/latest?from=USD&to=EUR,GBP,JPY,CHF,CAD,AUD,NZD,SEK,NOK,DKK,KRW,CNY');
    const fxData = await fxResp.json();
    const rates = fxData.rates || {};
    FX_DISPLAY.forEach(([label, key, inv]) => {
      const raw = rates[key];
      if(raw != null){
        const dp = (key==='JPY'||key==='KRW'||key==='SEK'||key==='NOK'||key==='DKK') ? 2 : 4;
        const val = inv ? (1/raw).toFixed(dp) : parseFloat(raw).toFixed(dp);
        lines.push('- ' + label + ': ' + val);
      } else {
        lines.push('- ' + label + ': N/A');
      }
    });
    lines.push('   [Source: Frankfurter / ECB | Date: ' + (fxData.date||dateStr) + ']');
  } catch(e) {
    FX_DISPLAY.forEach(([label]) => lines.push('- ' + label + ': data unavailable'));
    lines.push('   [FX fetch failed — include manually if needed]');
  }
  lines.push('');
  lines.push('---');
  lines.push('');

  // ── News by Region ────────────────────────────────────────────
  REGIONS.forEach(region => {
    const rd = data[region.id] || {};
    const arts = [];
    Object.entries(rd).forEach(([srcName, sd]) => {
      (sd.articles || []).forEach(a => {
        const t = (getT(a.title)||'').trim();
        const d = (getT(a.description)||'').trim();
        if(t) arts.push({title:t, desc:d, src:srcName, date:a.publishedAt});
      });
    });
    if(!arts.length) return;
    arts.sort((a,b)=>new Date(b.date)-new Date(a.date));
    lines.push('## ' + region.flag + ' ' + region.name.toUpperCase());
    arts.slice(0,10).forEach((a,i)=>{
      lines.push((i+1)+'. ' + a.title);
      if(a.desc && a.desc !== a.title) lines.push('   > ' + a.desc.slice(0,200));
      lines.push('   [Source: ' + a.src + (a.date ? ' | ' + new Date(a.date).toLocaleDateString() : '') + ']');
      lines.push('');
    });
    lines.push('---');
    lines.push('');
  });
  const text = lines.join('\n');
  // Download as .txt
  const blob = new Blob([text], {type:'text/plain'});
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = 'mirofish-seed-' + dateStr + '.txt';
  a.click();
  URL.revokeObjectURL(url);
}

// ── Yield Candlestick Charts ──────────────────────────────────
let _yieldsData   = null;
let _yieldsLoaded = false;
let _activeRange  = '1m';

const CCY_FLAGS = {USD:'🇺🇸',EUR:'🇪🇺',GBP:'🇬🇧',JPY:'🇯🇵',CAD:'🇨🇦',AUD:'🇦🇺'};

function filterOHLC(rows, range){
  const now=new Date(), cutoff=new Date(now);
  cutoff.setMonth(cutoff.getMonth()-({'1m':1,'3m':3,'6m':6,'1y':12}[range]||1));
  return (rows||[]).filter(r=>new Date(r.date)>=cutoff);
}

async function loadYields(range, btn){
  if(btn){
    document.querySelectorAll('.rng-btn').forEach(b=>b.classList.remove('on'));
    btn.classList.add('on');
  }
  if(range) _activeRange=range;
  if(!_yieldsLoaded){
    const ld=document.getElementById('rates-loading');
    ld.style.display='block';
    ld.textContent='⏳ Loading yield data from Yahoo Finance…';
    try{
      const resp=await fetch('/api/yields');
      const d=await resp.json();
      _yieldsData=d.curves;
      _yieldsLoaded=true;
    }catch(e){
      ld.textContent='⚠️ Failed to load yield data. Check server logs.';
      return;
    }
    ld.style.display='none';
  }
  renderCandleCharts();
}

function setYieldRange(range, btn){ loadYields(range, btn); }

function renderCandleCharts(){
  const grid=document.getElementById('rates-candle-grid');
  grid.innerHTML='';
  const CCY_ORDER=['USD','EUR','GBP','JPY','CAD','AUD'];
  CCY_ORDER.forEach(ccy=>{
    const rows=_yieldsData[ccy];
    if(!rows) return;
    const filtered=filterOHLC(rows, _activeRange);
    if(!filtered.length) return;
    const wrap=document.createElement('div');
    wrap.style.cssText='background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:12px;box-shadow:0 1px 4px rgba(0,0,0,.07);';
    const divId='candle-'+ccy;
    wrap.innerHTML=`<div style="font-size:12px;font-weight:700;color:#64748b;margin-bottom:4px">${CCY_FLAGS[ccy]} ${ccy} 10Y Treasury Yield</div><div id="${divId}" style="height:260px"></div>`;
    grid.appendChild(wrap);
    Plotly.newPlot(divId,[{
      type:'candlestick',
      x:filtered.map(r=>r.date),
      open:filtered.map(r=>r.o),
      high:filtered.map(r=>r.h),
      low:filtered.map(r=>r.l),
      close:filtered.map(r=>r.c),
      increasing:{line:{color:'#22c55e'},fillcolor:'#22c55e'},
      decreasing:{line:{color:'#ef4444'},fillcolor:'#ef4444'},
      name:ccy,
    }],{
      margin:{t:10,r:20,b:40,l:50},
      paper_bgcolor:'transparent',
      plot_bgcolor:'transparent',
      yaxis:{title:'Yield (%)',tickformat:'.2f',gridcolor:'#e2e8f0',zerolinecolor:'#e2e8f0'},
      xaxis:{rangeslider:{visible:false},gridcolor:'#e2e8f0'},
      showlegend:false,
    },{responsive:true,displayModeBar:false});
  });
}

// ── Market tab data ──────────────────────────────────────────
// Format: [displayName, "EXCHANGE:SYMBOL"]
const FX_PAIRS = [
  ['EUR/USD',  'FX:EURUSD'],
  ['GBP/USD',  'FX:GBPUSD'],
  ['USD/JPY',  'FX:USDJPY'],
  ['USD/CHF',  'FX:USDCHF'],
  ['AUD/USD',  'FX:AUDUSD'],
  ['USD/CAD',  'FX:USDCAD'],
  ['NZD/USD',  'FX:NZDUSD'],
  ['USD/SEK',  'FX:USDSEK'],
  ['USD/NOK',  'FX:USDNOK'],
  ['USD/DKK',  'FX:USDDKK'],
  ['USD/KRW',  'FX:USDKRW'],
  ['USD/CNH',  'FX:USDCNH'],
];

// ── FX range state ───────────────────────────────────────────
let _fxRange = '1d';   // default: 1-day view
// TradingView dateRange strings: "period|resolution"
const FX_RANGE_CFG = {
  '1d': {dateRange: '1d|60',  res: '60'},   // 1 day, 60-min candles
  '1m': {dateRange: '1m|1D',  res: '1D'},   // 1 month, daily candles
  '3m': {dateRange: '3m|1D',  res: '1D'},   // 3 months, daily candles
};

function buildTVChart(pair){
  const [label, sym] = pair;
  const {dateRange} = FX_RANGE_CFG[_fxRange] || FX_RANGE_CFG['1d'];
  const cfg = {
    symbols: [[label, sym + '|1D']],
    chartOnly: false,
    width: '100%',
    height: 280,
    locale: 'en',
    colorTheme: 'light',
    autosize: false,
    showVolume: false,
    showMA: false,
    hideDateRanges: true,
    hideMarketStatus: false,
    hideSymbolLogo: true,
    scalePosition: 'right',
    scaleMode: 'Normal',
    fontSize: '10',
    noTimeScale: false,
    valuesTracking: '1',
    changeMode: 'price-and-percent',
    chartType: 'candlesticks',
    upColor: '#22c55e',
    downColor: '#ef4444',
    borderUpColor: '#22c55e',
    borderDownColor: '#ef4444',
    wickUpColor: '#22c55e',
    wickDownColor: '#ef4444',
    dateRanges: [dateRange],
  };
  const wrap = document.createElement('div');
  wrap.className = 'mkt-card';
  const container = document.createElement('div');
  container.className = 'tradingview-widget-container';
  container.style.height = '280px';
  const inner = document.createElement('div');
  inner.className = 'tradingview-widget-container__widget';
  inner.style.height = '100%';
  const script = document.createElement('script');
  script.type = 'text/javascript';
  script.src = 'https://s3.tradingview.com/external-embedding/embed-widget-symbol-overview.js';
  script.async = true;
  script.textContent = JSON.stringify(cfg);
  container.appendChild(inner);
  container.appendChild(script);
  wrap.appendChild(container);
  return wrap;
}

function setFXRange(range, btn){
  _fxRange = range;
  document.querySelectorAll('.rng-btn').forEach(b => b.classList.remove('on'));
  btn.classList.add('on');
  // Destroy and rebuild all FX charts with new range
  const grid = document.getElementById('fx-grid');
  grid.innerHTML = '';
  FX_PAIRS.forEach(p => grid.appendChild(buildTVChart(p)));
}

let _currentMainTab = 'news';
function switchMainTab(tab, btn){
  _currentMainTab = tab;
  document.querySelectorAll('.mtab').forEach(b=>b.classList.remove('on'));
  btn.classList.add('on');
  const newsEls = ['stats','summary','fbar','grid'];
  const isNews = tab==='news';
  newsEls.forEach(id=>{ const el=document.getElementById(id); if(el) el.style.display = isNews ? '' : 'none'; });
  document.getElementById('trans-banner').style.display = isNews ? '' : 'none';
  document.getElementById('fx-view').style.display = tab==='fx' ? 'block' : 'none';
  document.getElementById('outlook-view').style.display = tab==='outlook' ? 'block' : 'none';
  if(tab==='fx'){
    const grid = document.getElementById('fx-grid');
    if(!grid.children.length) FX_PAIRS.forEach(p => grid.appendChild(buildTVChart(p)));
  }
  if(tab==='outlook') loadOutlook();
}

// ── AI Outlook (MiroFish) ─────────────────────────────────────
let _outlookLoaded = false;
function mdToHtml(md){
  if(!md) return '';
  return md
    .replace(/^### (.+)$/gm,'<h3>$1</h3>')
    .replace(/^## (.+)$/gm,'<h2>$1</h2>')
    .replace(/^# (.+)$/gm,'<h1>$1</h1>')
    .replace(/\*\*(.+?)\*\*/g,'<strong>$1</strong>')
    .replace(/\*(.+?)\*/g,'<em>$1</em>')
    .replace(/^> (.+)$/gm,'<blockquote>$1</blockquote>')
    .replace(/^\d+\. (.+)$/gm,'<li>$1</li>')
    .replace(/^[-*] (.+)$/gm,'<li>$1</li>')
    .replace(/(<li>.*<\/li>\n?)+/g, s => '<ul>'+s+'</ul>')
    .replace(/\n\n/g,'</p><p>')
    .replace(/^(?!<[hbul])/gm, '<p>')
    .replace(/(?<![>])$/gm, '</p>')
    .replace(/<p><\/p>/g,'')
    .replace(/<p>(<[hbul])/g,'$1')
    .replace(/(<\/[hbul][^>]*>)<\/p>/g,'$1');
}
async function translateChunkToEn(text){
  if(!text || !text.trim()) return text;
  try {
    const url = 'https://translate.googleapis.com/translate_a/single'
      + '?client=gtx&sl=auto&tl=en&dt=t&q=' + encodeURIComponent(text.slice(0, 1000));
    const r = await fetch(url);
    const d = await r.json();
    return (d[0]||[]).map(s=>s[0]||'').join('').trim() || text;
  } catch { return text; }
}
function fixMixedChinese(md){
  // Replace common mixed-language conviction markers and brackets
  return md
    .replace(/（/g,'(').replace(/）/g,')')
    .replace(/\s*-\s*高(\s|$)/g,' - High$1')
    .replace(/\s*-\s*中(\s|$)/g,' - Medium$1')
    .replace(/\s*-\s*低(\s|$)/g,' - Low$1')
    .replace(/【/g,'[').replace(/】/g,']')
    .replace(/：/g,': ').replace(/，/g,', ');
}
async function translateMdToEn(md){
  md = fixMixedChinese(md);
  // Split into lines, translate non-empty lines in batches
  const lines = md.split('\n');
  const translated = await Promise.all(lines.map(line => {
    const t = line.trim();
    if(!t || /^[-*#>|`]/.test(t) && t.length < 3) return Promise.resolve(line);
    // Skip pure English / already English lines
    if(/^[^\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]*$/.test(t)) return Promise.resolve(line);
    return translateChunkToEn(line);
  }));
  return translated.join('\n');
}
async function loadOutlook(){
  const content = document.getElementById('outlook-content');
  const weekEl = document.getElementById('outlook-week');
  const geo = document.getElementById('geo-content');
  // Render geo briefing in current language
  if(geo){
    if(currentLang === 'en'){
      geo.innerHTML = mdToHtml(GEO_BRIEFING_EN);
    } else {
      await refreshOutlookLang(currentLang);
    }
  }
  // Render static MiroFish report
  const md = STATIC_OUTLOOK_REPORT.trim();
  if(!md){
    weekEl.textContent = 'No report pasted';
    content.innerHTML = '<div class="outlook-empty"><div class="big">🐟</div>' +
      '<strong>Paste your MiroFish report</strong><br><br>' +
      'Open <code>news-server.py</code>, find <code>STATIC_OUTLOOK_REPORT</code> and paste your report there.</div>';
    _outlookLoaded = true;
    return;
  }
  weekEl.textContent = 'Week of Apr 7 - Apr 11';
  _outlookMdEn = md;
  if(currentLang !== 'en'){
    await refreshOutlookLang(currentLang);
  } else {
    content.innerHTML = '<div class="outlook-body">' + mdToHtml(md) + '</div>';
  }
  _outlookLoaded = true;
}

loadAll();
</script>
</body>
</html>"""

def build_html():
    return HTML_TEMPLATE.replace('__REGIONS_JSON__', regions_json())

# ── HTTP Handler ──────────────────────────────────────────────
class Handler(http.server.BaseHTTPRequestHandler):

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path in ('/', '/index.html'):
            content = build_html().encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        elif parsed.path == '/api/yields':
            try:
                curves = get_yields_cached()
                body = json.dumps({'curves': curves}).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except Exception as e:
                self._err(500, str(e))

        elif parsed.path == '/api/feed':
            params = urllib.parse.parse_qs(parsed.query)
            url = params.get('url', [''])[0]
            if not url:
                self._err(400, 'Missing url'); return
            # Serve from cache if fresh
            cached = get_cached(url)
            if cached:
                self._xml(cached); return
            # Otherwise fetch live
            try:
                raw = fetch_url(url)
                if not is_rss(raw):
                    raw = scrape_headlines(raw, url.split('/')[2], url)
                set_cached(url, raw)
                self._xml(raw)
            except urllib.error.HTTPError as e:
                print(f'  ❌  {url.split("/")[2]}  HTTP {e.code}')
                self._err(502, f'HTTP {e.code}: {e.reason}')
            except urllib.error.URLError as e:
                print(f'  ❌  {url.split("/")[2]}  {e.reason}')
                self._err(502, str(e.reason))
            except Exception as e:
                print(f'  ❌  {url}  {e}')
                self._err(500, str(e))
        else:
            self.send_error(404)

    def _xml(self, raw):
        self.send_response(200)
        self.send_header('Content-Type', 'application/xml; charset=utf-8')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Cache-Control', 'max-age=300')
        self.end_headers()
        self.wfile.write(raw)

    def _err(self, code, msg):
        body = json.dumps({'error': msg}).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass  # quiet

# ── Entry point ───────────────────────────────────────────────
IS_LOCAL = not os.environ.get('RENDER')  # True when running on your Mac

def open_browser():
    time.sleep(1.5)
    webbrowser.open(f'http://localhost:{PORT}')

if __name__ == '__main__':
    print(f'\n🌐  Global Market News Dashboard')
    if IS_LOCAL:
        print(f'    http://localhost:{PORT}\n')
    else:
        print(f'    Running on Render — port {PORT}\n')

    prefetch_all()

    try:
        host = 'localhost' if IS_LOCAL else '0.0.0.0'
        server = http.server.HTTPServer((host, PORT), Handler)
    except OSError:
        print(f'\n❌  Port {PORT} already in use.')
        if IS_LOCAL:
            print(f'    Run:  kill $(lsof -ti:{PORT})  then try again.\n')
        sys.exit(1)

    if IS_LOCAL:
        print(f'  Opening browser…  (Ctrl+C to stop)\n')
        threading.Thread(target=open_browser, daemon=True).start()
    else:
        print(f'  Server ready. (Ctrl+C to stop)\n')

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\n👋  Stopped.')
        sys.exit(0)
