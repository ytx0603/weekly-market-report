"""
======================================================================
每周趋势雷达 — 资本 * 代码 * 巨头的交叉信号
======================================================================
自动周报生成器。每周日 GitHub Actions 自动触发:
  -> 拉取美股板块、GitHub热榜、财经新闻、科技公司动态
  -> 喂给 DeepSeek LLM 交叉分析
  -> 生成 PDF + HTML 邮件，发到指定邮箱

核心哲学:
  股票是资本的投票机 -- 资本比媒体快半步
  GitHub热度是开发者的投票机 -- 代码不会说谎
  当多个独立信号源指向同一方向，这个方向值得高度关注。

这不是投资建议。这是趋势雷达。
======================================================================
"""

import os
import sys
import re
import logging
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

# ============================================================
# 环境配置 (密钥一律从环境变量读取，绝无硬编码)
# ============================================================

# 本地测试: 加载 .env 文件 (此文件不会上传 GitHub)
try:
    from dotenv import load_dotenv
    env_path = Path(__file__).parent / ".env"
    if env_path.exists():
        load_dotenv(env_path)
except ImportError:
    pass  # GitHub Actions 不需要 python-dotenv

DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"
DEEPSEEK_MODEL = "deepseek-chat"

QQ_SMTP_SERVER = "smtp.qq.com"
QQ_SMTP_PORT = 465
QQ_EMAIL = os.environ.get("QQ_EMAIL", "")
QQ_SMTP_CODE = os.environ.get("QQ_SMTP_CODE", "")
RECIPIENT_EMAIL = os.environ.get("RECIPIENT_EMAIL", QQ_EMAIL)

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", os.environ.get("GH_TOKEN", ""))

PROJECT_DIR = Path(__file__).parent
OUTPUT_DIR = PROJECT_DIR / "output"
OUTPUT_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(OUTPUT_DIR / "weekly_report.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)

NOW = datetime.now()
ONE_WEEK_AGO = NOW - timedelta(days=7)
WEEK_STR = f"{NOW.year}年第{NOW.isocalendar()[1]}周"


# ============================================================
# 第1步: 拉取美股板块数据 (yfinance)
# ============================================================

SECTOR_ETFS = {
    "科技": "XLK",
    "通信服务": "XLC",
    "金融": "XLF",
    "能源": "XLE",
    "医疗健康": "XLV",
    "可选消费": "XLY",
    "必选消费": "XLP",
    "工业": "XLI",
    "材料": "XLB",
    "房地产": "XLRE",
    "公用事业": "XLU",
    "半导体": "SMH",
    "生物科技": "XBI",
    "AI与机器人": "BOTZ",
}


def fetch_sector_data():
    """拉取美股板块ETF本周涨跌幅"""
    logger.info("[1/4] 拉取美股板块数据...")
    import yfinance as yf

    results = []
    for name, ticker in SECTOR_ETFS.items():
        try:
            etf = yf.Ticker(ticker)
            hist = etf.history(start=ONE_WEEK_AGO, end=NOW)
            if len(hist) < 2:
                results.append({
                    "板块": name, "代码": ticker, "周涨跌幅": "数据不足"
                })
                continue
            open_price = float(hist["Open"].iloc[0])
            close_price = float(hist["Close"].iloc[-1])
            change_pct = ((close_price - open_price) / open_price) * 100
            results.append({
                "板块": name,
                "代码": ticker,
                "周涨跌幅": f"{change_pct:+.2f}%",
                "周一开盘": f"${open_price:.2f}",
                "最新收盘": f"${close_price:.2f}",
            })
            logger.info(f"  {name} ({ticker}): {change_pct:+.2f}%")
        except Exception as e:
            logger.warning(f"  {name} ({ticker}) 失败: {e}")
            results.append({
                "板块": name, "代码": ticker, "周涨跌幅": "获取失败"
            })

    # 按涨跌幅排序
    def _sort_key(item):
        val = str(item.get("周涨跌幅", ""))
        try:
            return float(val.replace("%", "").replace("+", ""))
        except ValueError:
            return -999
    results.sort(key=_sort_key, reverse=True)

    ok_count = sum(
        1 for r in results
        if r["周涨跌幅"] not in ("获取失败", "数据不足")
    )
    logger.info(f"  板块数据: {len(results)} 个, {ok_count} 个成功")
    return results


# ============================================================
# 第2步: 拉取 GitHub 数据
# ============================================================

def fetch_github_trending():
    """拉取 GitHub 本周最热项目和飙升项目"""
    logger.info("[2/4] 拉取 GitHub 数据...")
    import requests

    headers = {"Accept": "application/vnd.github.v3+json"}
    if GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"

    date_str = ONE_WEEK_AGO.strftime("%Y-%m-%d")

    # --- 最热项目 ---
    top_repos = []
    try:
        url = (
            "https://api.github.com/search/repositories"
            f"?q=created:>={date_str}&sort=stars&order=desc&per_page=10"
        )
        resp = requests.get(url, headers=headers, timeout=30)
        if resp.status_code == 200:
            for repo in resp.json().get("items", []):
                desc = repo.get("description") or "无描述"
                topics = repo.get("topics", [])
                top_repos.append({
                    "项目名": repo["full_name"],
                    "简介": desc[:150],
                    "语言": repo.get("language") or "未知",
                    "本周Star": repo["stargazers_count"],
                    "话题": ", ".join(topics[:5]) or "无",
                    "URL": repo["html_url"],
                })
                logger.info(
                    f"  {repo['full_name']}: {repo['stargazers_count']} stars"
                )
        else:
            logger.warning(f"  GitHub API 返回 {resp.status_code}")
    except Exception as e:
        logger.error(f"  GitHub 热榜失败: {e}")

    # --- 飙升项目 ---
    rising_repos = []
    seen_names = {r["项目名"] for r in top_repos}
    try:
        url = (
            "https://api.github.com/search/repositories"
            f"?q=created:>={date_str}+stars:>30"
            "&sort=updated&order=desc&per_page=15"
        )
        resp = requests.get(url, headers=headers, timeout=30)
        if resp.status_code == 200:
            for repo in resp.json().get("items", []):
                name = repo["full_name"]
                if name not in seen_names and len(rising_repos) < 10:
                    desc = repo.get("description") or "无描述"
                    topics = repo.get("topics", [])
                    rising_repos.append({
                        "项目名": name,
                        "简介": desc[:150],
                        "语言": repo.get("language") or "未知",
                        "Star数": repo["stargazers_count"],
                        "话题": ", ".join(topics[:5]) or "无",
                        "URL": repo["html_url"],
                    })
                    seen_names.add(name)
            logger.info(f"  飙升项目: {len(rising_repos)} 个")
    except Exception as e:
        logger.error(f"  GitHub 飙升失败: {e}")

    return top_repos, rising_repos


# ============================================================
# 第3步: 拉取财经新闻 (RSS)
# ============================================================

FINANCE_RSS_FEEDS = [
    ("Reuters", "https://feeds.reuters.com/reuters/businessNews"),
    ("CNBC", (
        "https://search.cnbc.com/rs/search/combinedcms/view.xml"
        "?partnerId=wrss01&id=10001147"
    )),
    ("MarketWatch", "https://feeds.marketwatch.com/marketwatch/topstories"),
    ("Yahoo Finance", "https://finance.yahoo.com/news/rssindex"),
]


def fetch_finance_news():
    """拉取财经新闻 RSS"""
    logger.info("[3/4] 拉取财经新闻...")
    import feedparser

    all_news = []
    for source_name, url in FINANCE_RSS_FEEDS:
        try:
            feed = feedparser.parse(url)
            if not feed.entries:
                logger.warning(f"  {source_name}: 无内容")
                continue
            count = 0
            for entry in feed.entries[:8]:
                title = entry.get("title", "").strip()
                if not title:
                    continue
                link = entry.get("link", "")
                summary = entry.get(
                    "summary", entry.get("description", "")
                )
                summary = re.sub(r"<[^>]+>", "", summary)[:200]
                all_news.append({
                    "来源": source_name,
                    "标题": title,
                    "摘要": summary,
                    "链接": link,
                })
                count += 1
            logger.info(f"  {source_name}: {count} 条")
        except Exception as e:
            logger.warning(f"  {source_name} 失败: {e}")

    # 去重
    seen = set()
    unique = []
    for item in all_news:
        key = item["标题"][:50]
        if key not in seen:
            seen.add(key)
            unique.append(item)

    logger.info(f"  财经新闻: {len(unique)} 条 (去重后)")
    return unique[:30]


# ============================================================
# 第4步: 拉取科技公司动态 (Google News RSS)
# ============================================================

TECH_COMPANIES = [
    "SpaceX", "Tesla", "OpenAI", "Anthropic",
    "Apple", "NVIDIA", "Google", "Microsoft", "Meta", "Amazon",
]


def fetch_tech_news():
    """拉取科技公司近一周动态"""
    logger.info("[4/4] 拉取科技公司动态...")
    import feedparser

    all_news = []
    for company in TECH_COMPANIES:
        try:
            url = (
                "https://news.google.com/rss/search"
                f"?q={company}+technology+OR+AI+OR+product+OR+acquisition"
                "&hl=en-US&gl=US&ceid=US:en"
            )
            feed = feedparser.parse(url)
            if not feed.entries:
                logger.warning(f"  {company}: 无结果")
                continue

            count = 0
            for entry in feed.entries[:5]:
                title = entry.get("title", "").strip()
                if not title:
                    continue
                # Google News title 格式: "标题 - 来源"
                if " - " in title:
                    title = title.rsplit(" - ", 1)[0]
                link = entry.get("link", "")
                summary = entry.get(
                    "summary", entry.get("description", "")
                )
                summary = re.sub(r"<[^>]+>", "", summary)[:150]
                all_news.append({
                    "公司": company,
                    "标题": title,
                    "摘要": summary,
                    "链接": link,
                })
                count += 1
            logger.info(f"  {company}: {count} 条")
        except Exception as e:
            logger.warning(f"  {company} 失败: {e}")

    logger.info(f"  科技动态: {len(all_news)} 条")
    return all_news[:40]


# ============================================================
# 数据持久化 (SQLite) — 每周期原始数据全部入库
# ============================================================

DB_PATH = OUTPUT_DIR / "weekly_data.db"


def init_db():
    """初始化 SQLite 数据库，创建所有表"""
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()

    cursor.executescript("""
        CREATE TABLE IF NOT EXISTS sector_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_date TEXT NOT NULL,
            week_str TEXT NOT NULL,
            sector_name TEXT NOT NULL,
            ticker TEXT NOT NULL,
            change_pct TEXT,
            open_price TEXT,
            close_price TEXT
        );

        CREATE TABLE IF NOT EXISTS github_trending (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_date TEXT NOT NULL,
            week_str TEXT NOT NULL,
            category TEXT NOT NULL,
            repo_name TEXT NOT NULL,
            description TEXT,
            language TEXT,
            stars INTEGER,
            topics TEXT,
            url TEXT
        );

        CREATE TABLE IF NOT EXISTS finance_news (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_date TEXT NOT NULL,
            week_str TEXT NOT NULL,
            source TEXT,
            title TEXT,
            summary TEXT,
            link TEXT
        );

        CREATE TABLE IF NOT EXISTS tech_news (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_date TEXT NOT NULL,
            week_str TEXT NOT NULL,
            company TEXT,
            title TEXT,
            summary TEXT,
            link TEXT
        );

        CREATE TABLE IF NOT EXISTS reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_date TEXT NOT NULL,
            week_str TEXT NOT NULL,
            report_md TEXT,
            total_tokens INTEGER,
            prompt_tokens INTEGER,
            completion_tokens INTEGER
        );

        CREATE TABLE IF NOT EXISTS daily_prices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            sector_name TEXT NOT NULL,
            date TEXT NOT NULL,
            close_price REAL NOT NULL,
            UNIQUE(ticker, date)
        );
    """)
    conn.commit()
    conn.close()
    logger.info(f"  数据库已就绪: {DB_PATH}")


def save_sector_data(data):
    """将板块数据写入 SQLite"""
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    for item in data:
        cursor.execute(
            "INSERT INTO sector_data "
            "(run_date, week_str, sector_name, ticker, "
            "change_pct, open_price, close_price) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (NOW.isoformat(), WEEK_STR,
             item["板块"], item["代码"],
             item.get("周涨跌幅", ""),
             item.get("周一开盘", ""),
             item.get("最新收盘", ""))
        )
    conn.commit()
    conn.close()
    logger.info(f"  DB: {len(data)} 条板块数据已存储")


