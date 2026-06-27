# 每日 AI 资讯 → 飞书推送 🚀

每天定时抓取 AI 新闻，Claude 整理成简报，自动推送到你的飞书群。完全跑在 GitHub Actions 免费云服务器上，不用自己开电脑。

## 快速开始

1. **在飞书群里添加自定义机器人**，拿到 Webhook URL
2. **新建 GitHub 仓库**，把这些文件推上去
3. **设置 Secrets**：`FEISHU_WEBHOOK_URL`（必填）、`ANTHROPIC_API_KEY`（可选，启用 AI 摘要）
4. **手动跑一次 Actions** 测试
5. 搞定！之后每天北京时间早上 8 点自动推送

详细教程见 [项目方案.md](项目方案.md)

## 自定义

- 改 RSS 源 → 编辑 `fetch_ai_news.py` 里的 `RSS_FEEDS`
- 改推送时间 → 编辑 `.github/workflows/daily-ai-news.yml` 里的 `cron`
- 改推送渠道 → 替换 `fetch_ai_news.py` 里的 `send_feishu_message` 函数
