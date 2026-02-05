import hashlib
import time
import string
import json
import re
from typing import List, Optional
import urllib.parse

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import Response, FileResponse
from fastapi.staticfiles import StaticFiles
import httpx
from bs4 import BeautifulSoup
import charset_normalizer

app = FastAPI()

# Verification code from environment or default
VERIFICATION_CODE = "test"

# Base62 character set
BASE62_ALPHABET = string.digits + string.ascii_letters

def encode_base62(num: int) -> str:
    if num == 0:
        return BASE62_ALPHABET[0]
    arr = []
    base = len(BASE62_ALPHABET)
    while num:
        num, rem = divmod(num, base)
        arr.append(BASE62_ALPHABET[rem])
    arr.reverse()
    return ''.join(arr)

def decode_base62(s: str) -> int:
    base = len(BASE62_ALPHABET)
    num = 0
    for char in s:
        num = num * base + BASE62_ALPHABET.index(char)
    return num

def get_short_id(message: str) -> str:
    """Generate a short, URL-safe Base62 identifier from a string."""
    hash_obj = hashlib.md5(message.encode())
    num = int.from_bytes(hash_obj.digest()[:8], 'big')
    return encode_base62(num)

def extract_magnet_dn(url: str) -> Optional[str]:
    """从 magnet 链接中提取 dn 参数作为标题"""
    if not url.startswith("magnet:"):
        return None
    match = re.search(r"dn=([^&]+)", url)
    if match:
        return urllib.parse.unquote(match.group(1))
    return None

async def generate_rss(
    title: str,
    link: str,
    description: str,
    items: List[dict]
) -> str:
    rss_items = []
    now = time.time()
    for i, item in enumerate(items):
        item_time = now - i * 60
        date = time.strftime("%a, %d %b %Y %H:%M:%S GMT", time.gmtime(item_time))
        
        item_title = item['title'].strip()
        item_link = item['link']
        item_guid = get_short_id(item_link)
        
        if item_link.startswith("magnet:"):
            enclosure_url = item_link.split("&")[0]
            rss_items.append(f"""
    <item>
      <title><![CDATA[{item_title}]]></title>
      <link><![CDATA[{item_link}]]></link>
      <guid isPermaLink="false">{item_guid}</guid>
      <pubDate>{date}</pubDate>
      <enclosure url="{enclosure_url}" type="application/x-bittorrent"/>
      <description><![CDATA[{item_title or description}]]></description>
    </item>""")
        else:
            rss_items.append(f"""
    <item>
      <title><![CDATA[{item_title}]]></title>
      <link>{item_link}</link>
      <guid isPermaLink="false">{item_guid}</guid>
      <pubDate>{date}</pubDate>
      <description><![CDATA[{item_title or description}]]></description>
    </item>""")

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title><![CDATA[{title}]]></title>
    <link>{link}</link>
    <description><![CDATA[{description}]]></description>
    <lastBuildDate>{time.strftime("%a, %d %b %Y %H:%M:%S GMT", time.gmtime())}</lastBuildDate>{"".join(rss_items)}
  </channel>