def save_github_data(top_repos, rising_repos):
    """将 GitHub 数据写入 SQLite"""
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    for repo in top_repos:
        cursor.execute(
            "INSERT INTO github_trending "
            "(run_date, week_str, category, repo_name, "
            "description, language, stars, topics, url) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (NOW.isoformat(), WEEK_STR, "top",
             repo["项目名"], repo.get("简介", ""),
             repo.get("语言", ""), repo.get("本周Star", 0),
             repo.get("话题", ""), repo.get("URL", ""))
        )
    for repo in rising_repos:
        cursor.execute(
            "INSERT INTO github_trending "
            "(run_date, week_str, category, repo_name, "
            "description, language, stars, topics, url) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (NOW.isoformat(), WEEK_STR, "rising",
             repo["项目名"], repo.get("简介", ""),
             repo.get("语言", ""), repo.get("Star数", 0),
             repo.get("话题", ""), repo.get("URL", ""))
        )
    conn.commit()
    conn.close()
    logger.info(
        f"  DB: {len(top_repos)} + {len(rising_repos)} 条 GitHub 数据已存储"
    )


def save_finance_news(data):
    """将财经新闻写入 SQLite"""
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    for item in data:
        cursor.execute(
            "INSERT INTO finance_news "
            "(run_date, week_str, source, title, summary, link) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (NOW.isoformat(), WEEK_STR,
             item["来源"], item["标题"],
             item.get("摘要", ""), item.get("链接", ""))
        )
    conn.commit()
    conn.close()
    logger.info(f"  DB: {len(data)} 条财经新闻已存储")


