"""
每日 AI 资讯 → 飞书推送

抓取 RSS 源 →（可选）Claude AI 摘要 → 通过飞书自定义机器人 Webhook 发送。
"""

import os
import hashlib
import hmac
import base64
import json
import time
from datetime import datetime, timezone, timedelta
from urllib.parse import quote_plus

import feedparser
import requests

# ========================= 配置 =========================

# RSS 源列表，按需增减
RSS_FEEDS = [
    "https://www.theverge.com/ai-artificial-intelligence/rss/index.xml",
    "https://techcrunch.com/tag/artificial-intelligence/feed/",
    "https://feeds.arstechnica.com/arstechnica/ai",
    "https://venturebeat.com/category/ai/feed/",
    # 中文源（如果有公开RSS可以加在这）
    # "https://www.jiqizhixin.com/rss",
]

# 每个源最多取几条（避免一次发太多）
MAX_PER_FEED = 5

# 飞书消息允许的最大文本长度（超出会被截断）
FEISHU_MAX_TEXT_LEN = 28000

# 北京时间时区
TZ_CN = timezone(timedelta(hours=8))

# ========================= 飞书推送 =========================


def build_feishu_sign(secret: str, timestamp: str) -> str:
    """
    飞书机器人签名校验。
    如果没配置 FEISHU_SECRET 则不需要调用此函数。
    参考：https://open.feishu.cn/document/ukTMukTMukTM/ucTM5YjL3ETO24yNxkjN#348211be
    """
    string_to_sign = f"{timestamp}\n{secret}"
    hmac_code = hmac.new(
        string_to_sign.encode("utf-8"), digestmod=hashlib.sha256
    ).digest()
    return base64.b64encode(hmac_code).decode("utf-8")


def send_feishu_message(webhook_url: str, content: str, secret: str | None = None) -> bool:
    """
    通过飞书自定义机器人发送富文本消息。

    Args:
        webhook_url: 飞书机器人 webhook 地址
        content:     Markdown 格式的消息内容
        secret:      签名密钥（可选，与机器人安全设置保持一致）

    Returns:
        是否发送成功
    """
    # 截断超长内容
    if len(content) > FEISHU_MAX_TEXT_LEN:
        content = content[: FEISHU_MAX_TEXT_LEN - 200] + "\n\n...\n> ⚠️ 内容过长，已截断"

    # 飞书富文本消息：content 使用 post 格式，也可以用 Markdown 交互式消息
    post_content = [[{"tag": "text", "text": line}] for line in content.split("\n")]

    payload: dict = {
        "msg_type": "post",
        "content": {
            "post": {
                "zh_cn": {
                    "title": f"🤖 AI 日报 · {datetime.now(TZ_CN).strftime('%Y-%m-%d')}",
                    "content": post_content,
                }
            }
        },
    }

    headers = {"Content-Type": "application/json"}

    # 如果配置了签名密钥，走签名模式
    if secret:
        timestamp = str(int(time.time()))
        sign = build_feishu_sign(secret, timestamp)
        payload["timestamp"] = timestamp
        payload["sign"] = sign

    try:
        resp = requests.post(webhook_url, json=payload, headers=headers, timeout=15)
        result = resp.json()
        if result.get("code") == 0 or result.get("StatusCode") == 0:
            print("✅ 飞书消息发送成功")
            return True
        else:
            print(f"❌ 飞书返回错误：{result}")
            return False
    except requests.RequestException as e:
        print(f"❌ 请求飞书失败：{e}")
        return False


# ========================= RSS 抓取 =========================


def fetch_all_news() -> list[dict]:
    """遍历 RSS_FEEDS，返回合并后的新闻条目列表（按时间倒序）"""
    all_items: list[dict] = []
    today = datetime.now(TZ_CN).date()

    for url in RSS_FEEDS:
        print(f"📡 抓取：{url}")
        try:
            feed = feedparser.parse(url)
        except Exception as e:
            print(f"  ⚠️ 解析失败：{e}")
            continue

        if feed.bozo:
            print(f"  ⚠️ RSS 格式可能有问题：{feed.bozo_exception}")

        entries = feed.entries[:MAX_PER_FEED]
        print(f"  获取到 {len(entries)} 条")

        for entry in entries:
            # 尝试解析发布时间
            published = None
            if hasattr(entry, "published_parsed") and entry.published_parsed:
                published = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
            elif hasattr(entry, "updated_parsed") and entry.updated_parsed:
                published = datetime(*entry.updated_parsed[:6], tzinfo=timezone.utc)

            all_items.append(
                {
                    "title": entry.get("title", "").strip(),
                    "link": entry.get("link", "").strip(),
                    "summary": entry.get("summary", "").strip(),
                    "source": feed.feed.get("title", url),
                    "published": published,
                }
            )

    # 按发布时间倒排（没有时间的排最后）
    all_items.sort(
        key=lambda x: x["published"] or datetime(2000, 1, 1, tzinfo=timezone.utc),
        reverse=True,
    )

    return all_items


