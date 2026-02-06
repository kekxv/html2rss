import html
import hashlib
import time
import string
import json
import re
import base64
import os
import asyncio
from typing import List, Optional, Tuple
import urllib.parse

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import Response, FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
import httpx
from bs4 import BeautifulSoup
import charset_normalizer
import trafilatura

app = FastAPI()

# Verification code
VERIFICATION_CODE = os.getenv("VERIFICATION_CODE", "test")
BASE62_ALPHABET = string.digits + string.ascii_letters

def encode_base62(num: int) -> str:
    if num == 0: return BASE62_ALPHABET[0]
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
    hash_obj = hashlib.md5(message.encode())
    num = int.from_bytes(hash_obj.digest()[:8], 'big')
    return encode_base62(num)

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
            enclosure_url = html.escape(item_link)
            rss_items.append(f"""
    <item>
      <title><![CDATA[{item_title}]]></title>
      <link><![CDATA[{item_link}]]></link>
      <guid isPermaLink="false">{item_guid}</guid>
      <pubDate>{date}</pubDate>
      <enclosure url="{enclosure_url}" type="application/x-bittorrent"/>
      <torrent xmlns="https://mikanani.me/0.1/">
        <link><![CDATA[{item_link}]]></link>
      </torrent>
      <description><![CDATA[{item_title or description}]]></description>
    </item>""")
        else:
            rss_items.append(f"""
    <item>
      <title><![CDATA[{item_title}]]></title>
      <link><![CDATA[{item_link}]]></link>
      <guid isPermaLink="false">{item_guid}</guid>
      <pubDate>{date}</pubDate>
      <description><![CDATA[{item_title or description}]]></description>
    </item>""")

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:torrent="https://mikanani.me/0.1/">
  <channel>
    <title><![CDATA[{title}]]></title>
    <link>{link}</link>
    <description><![CDATA[{description}]]></description>
    <lastBuildDate>{time.strftime("%a, %d %b %Y %H:%M:%S GMT", time.gmtime())}</lastBuildDate>{"".join(rss_items)}
  </channel>