</rss>"""

async def fetch_html(url: str, charset: Optional[str] = None) -> str:
    async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as client:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        }
        response = await client.get(url, headers=headers)
        if response.status_code != 200:
            raise HTTPException(status_code=400, detail=f"Failed to fetch URL: {response.status_code}")
        
        if charset and charset.lower() not in ["utf-8", "auto"]:
            try:
                return response.content.decode(charset, errors='replace')
            except Exception:
                pass
        
        results = charset_normalizer.from_bytes(response.content)
        best_match = results.best()
        if best_match:
            return str(best_match)
        
        return response.text

@app.get("/html2rss")
async def html2rss(
    p: Optional[str] = Query(None),
    url: Optional[str] = Query(None),
    a: Optional[str] = Query(None),
    code: Optional[str] = Query(None),
    t: Optional[str] = Query(None),
    charset: str = Query("auto"), 
    ts: str = Query("a"), 
    as_: str = Query("a", alias="as"),
    attr: Optional[str] = Query(None)
):
    if p:
        try:
            num = decode_base62(p)
            byte_len = (num.bit_length() + 7) // 8
            payload_bytes = num.to_bytes(byte_len, 'big')
            params = json.loads(payload_bytes.decode('utf-8'))
            url = params.get('url')
            a = params.get('a')
            code = params.get('code')
            t = params.get('t')
            charset = params.get('charset', 'auto')
            ts = params.get('ts', 'a')
            as_ = params.get('as', 'a')
            attr = params.get('attr')
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Failed to decode parameters: {str(e)}")

    if not all([url, a, code]):
        raise HTTPException(status_code=400, detail="Missing required parameters (url, a, code)")

    if code != VERIFICATION_CODE:
        raise HTTPException(status_code=403, detail="Invalid verification code")

    try:
        html = await fetch_html(url, charset)
        soup = BeautifulSoup(html, 'lxml')
        
        links = soup.select(a)
        if not links:
            raise HTTPException(status_code=400, detail="No links found with the provided selector")
        
        titles = []
        if t:
            titles = soup.select(t)
        
        if not titles or len(titles) != len(links):
            titles = links

        page_title = soup.title.string if soup.title else url
        meta_desc = soup.find("meta", attrs={"name": "description"})
        page_description = meta_desc["content"] if meta_desc and meta_desc.get("content") else ""

        if as_ == 'd':
            links = links[::-1]
        if ts == 'd':
            titles = titles[::-1]

        item_list = []
        seen_links = set()

        for link_tag, title_tag in zip(links, titles):
            link_url = link_tag.get(attr or "href")
            if link_url:
                if not link_url.startswith(("magnet:", "http:", "https:", "ftp:")):
                    link_url = urllib.parse.urljoin(url, link_url)
                
                if link_url in seen_links:
                    continue
                seen_links.add(link_url)
                
                # 优化标题提取
                title_text = title_tag.get_text(strip=True) or link_tag.get_text(strip=True)
                
                # 如果标题看起来是一个原始磁力链接，尝试提取 dn 参数
                if title_text.startswith("magnet:"):
                    dn = extract_magnet_dn(title_text)
                    if dn:
                        title_text = dn
                
                # 如果还是没有标题或者是短链接，再次尝试从 link_url 提取
                if (not title_text or len(title_text) < 2) and link_url.startswith("magnet:"):
                    dn = extract_magnet_dn(link_url)
                    if dn:
                        title_text = dn

                item_list.append({
                    "title": title_text or "No Title",
                    "link": link_url
                })

        rss_content = await generate_rss(page_title, url, page_description, item_list)
        return Response(content=rss_content, media_type="application/xml")

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/detect")
async def detect_rules(url: str = Query(...), code: str = Query(...)):
    if code != VERIFICATION_CODE:
        return {"error": "Invalid verification code"}
    try:
        html = await fetch_html(url, "auto")
        soup = BeautifulSoup(html, 'lxml')
        
        # 1. Magnet 检测优化
        magnets = soup.select('a[href^="magnet:"]')
        if magnets:
            sample_magnet = magnets[0]
            sample_text = sample_magnet.get_text(strip=True)
            
            # 如果磁力链接的文字就是链接本身，尝试找同行的其他文字
            if sample_text.startswith("magnet:") or len(sample_text) < 3:
                # 尝试找同一个 tr 下的第一个 a 标签
                parent_tr = sample_magnet.find_parent('tr')
                if parent_tr and parent_tr.find('a'):
                    first_a = parent_tr.find('a')
                    if first_a and first_a != sample_magnet:
                        return {
                            "a": 'a[href^="magnet:"]',
                            "t": "tr a:nth-of-type(1)", # 猜想第一个 a 是标题
                            "attr": "href",
                            "message": "Found magnets with distinct titles in table!"
                        }
            
            return {
                "a": 'a[href^="magnet:"]',
                "t": "a[href^='magnet:']", # 兜底，解析逻辑会处理 dn
                "attr": "href",
                "message": "Found magnet links!"
            }

        # 2. 通用检测
        all_links = soup.find_all('a', href=True)
        content_links = [l for l in all_links if len(l.get_text(strip=True)) > 5 and l['href'].startswith(('http', '/'))]
        if content_links:
            from collections import Counter
            parents = Counter()
            for l in content_links[:10]:
                p = l.parent
                if p.get('class'):
                    parents[f"{p.name}.{'.'.join(p.get('class'))}"] += 1
                else:
                    parents[p.name] += 1
            best_parent = parents.most_common(1)[0][0]
            return {
                "a": f"{best_parent} a",
                "t": f"{best_parent} a",
                "attr": "href",
                "message": "Detected general list pattern."
            }
        return {"error": "Could not detect patterns."}
    except Exception as e:
        return {"error": str(e)}

@app.get("/")
async def read_index():
    return FileResponse('webroot/index.html')

app.mount("/", StaticFiles(directory="webroot"), name="static")

if __name__ == "__main__":
    import uvicorn
    import argparse
    import os
    
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", 3000)))
    parser.add_argument("--verification-code", type=str, default=os.getenv("VERIFICATION_CODE", "test"))
    args = parser.parse_args()
    
    VERIFICATION_CODE = args.verification_code
    uvicorn.run(app, host="0.0.0.0", port=args.port)