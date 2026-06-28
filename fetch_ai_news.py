"""
每日 AI 资讯 → 飞书推送（卡片版）

抓取 RSS 源 → Claude 中文摘要 + 配图 → 飞书卡片消息。
"""

import os
import re
import hashlib
import hmac
import base64
import json
import time
from datetime import datetime, timezone, timedelta
from html import unescape
from html.parser import HTMLParser

import feedparser
import requests

# ========================= 配置 =========================

RSS_FEEDS = [
    "https://www.theverge.com/ai-artificial-intelligence/rss/index.xml",
    "https://techcrunch.com/tag/artificial-intelligence/feed/",
    "https://feeds.arstechnica.com/arstechnica/ai",
    "https://venturebeat.com/category/ai/feed/",
    "https://www.artificialintelligence-news.com/feed/",
    # 中文源
    "https://www.jiqizhixin.com/rss",
]

# 每个源最多取几条
MAX_PER_FEED = 4

# AI 摘要时最多喂给 Claude 多少条（控制 token 消耗）
MAX_ITEMS_FOR_AI = 16

# 每张飞书卡片最多展示几条新闻
ITEMS_PER_CARD = 8

# 单张飞书卡片最大字符数
FEISHU_CARD_MAX_CHARS = 15000

# 北京时间
TZ_CN = timezone(timedelta(hours=8))


# ========================= HTML 工具 =========================

def strip_html(text: str) -> str:
    """去除 HTML 标签，保留纯文本"""
    if not text:
        return ""
    text = re.sub(r"<br\s*/?>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = unescape(text)
    return text.strip()


def extract_first_image(entry) -> str | None:
    """
    从 RSS 条目中提取第一张图片 URL。
    依次尝试：media_content → enclosures → summary 中的 <img>
    """
    # 1. media_content（标准 RSS 2.0 配图）
    if "media_content" in entry:
        for mc in entry.media_content:
            url = mc.get("url", "")
            if url and any(url.lower().endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".webp", ".gif")):
                return url

    # 2. media_thumbnail
    if "media_thumbnail" in entry:
        for mt in entry.media_thumbnail:
            url = mt.get("url", "")
            if url:
                return url

    # 3. enclosures
    if "enclosures" in entry:
        for enc in entry.enclosures:
            mime = enc.get("type", "")
            if mime and mime.startswith("image/"):
                return enc.get("href", "")
            url = enc.get("href", "")
            if url and any(url.lower().endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".webp", ".gif")):
                return url

    # 4. 从 summary HTML 中查找 <img> 标签
    summary = entry.get("summary", "") or entry.get("description", "")
    if summary:
        match = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', summary, re.IGNORECASE)
        if match:
            return match.group(1)

    # 5. content 字段
    if "content" in entry:
        for c in entry.content:
            val = c.get("value", "")
            match = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', val, re.IGNORECASE)
            if match:
                return match.group(1)

    return None


# ========================= 飞书签名 =========================

def build_feishu_sign(secret: str, timestamp: str) -> str:
    string_to_sign = f"{timestamp}\n{secret}"
    hmac_code = hmac.new(
        string_to_sign.encode("utf-8"), digestmod=hashlib.sha256
    ).digest()
    return base64.b64encode(hmac_code).decode("utf-8")


# ========================= 飞书卡片消息 =========================

def send_feishu_card(
    webhook_url: str,
    card_payload: dict,
    secret: str | None = None,
) -> bool:
    """发送一张飞书交互式卡片"""
    payload = {
        "msg_type": "interactive",
        "card": card_payload,
    }
    if secret:
        ts = str(int(time.time()))
        payload["timestamp"] = ts
        payload["sign"] = build_feishu_sign(secret, ts)

    try:
        resp = requests.post(webhook_url, json=payload, headers={"Content-Type": "application/json"}, timeout=15)
        result = resp.json()
        if result.get("code") == 0 or result.get("StatusCode") == 0:
            return True
        else:
            print(f"❌ 飞书返回错误：{result}")
            return False
    except requests.RequestException as e:
        print(f"❌ 请求飞书失败：{e}")
        return False


