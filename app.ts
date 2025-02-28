import { Application, Router, send } from "https://deno.land/x/oak/mod.ts";
import { DOMParser } from "jsr:@b-fuze/deno-dom";

let port = 3000;
let VERIFICATION_CODE = "test";
let webroot = "webroot";

const args = Deno.args;
args.forEach((arg) => {
  if (arg.startsWith("--port=")) {
    port = parseInt(arg.split("=")[1]);
  }
  if (arg.startsWith("--verification-code=")) {
    VERIFICATION_CODE = arg.split("=")[1];
  }
  if (arg.startsWith("--webroot=")) {
    webroot = arg.split("=")[1];
  }
});

const app = new Application();
const router = new Router();

function getRfc822Date(date: Date, h: number, m: number, s: number) {
  const days = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
  const months = [
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
  ];

  const dayOfWeek = days[date.getUTCDay()];
  const dayOfMonth = String(date.getUTCDate()).padStart(2, "0");
  const month = months[date.getUTCMonth()];
  const year = date.getUTCFullYear();
  const hours = String(h || date.getUTCHours()).padStart(2, "0");
  const minutes = String(m || date.getUTCMinutes()).padStart(2, "0");
  const seconds = String(s || date.getUTCSeconds()).padStart(2, "0");
  const timezone = "GMT"; // 或者 "+0000"

  return `${dayOfWeek}, ${dayOfMonth} ${month} ${year} ${hours}:${minutes}:${seconds} ${timezone}`;
}
async function hash(message: string) {
  const data = new TextEncoder().encode(message);
  const hashBuffer = await crypto.subtle.digest("SHA-256", data);
  const hashArray = Array.from(new Uint8Array(hashBuffer));
  return hashArray.map((b) =>
    b.toString(16)
      .padStart(2, "0")
  ).join("");
}

async function generateRSS(
  title: string,
  link: string,
  description: string,
  items: { title: string; link: string }[],
): Promise<string> {
  const rssItems = [];
  let h = 1;
  let m = 1;
  for (const item of items) {
    if (m >= 60) {
      h++;
      m = 1;
    }
    const date = getRfc822Date(new Date(), h, m++, 0);
    if (item.link.startsWith("magnet:")) {
      rssItems.push(`
    <item>
      <title><![CDATA[${item.title.trim()}]]></title>
      <link><![CDATA[${item.link}]]></link>
      <guid>${await hash(item.link)}</guid>
      <pubDate>${date}</pubDate>
      <enclosure url="${
        item.link.split("&")[0]
      }" type="application/x-bittorrent"/>
      <description><![CDATA[${(item.title||description).trim()}]]></description>
    </item>`);
    } else {
      rssItems.push(`
    <item>
      <title><![CDATA[${item.title.trim()}]]></title>
      <link>${item.link}</link>
      <guid>${await hash(item.link)}</guid>
      <pubDate>${date}</pubDate>
      <description><![CDATA[${(item.title||description).trim()}]]></description>
    </item>`);
    }
  }
  return `<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title><![CDATA[${title}]]></title>
    <link>${link}</link>
    <description><![CDATA[${description}]]></description>${rssItems.join("")}
  </channel>
</rss>`;
}

async function takeHtml(
  url: string,
  charset: string,
  a_selector: string,
  a_sort: boolean,
  title_selector: string,
  title_sort: boolean,
) {
  let html = null;
  if (!charset || charset.toLowerCase() === "utf-8") {
    html = await fetch(url).then((res) => res.text());
  } else {
    const result = await (await fetch(url)).blob();
    const decoder = new TextDecoder(charset);
    html = decoder.decode(await result.arrayBuffer());
  }
  const doc = new DOMParser().parseFromString(html, "text/html");
  let links = null;
  try {
    links = doc.querySelectorAll(a_selector);
  } catch (e) {
    console.error(e);
  }
  if (!links) {
    throw new Error("No links found");
  }
  let titles = null;
  try {
    if (title_selector) {
      titles = doc.querySelectorAll(title_selector);
    }
  } catch (e) {
    console.error(e);
  }
  if (titles == null || titles.length != links.length) {
    titles = links;
  }
  const title_ = doc.querySelector("title");
  const description_ = doc.querySelector("meta[name=description]");

  const list = [];
  for (let i = 0; i < links.length; i++) {
    const link = links[a_sort ? links.length - 1 - i : i];
    const title__ = titles[title_sort ? titles.length - 1 - i : i];
    const magnetLink = link.getAttribute("href");
    if (magnetLink) {
      list.push({
        title: title__.textContent || link.textContent,
        link: magnetLink,
      });
    }
  }
  return {
    title: title_?.textContent || "",
    description: description_?.getAttribute("content") || "",
    list,
  };
}

router.get("/html2rss", async (ctx) => {
  const urlSearchParams = new URLSearchParams(ctx.request.url.search);
  const url = decodeURIComponent(urlSearchParams.get("url") || "");
  if (!url) {
    throw new Error("Please provide a URL");
  }
  const a = decodeURIComponent(urlSearchParams.get("a") || "");
  if (!a) {
    throw new Error("Please provide a link");
  }
  const title = decodeURIComponent(urlSearchParams.get("t") || "");
  const charset = decodeURIComponent(urlSearchParams.get("charset") || "utf-8");
  const title_sort = decodeURIComponent(urlSearchParams.get("ts") || "a");
  const a_sort = decodeURIComponent(urlSearchParams.get("as") || "a");
  if (urlSearchParams.get("code") !== VERIFICATION_CODE) {
    throw new Error("Invalid verification code");
  }
  const list = await takeHtml(
    url,
    charset,
    a,
    a_sort !== "a",
    title,
    title_sort !== "a",
  );

  ctx.response.headers.set("Content-Type", "application/xml; charset=utf-8");
  ctx.response.status = 200;
  ctx.response.body = await generateRSS(
    list.title,
    url,
    list.description,
    list.list,
  );
});
app.use(router.routes());
app.use(router.allowedMethods());
app.use(async (ctx, next) => {
  // 判断ctx.request.url.pathname最后一个字符串是不是/
  if (ctx.request.url.pathname.endsWith("/")) {
    ctx.request.url.pathname = `${ctx.request.url.pathname}index.html`;
  }
  await send(ctx, ctx.request.url.pathname, { root: webroot });
  await next();
});

// console.log(`生成${100 * 10000}数据 开始`);
// generateTestData(100 * 10000);
// console.log(`生成${100 * 10000}数据 完成`);

app.addEventListener("listen", () => {
  console.log(`Server listening on port http://localhost:${port}`);
  console.log(`  Web root directory: ${webroot}`);
  console.log(`  Verification code: ${VERIFICATION_CODE}`);
});

await app.listen({ port });