</rss>"""

def url_encode_proxy(url: str) -> str:
    return base64.urlsafe_b64encode(url.encode()).decode().rstrip('=')

def url_decode_proxy(encoded: str) -> str:
    padding = '=' * (4 - len(encoded) % 4)
    return base64.urlsafe_b64decode(encoded + padding).decode()

def extract_magnet_dn(url: str) -> Optional[str]:
    if not url.startswith("magnet:"): return None
    # 移除链接中可能存在的空格和换行
    url = re.sub(r'\s+', '', url)
    match = re.search(r"dn=([^&]+)", url)
    if match: 
        # 解码并去除首尾空格
        return urllib.parse.unquote(match.group(1)).strip()
    return None

def clean_content_title(soup: BeautifulSoup, raw_title: str) -> str:
    # 优先寻找正文标题特征
    for tag in ['h1', 'h2', 'strong', 'b']:
        for el in soup.find_all(tag):
            text = el.get_text(strip=True)
            if (re.search(r'第.*?[章节节回]', text) or len(text) > 5) and len(text) < 80:
                return text
    title = raw_title
    title = re.split(r'[_|\-–—]', title)[0]
    title = re.sub(r'(最新章节|全文阅读|小说|在线阅读|无弹窗|目录|正文|第.*?页|[(（]\d+/\d+[)）]).*', '', title)
    return title.strip()

async def fetch_html_raw(url: str, charset: Optional[str] = None) -> Tuple[str, bytes]:
    parsed_url = urllib.parse.urlparse(url)
    base_url = f"{parsed_url.scheme}://{parsed_url.netloc}/"
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
        'Connection': 'keep-alive',
        'Referer': base_url,
    }

    last_error = None
    for attempt in range(3):
        try:
            async with httpx.AsyncClient(
                follow_redirects=True, 
                timeout=30.0, 
                headers=headers,
                http2=True
            ) as client:
                response = await client.get(url)
                if response.status_code != 200:
                    last_error = f"Status {response.status_code}"
                    await asyncio.sleep(1)
                    continue
                
                content = response.content
                
                # 1. 优先使用用户手动指定的编码
                if charset and charset.lower() != "auto":
                    try:
                        return content.decode(charset, errors="replace"), content
                    except: pass

                # 2. 自动识别：不依赖 header，直接尝试
                html_text = None
                
                # 尝试 A: 使用 charset_normalizer
                try:
                    detection = charset_normalizer.from_bytes(content).best()
                    if detection and detection.confidence > 0.8:
                        html_text = content.decode(detection.encoding, errors="replace")
                except: pass

                # 尝试 B: 如果 A 没把握，暴力尝试 GB18030 (兼容 GBK/GB2312)
                if not html_text:
                    try:
                        # GB18030 是最全的中文编码集
                        html_text = content.decode("gb18030")
                    except:
                        # 尝试 C: 最后的退路 UTF-8
                        html_text = content.decode("utf-8", errors="replace")
                    
                return html_text, content
        except Exception as e:
            last_error = str(e)
            await asyncio.sleep(1)
            
    raise HTTPException(status_code=500, detail=f"Failed to fetch {url} after 3 attempts: {last_error}")

def process_pure_content(text: str) -> str:
    """通用正文清洗与分段"""
    if not text: return ""
    text = re.sub(r'[\t ]{2,}', '\n', text)
    text = re.sub(r'\|', '\n', text)
    lines = text.split('\n')
    clean_lines = []
    
    junk_patterns = [
        r'上一[章页]', r'下一[章页]', r'目[录次]', r'书[架签]', r'加入书', r'推荐本', r'收藏本',
        r'选择背景', r'选择字体', r'font[a-z0-9]+', r'繁體', r'阅读[器网]', r'投推荐', r'返回',
        r'快捷键', r'Ctrl', r'所有文字', r'由网友', r'本站立场', r'Copyright', r'All rights',
        r'read[0-9]*\(\);', r'javascript', r'www\.', r'http', r'\.com', r'\.net', r'温馨提示',
        r'章节错误', r'点此举报', r'重要声明', r'不得转载', r'手机版', r'客户端'
    ]
    
    for line in lines:
        line = line.strip()
        if not line or len(line) < 2: continue
        hit_count = sum(1 for p in junk_patterns if re.search(p, line, re.I))
        if hit_count >= 2: continue
        if hit_count >= 1 and len(line) < 45: continue
        if re.search(r'©|20\d{2}', line): continue
        if re.match(r'^[_\-\s\*]+$', line): continue
        
        # 去除全角空格、&nbsp; 等各种空白字符，并进行规范化
        line = re.sub(r'[\s\u3000\xa0]+', ' ', line).strip()
        if line:
            clean_lines.append(f"<p>{line}</p>")
            
    return "".join(clean_lines)

@app.get("/read")
async def read_clean(url: str, code: str):
    if code != VERIFICATION_CODE:
        return HTMLResponse("Invalid code", status_code=403)
    
    try:
        actual_url = url_decode_proxy(url) if not url.startswith("http") else url
        full_body_text = []
        current_url = actual_url
        pages_fetched = 0
        next_url, prev_url, toc_url = None, None, None
        main_title = ""

        while pages_fetched < 5:
            html, _ = await fetch_html_raw(current_url)
            soup = BeautifulSoup(html, 'lxml')
            if not main_title:
                main_title = clean_content_title(soup, soup.title.string if soup.title else "")

            # 提取导航和目录
            for a in soup.find_all('a'):
                text = a.get_text(strip=True)
                href = a.get('href')
                if not href: continue
                full_href = urllib.parse.urljoin(current_url, href)
                if any(k in text for k in ['下一章', '下章节', 'Next']): next_url = full_href
                if any(k in text for k in ['上一章', '上章节', 'Prev']): prev_url = full_href
                if any(k in text for k in ['目录', 'Index', '返回列表', '返回书页']): toc_url = full_href

            container = soup.find(id=['content', 'booktxt', 'chaptercontent', 'showtxt', 'nr', 'read-content', 'main-content'])
            if not container:
                container = soup.find(class_=['content', 'book-content', 'read-content', 'showtxt', 'post-content'])
            
            target = container if container else soup.body if soup.body else soup
            for junk in target(['script', 'style', 'iframe', 'header', 'footer', 'nav', 'aside', 'button', 'fieldset', 'h1', 'h2']):
                junk.decompose()
            
            full_body_text.append(target.get_text(separator='\n'))

            # 分页逻辑
            has_next_page = False
            for a in soup.find_all('a'):
                if any(k in a.get_text() for k in ['下一页', '下一頁']) and len(a.get_text()) < 8:
                    next_p = urllib.parse.urljoin(current_url, a.get('href'))
                    if next_p != current_url:
                        current_url = next_p
                        has_next_page = True
                        break
            if has_next_page: pages_fetched += 1
            else: break

        final_html = process_pure_content("\n".join(full_body_text))

        def get_read_link(target_url):
            if not target_url: return "#"
            return f"/read?url={url_encode_proxy(target_url)}&code={code}"

        html_template = f"""
        <!DOCTYPE html>
        <html lang="zh-CN">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
            <title>{main_title}</title>
            <script src="https://cdn.tailwindcss.com"></script>
            <link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css" rel="stylesheet">
            <style>
                @import url('https://fonts.googleapis.com/css2?family=Noto+Serif+SC:wght@400;700&display=swap');
                :root {{ --bg-color: #f2efea; --text-color: #262626; --font-size: 1.25rem; }}
                body {{ 
                    background-color: var(--bg-color); color: var(--text-color); 
                    font-family: "Noto Serif SC", serif; transition: background-color 0.3s, color 0.3s;
                    -webkit-font-smoothing: antialiased; line-height: 1.85;
                }}
                .reader-container {{ max-width: 780px; margin: 0 auto; padding: 2rem 1.25rem 10rem; }}
                h1 {{ font-size: 1.9rem; font-weight: 700; margin-bottom: 4rem; text-align: center; color: #000; line-height: 1.4; }}
                #content p {{ 
                    margin-bottom: 1.7rem; line-height: 2.1; text-indent: 2em; 
                    font-size: var(--font-size); text-align: justify; word-break: break-all;
                }}
                .dark-mode {{ --bg-color: #181818; --text-color: #b0b0b0; }}
                .dark-mode h1 {{ color: #efefef; }}
                .nav-btn {{ 
                    @apply px-5 py-3 rounded-xl transition-all flex items-center space-x-2 text-sm font-medium;
                    border: 1px solid rgba(0,0,0,0.08); color: #666;
                }}
                .nav-btn:hover {{ background: rgba(0,0,0,0.03); color: #000; border-color: rgba(0,0,0,0.15); }}
                
                .nav-btn-next {{ 
                    @apply bg-slate-800 !text-white !border-none px-8;
                }}
                .nav-btn-next:hover {{ @apply bg-black shadow-lg; }}
                
                .dark-mode .nav-btn {{ 
                    border-color: rgba(255,255,255,0.1); color: #999;
                }}
                .dark-mode .nav-btn:hover {{ background: rgba(255,255,255,0.05); color: #fff; }}
                .dark-mode .nav-btn-next {{ @apply bg-indigo-600 !text-white; }}
                
                .progress-bar {{ position: fixed; top: 0; left: 0; height: 3px; background: #6366f1; transition: width 0.2s; z-index: 100; }}
            </style>
        </head>
        <body class="selection:bg-indigo-200">
            <div class="progress-bar" id="progressBar"></div>
            <div class="reader-container">
                <h1>{main_title}</h1>
                <div id="content">{final_html}</div>
                <div class="mt-20 flex justify-between items-center border-t pt-10 border-gray-300/20">
                    <a href="{get_read_link(prev_url)}" class="nav-btn {'opacity-20 pointer-events-none' if not prev_url else ''}">
                        <i class="fas fa-chevron-left text-[10px]"></i> <span>上一项</span>
                    </a>
                    <a href="{get_read_link(next_url)}" class="nav-btn nav-btn-next {'opacity-20 pointer-events-none' if not next_url else ''}">
                        <span>下一项</span> <i class="fas fa-chevron-right text-[10px]"></i>
                    </a>
                </div>
            </div>
            <div class="fixed bottom-8 right-6 flex flex-col space-y-4 z-50">
                <a href="{toc_url if toc_url else '#'}" class="w-12 h-12 rounded-full bg-white shadow-lg border border-gray-100 flex items-center justify-center text-slate-600 active:scale-95 {'hidden' if not toc_url else ''}">
                    <i class="fas fa-list-ul"></i>
                </a>
                <button onclick="changeFontSize(1)" class="w-12 h-12 rounded-full bg-white shadow-lg border border-gray-100 flex items-center justify-center text-slate-600 active:scale-95"><i class="fas fa-plus"></i></button>
                <button onclick="changeFontSize(-1)" class="w-12 h-12 rounded-full bg-white shadow-lg border border-gray-100 flex items-center justify-center text-slate-600 active:scale-95"><i class="fas fa-minus"></i></button>
                <button onclick="toggleDarkMode()" class="w-12 h-12 rounded-full bg-slate-800 text-white shadow-lg flex items-center justify-center active:scale-95"><i class="fas fa-circle-half-stroke"></i></button>
            </div>
            <script>
                window.onscroll = function() {{
                    let winScroll = document.body.scrollTop || document.documentElement.scrollTop;
                    let height = document.documentElement.scrollHeight - document.documentElement.clientHeight;
                    let scrolled = (winScroll / height) * 100;
                    document.getElementById("progressBar").style.width = scrolled + "%";
                }};
                let fontSize = parseFloat(localStorage.getItem('readerFontSize') || 1.25);
                function updateStyle() {{ document.documentElement.style.setProperty('--font-size', fontSize + 'rem'); localStorage.setItem('readerFontSize', fontSize); }}
                function changeFontSize(delta) {{ fontSize = Math.max(0.85, Math.min(2.5, fontSize + delta * 0.1)); updateStyle(); }}
                function toggleDarkMode() {{ document.body.classList.toggle('dark-mode'); localStorage.setItem('darkMode', document.body.classList.contains('dark-mode')); }}
                if (localStorage.getItem('darkMode') === 'true') document.body.classList.add('dark-mode');
                updateStyle();
            </script>
        </body>
        </html>
        """
        return HTMLResponse(content=html_template)
    except Exception as e:
        return HTMLResponse(f"<div style='padding:2rem;'><h3>解析失败</h3><p>{str(e)}</p></div>", status_code=500)

@app.get("/detect")
async def detect_rules(url: str = Query(...), code: str = Query(...), charset: Optional[str] = None):
    if code != VERIFICATION_CODE: return {"error": "Invalid verification code"}
    try:
        html_content, _ = await fetch_html_raw(url, charset=charset)
        soup = BeautifulSoup(html_content, 'lxml')
        all_links = soup.find_all('a', href=True)
        
        # Fallback to html.parser
        if not all_links:
            soup = BeautifulSoup(html_content, 'html.parser')
            all_links = soup.find_all('a', href=True)

        if not all_links:
            print(f"DEBUG: No links found for {url}. Length: {len(html_content)}. Snippet: {html_content[:500]}")
            return {"error": "No links found on the page. Site might be blocking requests or requires JavaScript."}

        magnets = soup.select('a[href^="magnet:"]')
        if magnets:
            return {"a": 'a[href^="magnet:"]', "t": "a[href^='magnet:']", "attr": "href", "message": "Detected media links."}
        
        # 增加更多常见的资源类关键词
        content_patterns = [r'第.*?[章节节回]', r'Chapter', r'分卷', r'番外', r'正文', r'BD', r'HD', r'720p', r'1080p', r'迅雷下载', r'磁力下载']
        novel_links = []
        for l in all_links:
            text = l.get_text(strip=True)
            if any(re.search(p, text, re.I) for p in content_patterns):
                novel_links.append(l)
        
                if len(novel_links) > 1:
                    from collections import Counter
                    parents = Counter()
                    # 增加检查范围，以便在大型列表中更准确地识别容器
                    for l in novel_links[:100]: 
                        p = l.parent
                        if not p: continue
                        # 尝试向上寻找 2 层父级，寻找具有特定类名或 ID 的容器
                        # 或者直接使用父级
                        classes = p.get('class')
                        class_str = f".{'.'.join(classes)}" if classes else ""
                        id_str = f"#{p.get('id')}" if p.get('id') else ""
                        sel = p.name + id_str + class_str
                        parents[sel] += 1
                    
                    best_parent = parents.most_common(1)[0][0]
                    # 如果最常见的父级包含项太少，尝试使用不带特定类的通用选择器
                    if parents.most_common(1)[0][1] < 5 and len(novel_links) > 20:
                         best_parent = "a" # 降级为全局 a 标签匹配，由 novel 模式过滤
                    
                    return {
                        "a": f"{best_parent} a" if best_parent != "a" else "a", 
                        "t": f"{best_parent} a" if best_parent != "a" else "a", 
                        "attr": "href", 
                        "novel": True,
                        "message": f"Detected novel pattern with {len(novel_links)} items."
                    }
                return {"error": "Could not detect patterns. Found " + str(len(all_links)) + " total links, but none match content patterns."}
    except HTTPException as e:
        return {"error": f"HTTP {e.status_code}: {e.detail}"}
    except Exception as e: 
        return {"error": str(e)}

def format_episode_title(title: str, season: Optional[int]) -> str:
    if season is None: return title
    
    # 更激进地匹配集数：支持 "13", "第13集", "13.mp4", " - 13", "[13]"
    # 1. 匹配“第xx集/话/P”
    match = re.search(r'第\s*(\d+)\s*[集话期P]', title)
    
    # 2. 匹配被空格、中划线、方括号包裹的数字，或文件名结尾前的数字
    if not match:
        # 匹配如 " - 13", "[13]", " 13 ", "_13_", "06v2.mp4"
        # 增加了对 v[0-9] 后缀的识别
        match = re.search(r'(?:[\s\-\[\]\(_]|^)(\d+)(?:v\d+)?(?:[\s\-\[\]\)_]|$|\.[a-z0-9]{2,4})', title, re.I)
    
    # 3. 实在不行，匹配末尾的数字
    if not match:
        match = re.search(r'(\d+)(?:v\d+)?(?:\.[a-z0-9]{2,4})?$', title, re.I)
        
    if match:
        try:
            ep_num = int(match.group(1))
            s_str = f"S{int(season):02d}E{ep_num:02d}"
            
            # 我们要替换的是包含数字及其可能版本号的整个部分
            # 但由于 match.group(0) 包含了周围的边界字符，我们需要精确找到要替换的文本
            # 重新构造一个正则来精确捕获包含数字和可选 v2 的部分
            replace_match = re.search(r'(\d+)(?:v\d+)?', match.group(0), re.I)
            if replace_match:
                to_replace = replace_match.group(0)
                # 寻找这个片段在原标题中相对于当前 match 的位置
                start_in_title = title.find(to_replace, match.start())
                if start_in_title != -1:
                    new_title = title[:start_in_title] + s_str + title[start_in_title+len(to_replace):]
                else:
                    new_title = f"{title} {s_str}"
            else:
                new_title = f"{title} {s_str}"
            
            # 如果是“第13集”这种带关键字的，直接整体替换更干净
            full_match = match.group(0)
            if "第" in full_match or any(k in full_match for k in ["集", "话", "P"]):
                new_title = title.replace(full_match, f" {s_str} ")

            return re.sub(r'\s+', ' ', new_title).strip()
        except:
            pass
    return title

def natural_sort_key(text: str) -> list:
    # 去除所有空白字符后再进行分块，确保排序不受空格干扰
    clean_text = re.sub(r'[\s\u3000\xa0]+', '', text)
    return [int(c) if c.isdigit() else c.lower() for c in re.split(r'(\d+)', clean_text) if c]

@app.get("/html2rss")
async def html2rss(
    request: Request, 
    p: Optional[str]=None, 
    url: Optional[str]=None, 
    a: Optional[str]=None, 
    code: Optional[str]=None, 
    t: Optional[str]=None,
    attr: Optional[str]=None,
    ts: str="a",
    as_: str="a",
    charset: str="auto",
    clean: bool=False,
    season: Optional[int]=None,
    novel: bool=False
):
    if p:
        try:
            num = decode_base62(p)
            byte_len = (num.bit_length() + 7) // 8
            params = json.loads(num.to_bytes(byte_len, 'big').decode('utf-8'))
            url = params.get('url')
            a = params.get('a')
            code = params.get('code')
            t = params.get('t')
            attr = params.get('attr')
            ts = params.get('ts', 'a')
            as_ = params.get('as', 'a')
            charset = params.get('charset', 'auto')
            clean = params.get('clean', False)
            season = params.get('season', season)
            novel = params.get('novel', False)
        except: raise HTTPException(status_code=400, detail="Parameter decoding failed")
    
    if not all([url, a, code]): raise HTTPException(status_code=400, detail="Missing essential params")
    if code != VERIFICATION_CODE: raise HTTPException(status_code=403)
    
    try:
        html, _ = await fetch_html_raw(url, charset=charset)
        soup = BeautifulSoup(html, 'lxml')
        
        # 支持逗号分隔的多选择器
        all_a_selectors = [sel.strip() for sel in a.split(',')]
        links = []
        for sel in all_a_selectors:
            links.extend(soup.select(sel))
            
        if not links: raise HTTPException(status_code=400, detail="No links found")
        
        titles = []
        if t:
            all_t_selectors = [sel.strip() for sel in t.split(',')]
            for sel in all_t_selectors:
                titles.extend(soup.select(sel))
        
        if not titles or len(titles) != len(links): titles = links

        # Basic Sorting
        if as_ == 'd': links = links[::-1]
        if ts == 'd': titles = titles[::-1]

        item_list = []; seen = set(); base_url = str(request.base_url).rstrip('/')
        for link_tag, title_tag in zip(links, titles):
            # 容错：尝试多个可能的属性
            raw_href = None
            for attr_name in [attr or "href", "data-href", "data-url", "original-href"]:
                raw_href = link_tag.get(attr_name)
                if raw_href: break
            
            if not raw_href: continue
            
            try:
                # 彻底清洗 URL 中的首尾空格及中间的换行
                l_url = urllib.parse.urljoin(url, raw_href.strip())
                l_url = re.sub(r'[\r\n\t]+', '', l_url)
            except Exception:
                continue
            
            if l_url in seen: continue
            
            # 容错：标题提取逻辑增强
            title = title_tag.get_text(strip=True)
            if not title:
                title = title_tag.get("title") or title_tag.get("alt") or "无标题"
            
            # 小说模式过滤与去空格
            if novel:
                # 匹配“第xx章/节/回/集/话/卷”或“Chapter xx”或“数字. ”开头等模式
                if not re.search(r'第.*?[章节节回集话卷]|Chapter\s*\d+|^\d+[\s\.]+', title, re.I):
                    continue
                # 小说模式下先去除标题中的各种空白字符
                title = re.sub(r'[\s\u3000\xa0]+', '', title)
                # 在章节关键字（第...章/节/回/集/话/卷）后面加一个空格，增加可读性
                title = re.sub(r'(第.*?[章节节回集话卷])', r'\1 ', title)
                # 处理 Chapter 格式
                title = re.sub(r'(Chapter\d+)', r'\1 ', title, flags=re.I)
                title = title.strip()

            seen.add(l_url)
            
            if l_url.startswith("magnet:"): 
                dn = extract_magnet_dn(l_url)
                if dn: title = dn
            
            if season is not None:
                title = format_episode_title(title, int(season))
            
            if clean and not l_url.startswith("magnet:"):
                l_url = f"{base_url}/read?url={url_encode_proxy(l_url)}&code={code}"
            
            item_list.append({"title": title, "link": l_url})
        
        if novel:
            # 小说模式下，按照章节标题进行自然排序（降序，即最新章节在前）
            item_list.sort(key=lambda x: natural_sort_key(x['title']), reverse=True)
            
        rss_content = await generate_rss(soup.title.string if soup.title else url, url, "", item_list)
        return Response(content=rss_content.encode('utf-8'), media_type="application/xml; charset=utf-8")
    except Exception as e: raise HTTPException(status_code=500, detail=str(e))

@app.get("/")
async def read_index(): return FileResponse('webroot/index.html')
app.mount("/", StaticFiles(directory="webroot"), name="static")

if __name__ == "__main__":

    import uvicorn

    import argparse

    

    parser = argparse.ArgumentParser()

    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", 3000)))

    args = parser.parse_args()

    

    uvicorn.run(app, host="0.0.0.0", port=args.port)