def build_news_card(items: list[dict], card_index: int, total_cards: int) -> dict:
    """
    将多条新闻拼接为一张飞书卡片。
    每一条新闻用 lark_md 渲染：标题链接 + 中文摘要 + 配图。
    """
    date_str = datetime.now(TZ_CN).strftime("%Y-%m-%d")
    title_suffix = f" ({card_index+1}/{total_cards})" if total_cards > 1 else ""
    header_title = f"🤖 AI 日报 · {date_str}{title_suffix}"

    elements = []
    for i, item in enumerate(items):
        cn_title = item.get("cn_title", item["title"])
        link = item["link"]
        cn_summary = item.get("cn_summary", "")
        image_url = item.get("image")
        source = item["source"]
        published = item.get("published")
        time_str = ""
        if published:
            time_str = published.astimezone(TZ_CN).strftime("%H:%M")

        # 构建每条新闻的 lark_md
        md = ""
        # 中文标题（粗体）
        md += f"**{cn_title}**  \n"
        # 来源 + 时间
        if time_str:
            md += f"📰 {source} · {time_str}  \n"
        else:
            md += f"📰 {source}  \n"
        # 原文链接
        if link:
            md += f"🔗 [阅读原文]({link})  \n"
        # 中文摘要
        if cn_summary:
            md += f"\n{cn_summary}  \n"
        # 配图
        if image_url:
            md += f"\n![配图]({image_url})  \n"

        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": md.strip()}})
        elements.append({"tag": "hr"})

    # 去掉最后一条分割线
    if elements and elements[-1].get("tag") == "hr":
        elements.pop()

    # 底部备注
    elements.append({
        "tag": "note",
        "elements": [{"tag": "plain_text", "content": "⚡ 每日 AI 资讯自动推送 | Powered by GitHub Actions + Claude"}],
    })

    card = {
        "header": {
            "title": {"tag": "plain_text", "content": header_title},
            "template": "blue",
        },
        "elements": elements,
    }

    # 粗略截断：飞书卡片有 30KB 限制，按字符数估算
    card_json = json.dumps(card, ensure_ascii=False)
    while len(card_json) > 25000 and len(elements) > 2:
        # 删掉倒数第二条新闻（一条 div + 一条 hr）
        elements.pop()  # note
        elements.pop()  # hr
        elements.pop()  # div
        elements.append({
            "tag": "note",
            "elements": [{"tag": "plain_text", "content": "⚠️ 部分内容因长度限制被截断"}],
        })
        card["elements"] = elements
        card_json = json.dumps(card, ensure_ascii=False)

    return card


# ========================= RSS 抓取 =========================

def fetch_all_news() -> list[dict]:
    """遍历 RSS_FEEDS，返回合并后的新闻条目（按时间倒序）"""
    all_items: list[dict] = []

    for url in RSS_FEEDS:
        print(f"📡 抓取：{url}")
        try:
            feed = feedparser.parse(url)
        except Exception as e:
            print(f"  ⚠️ 解析失败：{e}")
            continue

        if feed.bozo:
            print(f"  ⚠️ RSS 异常：{feed.bozo_exception}")

        entries = feed.entries[:MAX_PER_FEED]
        print(f"  获取到 {len(entries)} 条")

        for entry in entries:
            published = None
            if hasattr(entry, "published_parsed") and entry.published_parsed:
                published = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
            elif hasattr(entry, "updated_parsed") and entry.updated_parsed:
                published = datetime(*entry.updated_parsed[:6], tzinfo=timezone.utc)

            # 提取摘要（去 HTML 标签）
            raw_summary = entry.get("summary", "") or entry.get("description", "")
            clean_summary = strip_html(raw_summary)[:500]

            # 提取配图
            image = extract_first_image(entry)

            all_items.append({
                "title": (entry.get("title", "") or "").strip(),
                "link": (entry.get("link", "") or "").strip(),
                "summary": clean_summary,
                "source": (feed.feed.get("title", "") or url).strip(),
                "published": published,
                "image": image,
            })

    # 按发布时间倒排
    all_items.sort(
        key=lambda x: x["published"] or datetime(2000, 1, 1, tzinfo=timezone.utc),
        reverse=True,
    )
    return all_items


