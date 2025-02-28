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

function generateRSS(
  title: string,
  link: string,
  description: string,
  items: { title: string; link: string }[],
): string {
  const rssItems = items
    .map(
      (item) => `
    <item>
      <title><![CDATA[${item.title}]]></title>
      <link>${item.link}</link>
      <guid>${item.title}</guid>
    </item>`,
    )
    .join("");

  return `<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title><![CDATA[${title}]]></title>
    <link>${link}</link>
    <description><![CDATA[${description}]]></description>${rssItems}
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
  ctx.response.body = generateRSS(
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