def save_tech_news(data):
    """将科技公司动态写入 SQLite"""
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    for item in data:
        cursor.execute(
            "INSERT INTO tech_news "
            "(run_date, week_str, company, title, summary, link) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (NOW.isoformat(), WEEK_STR,
             item["公司"], item["标题"],
             item.get("摘要", ""), item.get("链接", ""))
        )
    conn.commit()
    conn.close()
    logger.info(f"  DB: {len(data)} 条科技动态已存储")


def save_report(report_md, usage):
    """将 LLM 报告写入 SQLite"""
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    total = usage.total_tokens if usage else 0
    prompt_t = usage.prompt_tokens if usage else 0
    completion_t = usage.completion_tokens if usage else 0
    cursor.execute(
        "INSERT INTO reports "
        "(run_date, week_str, report_md, "
        "total_tokens, prompt_tokens, completion_tokens) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (NOW.isoformat(), WEEK_STR, report_md,
         total, prompt_t, completion_t)
    )
    conn.commit()
    conn.close()
    logger.info(f"  DB: LLM 报告已存储 ({len(report_md)} 字符)")


# ============================================================
# 数据分析模块（程序自行计算，非 LLM 生成 — 不可替代性在这里）
# ============================================================

def fetch_daily_prices():
    """拉取近 4 周每日收盘价，用于相关性分析"""
    logger.info("[分析] 拉取近 4 周每日价格...")
    import yfinance as yf

    four_weeks_ago = NOW - timedelta(days=28)
    prices = {}

    for name, ticker in SECTOR_ETFS.items():
        try:
            etf = yf.Ticker(ticker)
            hist = etf.history(start=four_weeks_ago, end=NOW)
            if len(hist) < 5:
                logger.warning(f"    {ticker}: 数据不足 ({len(hist)} 天)")
                continue
            prices[ticker] = {
                "name": name,
                "close": hist["Close"],
            }
            logger.info(f"    {name} ({ticker}): {len(hist)} 天")
        except Exception as e:
            logger.warning(f"    {ticker} 拉取失败: {e}")

    # 存入 SQLite
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    saved = 0
    for ticker, info in prices.items():
        for dt, price in info["close"].items():
            try:
                cursor.execute(
                    "INSERT OR IGNORE INTO daily_prices "
                    "(ticker, sector_name, date, close_price) "
                    "VALUES (?, ?, ?, ?)",
                    (ticker, info["name"],
                     dt.strftime("%Y-%m-%d"), float(price))
                )
                saved += 1
            except Exception:
                pass
    conn.commit()
    conn.close()
    logger.info(f"  每日价格: {len(prices)} 个 ETF, 存储 {saved} 条")
    return prices


def compute_sector_correlation(prices):
    """计算板块间 Pearson 相关系数（基于近 4 周每日收益率）"""
    logger.info("[分析] 计算板块相关性矩阵...")

    if len(prices) < 3:
        logger.warning("  板块数据不足，跳过相关性分析")
        return None

    try:
        import pandas as pd
    except ImportError:
        logger.warning("  pandas 未安装，跳过相关性分析")
        return None

    # 构建 DataFrame: 行=日期, 列=板块 ticker
    dfs = {}
    for ticker, info in prices.items():
        dfs[ticker] = info["close"]

    df = pd.DataFrame(dfs)
    returns = df.pct_change().dropna()

    if len(returns) < 5:
        logger.warning(f"  有效交易日不足 ({len(returns)} 天)")
        return None

    # 相关性矩阵
    corr = returns.corr()

    # ticker → 中文名称映射
    ticker_to_name = {v: k for k, v in SECTOR_ETFS.items()}

    # 提取所有配对
    pairs = []
    tickers = list(corr.columns)
    for i, t1 in enumerate(tickers):
        for t2 in tickers[i + 1:]:
            r = corr.loc[t1, t2]
            pairs.append((t1, t2, r))

    pairs.sort(key=lambda x: x[2], reverse=True)

    result = {
        "top_positive": [],
        "top_negative": [],
        "clusters": [],
        "n_days": len(returns),
        "n_sectors": len(tickers),
    }

    # 最强正相关 Top 5
    for t1, t2, r in pairs[:5]:
        result["top_positive"].append({
            "pair": f"{ticker_to_name.get(t1, t1)} ↔ "
                    f"{ticker_to_name.get(t2, t2)}",
            "r": round(r, 3),
        })

    # 最强负相关 Top 5
    for t1, t2, r in pairs[-5:]:
        result["top_negative"].append({
            "pair": f"{ticker_to_name.get(t1, t1)} ↔ "
                    f"{ticker_to_name.get(t2, t2)}",
            "r": round(r, 3),
        })

    # 连通分量聚类：r > 0.7 视为关联边，DFS 找最大连通子图
    visited = set()
    for t1 in tickers:
        if t1 in visited:
            continue
        cluster = {t1}
        stack = [t1]
        visited.add(t1)
        while stack:
            current = stack.pop()
            for t2 in tickers:
                if t2 not in visited and corr.loc[current, t2] > 0.7:
                    cluster.add(t2)
                    visited.add(t2)
                    stack.append(t2)
        if len(cluster) >= 2:
            result["clusters"].append(
                [ticker_to_name.get(t, t) for t in sorted(cluster)]
            )

    logger.info(
        f"  正相关 top3: "
        + ", ".join(
            p["pair"] + f"({p['r']})"
            for p in result["top_positive"][:3]
        )
    )
    return result


def compute_github_topic_clusters(top_repos, rising_repos):
    """统计 GitHub 热门话题标签频率"""
    logger.info("[分析] 统计 GitHub 话题频率...")

    from collections import Counter

    topic_counter = Counter()
    all_repos = top_repos + rising_repos

    for repo in all_repos:
        topics_str = repo.get("话题", "")
        if topics_str and topics_str != "无":
            for topic in topics_str.split(", "):
                topic = topic.strip()
                if topic:
                    topic_counter[topic] += 1

    if not topic_counter:
        logger.warning("  无有效话题标签")
        return None

    result = {
        "top_topics": topic_counter.most_common(10),
        "total_repos": len(all_repos),
        "total_unique_topics": len(topic_counter),
    }

    logger.info(
        f"  热门话题 Top 5: "
        + ", ".join(f"{t}({c})" for t, c in result["top_topics"][:5])
    )
    return result


def compute_news_sentiment(finance_news):
    """对财经新闻标题做 VADER 情感分析"""
    logger.info("[分析] 计算新闻情感...")

    try:
        from vaderSentiment.vaderSentiment import \
            SentimentIntensityAnalyzer
        analyzer = SentimentIntensityAnalyzer()
    except ImportError:
        logger.warning("  vaderSentiment 未安装，跳过情感分析")
        return None

    scores = []
    for item in finance_news:
        title = item.get("标题", "")
        if not title:
            continue
        vs = analyzer.polarity_scores(title)
        scores.append({
            "title": title[:80],
            "source": item.get("来源", ""),
            "compound": vs["compound"],
            "pos": vs["pos"],
            "neg": vs["neg"],
            "neu": vs["neu"],
        })

    if not scores:
        return None

    scores.sort(key=lambda x: x["compound"], reverse=True)

    avg_compound = sum(s["compound"] for s in scores) / len(scores)
    pos_count = sum(1 for s in scores if s["compound"] > 0.05)
    neg_count = sum(1 for s in scores if s["compound"] < -0.05)
    neu_count = len(scores) - pos_count - neg_count

    result = {
        "avg_compound": round(avg_compound, 3),
        "positive_count": pos_count,
        "negative_count": neg_count,
        "neutral_count": neu_count,
        "top_positive": scores[:3],
        "top_negative": scores[-3:],
        "total": len(scores),
    }

    logger.info(
        f"  情感均值: {avg_compound:.3f} "
        f"(正面: {pos_count}, 负面: {neg_count}, 中性: {neu_count})"
    )
    return result