# ========================= AI 摘要（Claude API） =========================


def summarize_with_claude(items: list[dict], api_key: str) -> str | None:
    """
    使用 Claude API 将抓取的标题列表整理成一份中文 AI 日报简报。
    返回 Markdown 文本，如果失败则返回 None。
    """
    if not items:
        return None

    # 构造给 Claude 的 prompt
    headlines = []
    for i, item in enumerate(items, 1):
        source = item["source"]
        title = item["title"]
        link = item["link"]
        headlines.append(f"{i}. [{title}]({link}) — {source}")

    prompt = f"""以下是今天抓取到的 AI 领域新闻标题和链接。请你：

1. 把内容相近的归为一个小节，每节写一段 1-2 句中文摘要（不要翻译，用自己的话总结）
2. 每条保留原文标题和链接
3. 最后加一段「今日看点」，用一两句话点出今天最重要的动态
4. 整体风格简洁专业，使用 Markdown 排版

新闻列表：

{chr(10).join(headlines)}

请直接输出 Markdown 日报内容，不需要额外说明。"""

    resp = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": "claude-sonnet-4-6",
            "max_tokens": 2048,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=60,
    )

    if resp.status_code != 200:
        print(f"⚠️ Claude API 返回 {resp.status_code}：{resp.text[:300]}")
        return None

    data = resp.json()
    content = data.get("content", [])
    for block in content:
        if block.get("type") == "text":
            return block["text"].strip()

    return None


# ========================= 无 AI 时的简单输出 =========================


def build_simple_report(items: list[dict]) -> str:
    """不经过 AI，直接把标题+链接拼成 Markdown"""
    lines = [
        f"## 🤖 AI 日报 · {datetime.now(TZ_CN).strftime('%Y-%m-%d')}",
        f"\n共抓取到 {len(items)} 条 AI 领域新闻：\n",
    ]
    for i, item in enumerate(items, 1):
        title = item["title"]
        link = item["link"]
        source = item["source"]
        lines.append(f"{i}. [{title}]({link}) — *{source}*")

    lines.append(
        "\n\n---\n> ⚠️ 未配置 ANTHROPIC_API_KEY，展示为原始列表。配置 Claude API 可获得 AI 摘要。"
    )
    return "\n".join(lines)


# ========================= 主流程 =========================


def main():
    # --- 读取配置 ---
    webhook_url = os.environ.get("FEISHU_WEBHOOK_URL", "").strip()
    if not webhook_url:
        print("❌ 未配置 FEISHU_WEBHOOK_URL，请在 GitHub Secrets 中添加")
        return

    feishu_secret = os.environ.get("FEISHU_SECRET", "").strip() or None
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "").strip() or None

    # --- 抓新闻 ---
    print("=" * 50)
    print(f"🕗 开始运行 · {datetime.now(TZ_CN).strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 50)

    items = fetch_all_news()
    print(f"\n📰 总共抓到 {len(items)} 条新闻\n")

    if not items:
        send_feishu_message(
            webhook_url,
            f"## ⚠️ 无新闻\n\n今天（{datetime.now(TZ_CN).strftime('%Y-%m-%d')}）没有抓到任何 AI 新闻。\n可能原因：RSS 源失效或网络问题，请检查。",
            feishu_secret,
        )
        return

    # --- 生成报告 ---
    report: str | None = None

    if anthropic_key:
        print("🤖 正在使用 Claude 生成 AI 摘要...")
        report = summarize_with_claude(items, anthropic_key)
        if report:
            # 加上日报标题行
            report = (
                f"## 🤖 AI 日报 · {datetime.now(TZ_CN).strftime('%Y-%m-%d')}\n\n{report}"
            )
            # 替换用户消息标题中的尖括号，避免飞书按 HTML 解析
            report = report.replace("<", "&lt;").replace(">", "&gt;")

    if not report:
        print("📋 使用简单模式（无 AI 摘要）")
        report = build_simple_report(items)

    # --- 发送飞书 ---
    print("\n📨 正在发送到飞书...")
    send_feishu_message(webhook_url, report, feishu_secret)

    print("\n🎉 完成！")


if __name__ == "__main__":
    main()