# ========================= AI 中文摘要 =========================

def summarize_with_claude(items: list[dict], api_key: str) -> list[dict]:
    """
    使用 Claude 对新闻列表做中文改写 + 摘要。
    返回带有 cn_summary 字段的 items 列表。
    """
    if not items:
        return items

    # 取前 N 条给 AI 处理
    items_to_process = items[:MAX_ITEMS_FOR_AI]

    # 构造 prompt
    headlines = []
    for i, item in enumerate(items_to_process, 1):
        lines = [f"### {i}"]
        lines.append(f"标题：{item['title']}")
        lines.append(f"来源：{item['source']}")
        if item["summary"]:
            lines.append(f"原文摘要：{item['summary'][:300]}")
        headlines.append("\n".join(lines))

    prompt = f"""以下是今天抓取到的 AI 领域新闻。请你：

1. **为每条新闻写一个中文标题**（简洁明了，保留原意的核心信息）
2. **为每条新闻写一段中文摘要**（2-3 句），用自己的话总结核心信息，不是字面翻译
3. 最后加一段「📌 今日看点」简要综述（2-3 句话概括今天最重要的趋势）
4. 整体简洁专业，用中文表达

输出格式（严格按此模板）：

**【1】中文标题**

**原标题：English Original Title Here**
中文摘要内容……

**【2】中文标题**

**原标题：English Original Title Here**
中文摘要内容……

📌 今日看点
综述内容……

新闻列表：

{chr(10).join(headlines)}"""

    resp = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": "claude-sonnet-4-6",
            "max_tokens": 2560,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=90,
    )

    if resp.status_code != 200:
        print(f"⚠️ Claude API 返回 {resp.status_code}：{resp.text[:300]}")
        return items  # 返回不带 cn_summary 的原始列表

    data = resp.json()
    content = data.get("content", [])
    ai_text = ""
    for block in content:
        if block.get("type") == "text":
            ai_text = block["text"].strip()
            break

    if not ai_text:
        return items

    # 解析 AI 输出，匹配每条新闻的中文标题和摘要
    # 匹配模式：**【数字】中文标题**\n**原标题：English**\n中文摘要
    cn_map = {}
    # 按 "**【数字】" 分割
    blocks = re.split(r"\n(?=\*\*【\d+】)", ai_text)

    for block_text in blocks:
        # 提取：**【1】中文标题** \n **原标题：xxx** \n 中文内容
        m = re.match(
            r"\*\*【(\d+)】(.+?)\*\*\s*\n\*\*原标题：(.+?)\*\*\s*\n(.*)",
            block_text,
            re.DOTALL,
        )
        if m:
            idx = int(m.group(1)) - 1  # 0-based
            cn_title = m.group(2).strip()
            cn_body = m.group(4).strip()
            # 去掉 "📌 今日看点" 及之后
            cn_body = cn_body.split("📌")[0].strip()
            if cn_title:
                cn_map[idx] = {"cn_title": cn_title, "cn_summary": cn_body}

    # 回填 cn_title + cn_summary
    for i, item in enumerate(items):
        if i in cn_map:
            item["cn_title"] = cn_map[i]["cn_title"]
            item["cn_summary"] = cn_map[i]["cn_summary"]
        else:
            # 无 AI 摘要时用原文
            item["cn_title"] = item.get("title", "")[:100]
            item["cn_summary"] = item.get("summary", "")[:200]

    return items


# ========================= 无 AI 时的降级 =========================