def build_analysis_section(corr_result, topic_result, sentiment_result):
    """将分析结果格式化为 Markdown，注入 LLM prompt"""
    parts = []
    parts.append("## 数据透视（程序自动计算，非 LLM 生成）\n")

    # --- 板块相关性 ---
    if corr_result:
        parts.append("### 板块相关性矩阵")
        parts.append(
            f"（基于近 {corr_result['n_days']} 个交易日"
            f"、{corr_result['n_sectors']} 个板块的每日收益率 Pearson r）\n"
        )
        parts.append("**最强正相关：**")
        for item in corr_result["top_positive"][:3]:
            parts.append(f"- {item['pair']}: r={item['r']}")
        parts.append("\n**最强负相关：**")
        for item in corr_result["top_negative"][:3]:
            parts.append(f"- {item['pair']}: r={item['r']}")
        if corr_result["clusters"]:
            parts.append("\n**板块聚类（r>0.7）：**")
            for i, cluster in enumerate(corr_result["clusters"], 1):
                parts.append(f"- 集群{i}: {' / '.join(cluster)}")
        parts.append("")

    # --- GitHub 话题 ---
    if topic_result and topic_result["top_topics"]:
        parts.append("### GitHub 热门话题")
        topic_str = "、".join(
            f"{t}({c}次)" for t, c in topic_result["top_topics"][:8]
        )
        parts.append(
            f"本周 {topic_result['total_repos']} 个热门项目中，"
            f"最频繁的话题标签: {topic_str}\n"
        )

    # --- 新闻情感 ---
    if sentiment_result:
        parts.append("### 财经新闻情感")
        parts.append(
            f"共分析 {sentiment_result['total']} 条新闻标题，"
            f"平均情感得分: {sentiment_result['avg_compound']} "
            f"（-1=极度负面, +1=极度正面）。"
            f"正面 {sentiment_result['positive_count']} 条，"
            f"负面 {sentiment_result['negative_count']} 条，"
            f"中性 {sentiment_result['neutral_count']} 条。\n"
        )
        if sentiment_result["top_positive"]:
            parts.append("**最正面标题：**")
            for item in sentiment_result["top_positive"]:
                parts.append(
                    f"- [{item['source']}] {item['title']} "
                    f"(compound={item['compound']:.2f})"
                )
        if sentiment_result["top_negative"]:
            parts.append("\n**最负面标题：**")
            for item in sentiment_result["top_negative"]:
                parts.append(
                    f"- [{item['source']}] {item['title']} "
                    f"(compound={item['compound']:.2f})"
                )
        parts.append("")

    return "\n".join(parts)


# ============================================================
# 规则引擎 — 多信号交叉异常检测，直接标红报警
# ============================================================

# 板块 → GitHub 话题映射（用于资本-代码交叉比对）
SECTOR_TOPIC_MAP = {
    "半导体": {"semiconductor", "chip", "gpu", "tpu", "npu", "risc-v",
               "eda", "fpga", "asic", "silicon", "cuda", "opencl",
               "verilog", "hdl"},
    "AI与机器人": {"ai", "artificial-intelligence", "machine-learning",
                  "deep-learning", "llm", "nlp", "robot", "robotics",
                  "agent", "rag", "transformer", "diffusion", "rlhf",
                  "fine-tuning", "langchain", "autogpt", "mcp"},
    "科技": {"cloud", "saas", "devops", "kubernetes", "docker",
            "serverless", "api", "microservices", "backend"},
    "金融": {"fintech", "blockchain", "defi", "crypto", "trading",
            "quantitative-finance", "web3", "solidity"},
    "能源": {"energy", "solar", "battery", "renewable", "nuclear",
            "hydrogen", "photovoltaic"},
    "医疗健康": {"health", "medical", "healthcare", "ehr", "telemedicine"},
    "生物科技": {"biotech", "bioinformatics", "protein", "crispr",
                "rna", "sequencing", "genomics", "drug-discovery"},
    "通信服务": {"5g", "6g", "telecom", "satellite", "networking",
                "iot", "edge-computing", "sdn"},
    "可选消费": {"ecommerce", "gaming", "metaverse", "vr", "ar",
                "streaming", "social-media"},
    "工业": {"manufacturing", "industry-4.0", "iot", "automation",
            "digital-twin", "robotics", "plc"},
    "材料": {"materials", "battery-materials", "graphene", "nanotech"},
    "房地产": {"proptech", "smart-home", "construction"},
    "公用事业": {"energy", "grid", "smart-grid", "water"},
}


def check_capital_code_divergence(sector_data, topic_result):
    """规则1：资本-代码背离。板块涨但GitHub无声 / GitHub热但板块跌"""
    if not topic_result or not topic_result.get("top_topics"):
        return None

    github_topics = {t.lower() for t, _ in topic_result["top_topics"]}
    all_github_words = set()
    for topic in github_topics:
        all_github_words.update(
            topic.replace("-", " ").replace("_", " ").split()
        )
    all_github_words |= github_topics

    ranked = sorted(
        [s for s in sector_data
         if s.get("周涨跌幅") not in ("获取失败", "数据不足")],
        key=lambda x: float(x["周涨跌幅"].replace("%", "").replace("+", "")),
        reverse=True,
    )
    if len(ranked) < 5:
        return None

    alerts = []

    # 涨幅 Top 3，对应 GitHub 话题完全缺席
    for sector in ranked[:3]:
        name = sector["板块"]
        topics = SECTOR_TOPIC_MAP.get(name, set())
        if not topics:
            continue
        overlap = topics & all_github_words
        if not overlap:
            alerts.append({
                "type": "资本-代码背离",
                "direction": "资本先行",
                "sector": name,
                "change": sector["周涨跌幅"],
                "evidence": (
                    f"{name}本周涨{sector['周涨跌幅']}"
                    f"（板块第{ranked.index(sector) + 1}），"
                    f"但其关联技术话题（"
                    f"{', '.join(sorted(topics)[:8])}等）"
                    f"在 GitHub Top {len(github_topics)} 热门话题中完全缺席"
                ),
                "interpretation": (
                    f"机构可能在提前布局{name}相关标的，"
                    f"而开发者社区尚未跟上。"
                    f"若未来 2-4 周 GitHub 上相关话题开始出现，则信号确认。"
                ),
            })

    # GitHub 最热话题对应垫底板块
    hot_topics = {t.lower() for t, _ in topic_result["top_topics"][:5]}
    for sector in ranked[-3:]:
        name = sector["板块"]
        topics = SECTOR_TOPIC_MAP.get(name, set())
        if not topics:
            continue
        overlap = topics & hot_topics
        if overlap:
            alerts.append({
                "type": "资本-代码背离",
                "direction": "代码先行",
                "sector": name,
                "change": sector["周涨跌幅"],
                "evidence": (
                    f"GitHub 热门话题包含 {', '.join(sorted(overlap))}"
                    f"（映射到{name}板块），但{name}本周仅"
                    f"{sector['周涨跌幅']}（板块倒数）。"
                    f"开发者在建的东西，资本还没跟进。"
                ),
                "interpretation": (
                    f"可能是一个早期信号：开发者社区在押注"
                    f"{name}相关技术方向，但市场尚未定价。"
                    f"需关注该板块后续资金流入。"
                ),
            })

    if alerts:
        logger.info(f"  [规则引擎] 资本-代码背离: {len(alerts)} 条")
        return alerts
    return None


def check_sentiment_price_divergence(sector_data, sentiment_result):
    """规则2：情绪-价格背离。新闻乐观但市场跌 / 新闻悲观但市场涨"""
    if not sentiment_result:
        return None

    avg_sentiment = sentiment_result["avg_compound"]

    up_count = 0
    down_count = 0
    for s in sector_data:
        val = str(s.get("周涨跌幅", ""))
        try:
            pct = float(val.replace("%", "").replace("+", ""))
            if pct > 0:
                up_count += 1
            elif pct < 0:
                down_count += 1
        except ValueError:
            pass

    total = up_count + down_count
    if total < 5:
        return None

    up_ratio = up_count / total
    alerts = []

    if avg_sentiment > 0.3 and up_ratio < 0.35:
        alerts.append({
            "type": "情绪-价格背离",
            "direction": "媒体乐观、市场悲观",
            "evidence": (
                f"本周财经新闻平均情感得分 {avg_sentiment:.2f}（偏正面），"
                f"但 {total} 个板块中仅 {up_count} 个上涨"
                f"（{up_ratio:.0%}）。"
                f"媒体叙事与资金实际走向出现明显分歧。"
            ),
            "interpretation": (
                "媒体乐观但资金在撤退——通常意味着聪明的钱已在离场，"
                "而媒体仍在讲好故事。需结合具体板块跌幅判断风险级别。"
            ),
        })

    if avg_sentiment < -0.2 and up_ratio > 0.65:
        alerts.append({
            "type": "情绪-价格背离",
            "direction": "媒体悲观、市场乐观",
            "evidence": (
                f"本周财经新闻平均情感得分 {avg_sentiment:.2f}（偏负面），"
                f"但 {total} 个板块中 {up_count} 个上涨"
                f"（{up_ratio:.0%}）。"
                f"媒体在恐慌，但资金在买入。"
            ),
            "interpretation": (
                "这往往是市场见底的信号之一——恐慌叙事笼罩时，"
                "机构可能在悄悄吸筹。关注涨幅最大的板块。"
            ),
        })

    if alerts:
        logger.info(f"  [规则引擎] 情绪-价格背离: {len(alerts)} 条")
        return alerts
    return None


def check_topic_concentration(topic_result):
    """规则3：话题集中度预警。单一话题占比过高可能意味着泡沫"""
    if not topic_result or not topic_result.get("top_topics"):
        return None

    topics = topic_result["top_topics"]
    total_mentions = sum(c for _, c in topics)
    if total_mentions == 0:
        return None

    top_topic, top_count = topics[0]
    concentration = top_count / total_mentions

    if concentration > 0.30:
        logger.info(
            f"  [规则引擎] 话题集中度: {top_topic} 占 {concentration:.0%}"
        )
        return [{
            "type": "话题集中度预警",
            "topic": top_topic,
            "concentration": f"{concentration:.0%}",
            "evidence": (
                f"GitHub 热门话题中「{top_topic}」出现 {top_count} 次，"
                f"占全部 {total_mentions} 次话题标注的 {concentration:.0%}。"
                f"前 5 话题: "
                + "、".join(f"{t}({c})" for t, c in topics[:5])
            ),
            "interpretation": (
                f"极高的话题集中度可能意味着泡沫——"
                f"当所有人都在谈论同一件事时，这件事已经被充分定价。"
                f"同时关注话题榜长尾部分是否有被忽视的信号。"
            ),
        }]
    return None


def run_rule_engine(sector_data, topic_result, sentiment_result):
    """规则引擎主入口。顺序执行所有规则，返回格式化的报警 Markdown"""
    logger.info("[规则引擎] 运行交叉信号异常检测...")

    all_alerts = []

    result = check_capital_code_divergence(sector_data, topic_result)
    if result:
        all_alerts.extend(result)

    result = check_sentiment_price_divergence(sector_data, sentiment_result)
    if result:
        all_alerts.extend(result)

    result = check_topic_concentration(topic_result)
    if result:
        all_alerts.extend(result)

    if not all_alerts:
        logger.info("  [规则引擎] 无异常信号")
        return ""

    logger.info(f"  [规则引擎] 共触发 {len(all_alerts)} 条报警")

    lines = []
    lines.append("## ⚠️ 规则引擎报警（程序自动检测，非 LLM 生成）\n")

    for i, alert in enumerate(all_alerts, 1):
        lines.append(f"### 🚨 报警 {i}: {alert['type']}")

        if "direction" in alert:
            lines.append(f"**方向**: {alert['direction']}")
        if "sector" in alert:
            lines.append(
                f"**涉及板块**: {alert['sector']} "
                f"({alert.get('change', '')})"
            )
        if "topic" in alert:
            lines.append(
                f"**相关话题**: {alert['topic']} "
                f"(集中度: {alert.get('concentration', '')})"
            )

        lines.append(f"\n**证据**: {alert['evidence']}")
        lines.append(f"\n**解读**: {alert['interpretation']}")
        lines.append("")

    lines.append(
        "> 以上报警由程序规则引擎自动触发，非 LLM 分析结果。"
        "请 LLM 在后续分析中重点回应这些信号，但不必逐条复述。\n"
    )

    return "\n".join(lines)


# ============================================================
# 第5步: 构建 Prompt 并调用 DeepSeek
# ============================================================