def build_simple_cards(items: list[dict]) -> list[dict]:
    """无 AI 摘要时，生成简单卡片列表"""
    date_str = datetime.now(TZ_CN).strftime("%Y-%m-%d")
    cards = []
    chunks = [items[i:i + ITEMS_PER_CARD] for i in range(0, len(items), ITEMS_PER_CARD)]

    for card_idx, chunk in enumerate(chunks):
        date_str = datetime.now(TZ_CN).strftime("%Y-%m-%d")
        title_suffix = f" ({card_idx+1}/{len(chunks)})" if len(chunks) > 1 else ""

        elements = []
        for i, item in enumerate(chunk, 1):
            title = item["title"]
            link = item["link"]
            source = item["source"]
            image_url = item.get("image")
            summary = item.get("summary", "")[:200]

            md = f"**{title}**  \n📰 {source}  \n"
            if link:
                md += f"🔗 [阅读原文]({link})  \n"
            if summary:
                md += f"\n{summary}  \n"
            if image_url:
                md += f"\n![配图]({image_url})  \n"

            elements.append({"tag": "div", "text": {"tag": "lark_md", "content": md.strip()}})
            elements.append({"tag": "hr"})

        if elements and elements[-1].get("tag") == "hr":
            elements.pop()

        elements.append({
            "tag": "note",
            "elements": [{"tag": "plain_text", "content": "⚠️ 未配置 AI 摘要，显示为原文列表 | 配置 ANTHROPIC_API_KEY 获得中文简报"}],
        })

        cards.append({
            "header": {
                "title": {"tag": "plain_text", "content": f"🤖 AI 日报 · {date_str}{title_suffix}"},
                "template": "blue",
            },
            "elements": elements,
        })

    return cards


# ========================= 主流程 =========================

def main():
    webhook_url = os.environ.get("FEISHU_WEBHOOK_URL", "").strip()
    if not webhook_url:
        print("❌ 未配置 FEISHU_WEBHOOK_URL")
        return

    feishu_secret = os.environ.get("FEISHU_SECRET", "").strip() or None
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "").strip() or None

    print("=" * 50)
    print(f"🕗 开始运行 · {datetime.now(TZ_CN).strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 50)

    # 1. 抓新闻
    items = fetch_all_news()
    print(f"\n📰 总共抓到 {len(items)} 条新闻\n")

    if not items:
        no_news_card = {
            "header": {
                "title": {"tag": "plain_text", "content": "🤖 AI 日报 · 无新闻"},
                "template": "red",
            },
            "elements": [{
                "tag": "div",
                "text": {"tag": "lark_md", "content": "今天没有抓到任何 AI 新闻。\n可能原因：RSS 源失效或网络问题。"},
            }],
        }
        send_feishu_card(webhook_url, no_news_card, feishu_secret)
        return

    # 2. AI 摘要
    if anthropic_key:
        print("🤖 正在使用 Claude 生成中文摘要...")
        items = summarize_with_claude(items, anthropic_key)

    # 3. 生成卡片
    if anthropic_key:
        # 有 AI 摘要 → 直接构建卡片
        chunks = [items[i:i + ITEMS_PER_CARD] for i in range(0, len(items), ITEMS_PER_CARD)]
        cards = [build_news_card(chunk, idx, len(chunks)) for idx, chunk in enumerate(chunks)]
    else:
        cards = build_simple_cards(items)

    # 4. 发送飞书
    print(f"\n📨 正在发送 {len(cards)} 张卡片到飞书...")
    for i, card in enumerate(cards):
        ok = send_feishu_card(webhook_url, card, feishu_secret)
        if ok:
            print(f"  ✅ 卡片 {i+1}/{len(cards)} 发送成功")
        else:
            print(f"  ❌ 卡片 {i+1}/{len(cards)} 发送失败")
        # 多条消息之间稍等，避免触发飞书频率限制
        if i < len(cards) - 1:
            time.sleep(1)

    print("\n🎉 完成！")


if __name__ == "__main__":
    main()