def build_prompt(sector_data, top_repos, rising_repos,
                 finance_news, tech_news, analysis_section="",
                 alerts_section=""):
    """构建给 DeepSeek 的分析 prompt"""
    prompt = (
        "你是一个顶尖的趋势分析师。你的任务不是推荐股票，而是从"
        "资本流向、开发者社区热度、科技巨头动作和财经新闻中，"
        "交叉比对找出未来可能爆发的技术/产业/商业热点。\n\n"
    )
    prompt += "## 你的核心方法论\n\n"
    prompt += (
        "> **资本是先行者** -- 板块资金流向往往比媒体报道快 2-4 周，"
        "因为机构能拿到我们拿不到的信息。\n"
        "> **代码不会说谎** -- GitHub 热度反映开发者社区的真实兴趣，"
        "不受 PR 话术影响。\n"
        "> **巨头用钱投票** -- 公司的收购、开源、关停、招人方向，"
        "反映产业巨头的真实判断。\n"
        "> **交叉验证** -- 当 2 个以上独立信号源指向同一方向时，"
        "这个方向值得高度关注。\n\n"
    )

    prompt += "---\n\n## 美股板块本周表现\n\n"
    for s in sector_data:
        prompt += (
            f"- {s['板块']} ({s['代码']}): {s['周涨跌幅']}\n"
        )

    prompt += "\n## GitHub 本周最热项目\n\n"
    for i, r in enumerate(top_repos, 1):
        prompt += (
            f"{i}. **{r['项目名']}** "
            f"(stars: {r['本周Star']}) -- {r['简介']}\n"
            f"   语言: {r['语言']} | 话题: {r['话题']}\n"
        )

    prompt += "\n## GitHub 飙升项目\n\n"
    for i, r in enumerate(rising_repos, 1):
        prompt += (
            f"{i}. **{r['项目名']}** "
            f"(stars: {r['Star数']}) -- {r['简介']}\n"
            f"   语言: {r['语言']} | 话题: {r['话题']}\n"
        )

    prompt += "\n## 本周财经要闻\n\n"
    for n in finance_news:
        prompt += f"- [{n['来源']}] {n['标题']}\n"

    prompt += "\n## 科技公司动态\n\n"
    for n in tech_news:
        prompt += f"- [{n['公司']}] {n['标题']}\n"

    if alerts_section:
        prompt += "\n" + alerts_section + "\n"

    if analysis_section:
        prompt += "\n" + analysis_section + "\n"

    prompt += f"""

---

## 你的任务

基于以上全部数据，撰写一份《每周趋势雷达》
({WEEK_STR}, {NOW.strftime('%Y年%m月%d日')})。

按以下结构输出，用中文，Markdown 格式:

### 一、本周核心判断

最重要的 3-5 个趋势信号。每个包括:
- **信号名称**
- **为什么重要** (2-3 句)
- **证据链** (来自哪几个数据源的哪几条，交叉印证了什么)

### 二、各维度详情

#### 2.1 资本信号: 资金在从哪往哪流
- 本周涨幅前三和跌幅前三的板块
- 这些流向暗示什么产业趋势 (联系具体新闻)

#### 2.2 代码信号: 开发者在疯狂造什么
- GitHub 热榜反映的主题聚类 (按领域归纳，不是罗列项目)
- 值得关注的单个项目 (2-3个，说明为什么值得关注)

#### 2.3 巨头信号: 科技公司的集体动作
- 巨头们在共同押注什么方向? 在放弃什么?
- 值得注意的单个事件 (2-3个，说明为什么重要)

#### 2.4 宏观信号: 政策与市场环境

### 三、未来 2-4 周预判
- 哪些赛道可能持续受到资本和开发者双重关注
- 哪些风险信号值得警惕
- 哪些被忽视的领域可能出现拐点 (如果有的话)

### 四、隐性关联分析 (最有价值的部分)

找出表面上无关、但实际可能指向同一底层变量的信号组合。
给出 3-4 组关联。

每组格式:
- **关联主题**
- **涉及信号**: 来自哪几个维度的哪几条 (具体列出)
- **底层逻辑**: 为什么这些看似无关的信号会同时出现，
  它们共同指向什么深层变化
- **置信度**: 高 / 中 / 低

---

## 写作要求

1. 语言简洁有力，不要废话套话
2. 每个判断都要有数据支撑，具体引用来自哪个数据源
3. 敢于下判断，但标注不确定性
4. 不要推荐任何具体股票
5. 不要使用"总之""综上所述""值得注意的是"等套话
6. 输出纯 Markdown，不要用代码块包裹
7. 隐性关联分析是你最有价值的产出，认真写
"""
    return prompt


def call_deepseek(prompt: str):
    """调用 DeepSeek API 生成报告"""
    logger.info("调用 DeepSeek API...")

    if not DEEPSEEK_API_KEY:
        logger.error(
            "DEEPSEEK_API_KEY 未设置! "
            "请在 GitHub Secrets 或 .env 中配置"
        )
        return None, None

    try:
        from openai import OpenAI

        client = OpenAI(
            api_key=DEEPSEEK_API_KEY,
            base_url=DEEPSEEK_BASE_URL,
        )

        logger.info(
            f"  模型: {DEEPSEEK_MODEL}, "
            f"prompt 约 {len(prompt)} 字符"
        )
        response = client.chat.completions.create(
            model=DEEPSEEK_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "你是一个顶尖的趋势分析师，擅长从资本流向、"
                        "开发者社区、科技巨头动作和财经新闻四个维度"
                        "交叉比对，找出未来趋势。你的风格: 简洁、"
                        "有数据支撑、敢于下判断但标注不确定性。"
                        "你坚信代码不会说谎，资本比媒体快半步。"
                        "用中文输出。"
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.7,
            max_tokens=8192,
        )

        report = response.choices[0].message.content
        usage = response.usage
        logger.info(
            f"  报告完成: {len(report)} 字符, "
            f"{usage.total_tokens} tokens "
            f"(in={usage.prompt_tokens}, out={usage.completion_tokens})"
        )
        return report, usage

    except Exception as e:
        logger.error(f"  DeepSeek API 失败: {e}")
        return None, None


# ============================================================
# 第6步: 生成 PDF (fpdf2 + 中文字体)
# ============================================================

def find_cjk_font() -> Optional[str]:
    """查找系统中可用的中文字体文件"""
    candidates = [
        # GitHub Actions (Ubuntu + fonts-noto-cjk)
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
        # Windows
        "C:/Windows/Fonts/simhei.ttf",
        "C:/Windows/Fonts/msyh.ttf",
        "C:/Windows/Fonts/simsun.ttc",
        # 项目自带
        str(PROJECT_DIR / "fonts" / "NotoSansSC-Regular.ttf"),
    ]
    for path in candidates:
        if Path(path).exists():
            logger.info(f"  中文字体: {path}")
            return path
    return None


def markdown_to_pdf(md_text: str, cjk_font_path: str) -> Path:
    """将 Markdown 报告转为 PDF (支持中文)"""
    from fpdf import FPDF

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=20)

    font_name = "CJK"
    try:
        pdf.add_font(font_name, "", cjk_font_path)
        try:
            pdf.add_font(font_name, "B", cjk_font_path)
        except Exception:
            pass  # TTC 可能不支持粗体注册
    except Exception as e:
        logger.error(f"  字体注册失败: {e}")
        raise

    # ---- 封面 ----
    pdf.add_page()
    pdf.ln(50)
    pdf.set_font(font_name, "B", 32)
    pdf.set_text_color(30, 30, 30)
    pdf.cell(0, 18, "每周趋势雷达", align="C",
             new_x="LMARGIN", new_y="NEXT")
    pdf.ln(8)
    pdf.set_font(font_name, "", 14)
    pdf.set_text_color(100, 100, 100)
    pdf.cell(0, 10, "资本 * 代码 * 巨头的交叉信号",
             align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(15)
    pdf.set_draw_color(200, 200, 200)
    y = pdf.get_y()
    pdf.line(60, y, 150, y)
    pdf.ln(15)
    pdf.set_font(font_name, "", 12)
    pdf.set_text_color(80, 80, 80)
    pdf.cell(0, 8, WEEK_STR, align="C",
             new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 8,
             f"生成日期: {NOW.strftime('%Y年%m月%d日')}",
             align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(30)
    pdf.set_font(font_name, "", 9)
    pdf.set_text_color(150, 150, 150)
    pdf.cell(0, 7, "数据源: Yahoo Finance / GitHub API / RSS",
             align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 7, "分析引擎: DeepSeek LLM",
             align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 7,
             "本报告由 AI 自动生成，仅供参考，不构成投资建议",
             align="C", new_x="LMARGIN", new_y="NEXT")

    # ---- 正文 ----
    pdf.add_page()
    pdf.set_text_color(40, 40, 40)

    lines = md_text.split("\n")
    in_code_block = False

    for line in lines:
        stripped = line.strip()

        # 跳过代码块标记
        if stripped.startswith("```"):
            in_code_block = not in_code_block
            pdf.ln(2)
            continue
        if in_code_block:
            pdf.set_font(font_name, "", 8)
            pdf.set_text_color(80, 80, 80)
            pdf.multi_cell(0, 4, stripped)
            continue

        if not stripped:
            pdf.ln(2)
            continue

        # 一级标题
        if (stripped.startswith("# ")
                and not stripped.startswith("## ")):
            pdf.ln(4)
            pdf.set_font(font_name, "B", 16)
            pdf.set_text_color(30, 30, 30)
            pdf.multi_cell(0, 9, stripped[2:])
            pdf.ln(3)
            continue

        # 二级标题
        if stripped.startswith("## "):
            pdf.ln(3)
            pdf.set_font(font_name, "B", 13)
            pdf.set_text_color(50, 50, 50)
            pdf.multi_cell(0, 7, stripped[3:])
            pdf.ln(1)
            continue

        # 三级标题
        if stripped.startswith("### "):
            pdf.ln(2)
            pdf.set_font(font_name, "B", 11)
            pdf.set_text_color(60, 60, 60)
            pdf.multi_cell(0, 6, stripped[4:])
            continue

        # 四级标题
        if stripped.startswith("#### "):
            pdf.ln(1)
            pdf.set_font(font_name, "B", 10)
            pdf.set_text_color(70, 70, 70)
            pdf.multi_cell(0, 6, stripped[5:])
            continue

        # 分隔线
        if stripped == "---":
            pdf.ln(2)
            pdf.set_draw_color(200, 200, 200)
            y_pos = pdf.get_y()
            pdf.line(pdf.l_margin, y_pos,
                     pdf.w - pdf.r_margin, y_pos)
            pdf.ln(2)
            continue

        # 清除 Markdown 格式标记
        clean = re.sub(r"\*\*(.+?)\*\*", r"\1", stripped)
        clean = re.sub(r"\*(.+?)\*", r"\1", clean)
        clean = re.sub(r"`(.+?)`", r"\1", clean)

        # 列表项
        if re.match(r"^[-*]\s", clean):
            pdf.set_font(font_name, "", 9)
            pdf.set_text_color(40, 40, 40)
            pdf.cell(4, 5, "-")
            pdf.multi_cell(0, 5, clean[2:])
            continue

        # 编号列表
        if re.match(r"^\d+\.\s", clean):
            pdf.set_font(font_name, "", 9)
            pdf.set_text_color(40, 40, 40)
            pdf.multi_cell(0, 5, clean)
            continue

        # 普通段落
        pdf.set_font(font_name, "", 9)
        pdf.set_text_color(40, 40, 40)
        pdf.multi_cell(0, 5, clean)

    # 保存
    filename = f"WeeklyTrendRadar_{NOW.strftime('%Y%m%d')}.pdf"
    filepath = OUTPUT_DIR / filename
    pdf.output(str(filepath))
    logger.info(f"  PDF 已保存: {filepath}")
    return filepath


def generate_pdf(report_md: str) -> Optional[Path]:
    """生成 PDF，找不到字体则跳过"""
    logger.info("生成 PDF...")
    font_path = find_cjk_font()
    if not font_path:
        logger.warning("  未找到中文字体，跳过 PDF 生成")
        return None
    try:
        return markdown_to_pdf(report_md, font_path)
    except Exception as e:
        logger.error(f"  PDF 生成失败: {e}")
        return None


# ============================================================
# 第7步: 简易 Markdown -> HTML (邮件正文)
# ============================================================

def md_to_html(md_text: str) -> str:
    """将 Markdown 报告转为 HTML 邮件正文"""
    lines = md_text.strip().split("\n")
    html_parts = []
    in_ul = False
    in_ol = False
    in_blockquote = False

    for line in lines:
        stripped = line.strip()

        # 代码块
        if stripped.startswith("```"):
            if in_ul:
                html_parts.append("</ul>")
                in_ul = False
            if in_ol:
                html_parts.append("</ol>")
                in_ol = False
            html_parts.append("<br>")
            continue

        if not stripped:
            if in_ul:
                html_parts.append("</ul>")
                in_ul = False
            if in_ol:
                html_parts.append("</ol>")
                in_ol = False
            if in_blockquote:
                html_parts.append("</blockquote>")
                in_blockquote = False
            html_parts.append("<br>")
            continue

        # 引用块
        if stripped.startswith("> "):
            if in_ul:
                html_parts.append("</ul>")
                in_ul = False
            if not in_blockquote:
                html_parts.append(
                    '<blockquote style="border-left:3px solid #ccc; '
                    'margin:8px 0; padding:4px 12px; color:#555">'
                )
                in_blockquote = True
            content = re.sub(
                r"\*\*(.+?)\*\*", r"<b>\1</b>", stripped[2:]
            )
            html_parts.append(f"<p>{content}</p>")
            continue

        if in_blockquote:
            html_parts.append("</blockquote>")
            in_blockquote = False

        # 标题
        if stripped.startswith("#### "):
            if in_ul:
                html_parts.append("</ul>")
                in_ul = False
            html_parts.append(
                f"<h4 style='color:#444'>{stripped[5:]}</h4>"
            )
        elif stripped.startswith("### "):
            if in_ul:
                html_parts.append("</ul>")
                in_ul = False
            html_parts.append(
                f"<h3 style='color:#333; margin-top:20px'>"
                f"{stripped[4:]}</h3>"
            )
        elif stripped.startswith("## "):
            if in_ul:
                html_parts.append("</ul>")
                in_ul = False
            html_parts.append(
                f"<h2 style='color:#222; margin-top:24px; "
                f"border-bottom:1px solid #ddd; padding-bottom:4px'>"
                f"{stripped[3:]}</h2>"
            )
        elif stripped.startswith("# "):
            if in_ul:
                html_parts.append("</ul>")
                in_ul = False
            html_parts.append(
                f"<h1 style='color:#111; margin-top:28px'>"
                f"{stripped[2:]}</h1>"
            )
        elif stripped == "---":
            if in_ul:
                html_parts.append("</ul>")
                in_ul = False
            html_parts.append(
                "<hr style='border:none; border-top:1px solid #ddd; "
                "margin:16px 0'>"
            )
        elif re.match(r"^[-*]\s", stripped):
            if in_ol:
                html_parts.append("</ol>")
                in_ol = False
            if not in_ul:
                html_parts.append(
                    '<ul style="padding-left:20px">'
                )
                in_ul = True
            content = re.sub(
                r"\*\*(.+?)\*\*", r"<b>\1</b>", stripped[2:]
            )
            html_parts.append(f"<li>{content}</li>")
        elif re.match(r"^\d+\.\s", stripped):
            if in_ul:
                html_parts.append("</ul>")
                in_ul = False
            if not in_ol:
                html_parts.append(
                    '<ol style="padding-left:20px">'
                )
                in_ol = True
            content = re.sub(
                r"\*\*(.+?)\*\*", r"<b>\1</b>", stripped
            )
            html_parts.append(f"<li>{content}</li>")
        else:
            if in_ul:
                html_parts.append("</ul>")
                in_ul = False
            if in_ol:
                html_parts.append("</ol>")
                in_ol = False
            content = re.sub(
                r"\*\*(.+?)\*\*", r"<b>\1</b>", stripped
            )
            content = re.sub(r"\*(.+?)\*", r"<i>\1</i>", content)
            content = re.sub(
                r"`(.+?)`", r"<code>\1</code>", content
            )
            html_parts.append(f"<p>{content}</p>")

    if in_ul:
        html_parts.append("</ul>")
    if in_ol:
        html_parts.append("</ol>")
    if in_blockquote:
        html_parts.append("</blockquote>")

    body = "\n".join(html_parts)

    return (
        "<!DOCTYPE html>\n"
        '<html lang="zh-CN">\n'
        '<head><meta charset="utf-8"></head>\n'
        '<body style="font-family:-apple-system,\'Microsoft YaHei\','
        "'PingFang SC',sans-serif; max-width:720px; margin:0 auto; "
        "padding:20px; color:#333; line-height:1.8; font-size:14px; "
        'background:#fff;">\n'
        '<div style="background:#f8f8f8; padding:20px; '
        'border-radius:8px; margin-bottom:24px;">\n'
        '  <h1 style="color:#1a1a1a; margin:0 0 4px 0; '
        'font-size:22px;">'
        '每周趋势雷达'
        '</h1>\n'
        '  <p style="color:#888; margin:0; font-size:12px;">\n'
        f'    {WEEK_STR} / 资本*代码*巨头的交叉信号'
        f' / {NOW.strftime("%Y-%m-%d %H:%M")}\n'
        '  </p>\n'
        '</div>\n'
        f'{body}\n'
        '<hr style="border:none; border-top:1px solid #eee; '
        'margin:24px 0">\n'
        '<p style="color:#aaa; font-size:11px; text-align:center;">\n'
        '本报告由 DeepSeek LLM 自动分析生成，仅供参考，'
        '不构成任何投资建议。<br>\n'
        '数据源: Yahoo Finance / GitHub API / RSS\n'
        '</p>\n'
        '</body>\n'
        '</html>'
    )


# ============================================================
# 第8步: 发送邮件
# ============================================================

def send_email(pdf_path: Optional[Path], report_md: str) -> bool:
    """通过 QQ 邮箱发送报告 (HTML 正文 + PDF 附件)"""
    logger.info("发送邮件...")

    if not QQ_EMAIL or not QQ_SMTP_CODE:
        logger.error("QQ_EMAIL 或 QQ_SMTP_CODE 未设置!")
        return False

    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    from email.mime.base import MIMEBase
    from email import encoders

    msg = MIMEMultipart("mixed")
    msg["From"] = QQ_EMAIL
    msg["To"] = RECIPIENT_EMAIL
    msg["Subject"] = (
        f"[每周趋势雷达] {WEEK_STR} ({NOW.strftime('%m/%d')})"
    )

    # HTML 正文
    html_body = md_to_html(report_md)
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    # PDF 附件
    if pdf_path and pdf_path.exists():
        with open(pdf_path, "rb") as f:
            attachment = MIMEBase("application", "pdf")
            attachment.set_payload(f.read())
            encoders.encode_base64(attachment)
            attachment.add_header(
                "Content-Disposition",
                "attachment",
                filename=("utf-8", "", pdf_path.name),
            )
            msg.attach(attachment)
        logger.info(f"  附件: {pdf_path.name}")
    else:
        logger.info("  无 PDF 附件 (仅 HTML 邮件)")

    try:
        with smtplib.SMTP_SSL(
            QQ_SMTP_SERVER, QQ_SMTP_PORT
        ) as server:
            server.login(QQ_EMAIL, QQ_SMTP_CODE)
            server.send_message(msg)
        logger.info(f"  邮件已发送 -> {RECIPIENT_EMAIL}")
        return True
    except Exception as e:
        logger.error(f"  邮件发送失败: {e}")
        return False


# ============================================================
# 主流程
# ============================================================

def main():
    logger.info("=" * 60)
    logger.info(
        f"Weekly Trend Radar - Start ({NOW.isoformat()})"
    )
    logger.info("=" * 60)

    # ---- 初始化数据库 ----
    init_db()

    # ---- 拉取数据 ----
    sector_data = fetch_sector_data()
    save_sector_data(sector_data)

    top_repos, rising_repos = fetch_github_trending()
    save_github_data(top_repos, rising_repos)

    finance_news = fetch_finance_news()
    save_finance_news(finance_news)

    tech_news = fetch_tech_news()
    save_tech_news(tech_news)

    # ---- 检查关键数据 ----
    sector_ok = any(
        s.get("周涨跌幅") not in ("获取失败", "数据不足")
        for s in sector_data
    )
    if not sector_ok:
        logger.error("板块数据全部失败，但仍尝试生成报告")

    if not top_repos and not rising_repos:
        logger.warning("GitHub 数据为空 (可能被限流)")

    # ---- 数据透视（程序自行计算，非 LLM 生成）----
    daily_prices = fetch_daily_prices()
    corr_result = compute_sector_correlation(daily_prices)
    topic_result = compute_github_topic_clusters(top_repos, rising_repos)
    sentiment_result = compute_news_sentiment(finance_news)
    analysis_section = build_analysis_section(
        corr_result, topic_result, sentiment_result
    )

    # ---- 规则引擎（交叉信号异常检测）----
    alerts_section = run_rule_engine(
        sector_data, topic_result, sentiment_result
    )

    # ---- 调用 DeepSeek ----
    prompt = build_prompt(
        sector_data, top_repos, rising_repos,
        finance_news, tech_news, analysis_section, alerts_section
    )
    report_md, usage = call_deepseek(prompt)

    if not report_md:
        logger.error("DeepSeek 报告生成失败，流程终止")
        sys.exit(1)

    # ---- 存入数据库 ----
    save_report(report_md, usage)

    # ---- 保存 Markdown ----
    md_path = OUTPUT_DIR / f"report_{NOW.strftime('%Y%m%d')}.md"
    md_path.write_text(report_md, encoding="utf-8")
    logger.info(f"Markdown 已保存: {md_path}")

    # ---- 生成 PDF ----
    pdf_path = generate_pdf(report_md)

    # ---- 发送邮件 ----
    success = send_email(pdf_path, report_md)

    # ---- 收尾 ----
    logger.info("=" * 60)
    if success:
        logger.info(f"Done! Report sent to {RECIPIENT_EMAIL}")
    else:
        logger.warning(
            f"Report generated but email failed. See: {md_path}"
        )
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
