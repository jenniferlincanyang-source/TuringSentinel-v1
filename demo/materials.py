# -*- coding: utf-8 -*-
"""
RiskCopilot · 资料层（A：资料理解的"取材"部分）

从公开渠道拉"文本类"材料——数字报表看不见、却是资深审计"闻味道"的地方：
  - 巨潮公告标题+链接（stock_zh_a_disclosure_report_cninfo）：立案调查、问询函、
    变更会计师、控股股东质押/冻结、业绩预告修正、对外担保、媒体澄清……
  - 东财个股新闻（stock_news_em）：舆情补充

只取标题+链接+日期（便宜、可溯源，不下载解析 PDF 正文——那留复赛）。
网络失败一律降级返回空列表，绝不阻断主流程。
"""
from __future__ import annotations

import warnings
warnings.filterwarnings("ignore")

# 定性红旗关键词 → 归类（供 LLM 之前的粗筛，减少噪音、也让降级路径仍可用）
REDFLAG_KEYWORDS = {
    "立案调查": "监管立案", "立案": "监管立案", "行政处罚": "监管处罚", "警示函": "监管处罚",
    "问询函": "交易所问询", "关注函": "交易所问询", "监管函": "交易所问询",
    "更正": "财报更正", "重述": "财报更正", "会计差错": "财报更正", "追溯调整": "财报更正",
    "业绩预告修正": "业绩变脸", "业绩修正": "业绩变脸", "预亏": "业绩变脸", "商誉减值": "商誉减值",
    "变更会计师": "更换审计机构", "改聘会计师": "更换审计机构", "解聘": "更换审计机构",
    "非标准": "非标审计意见", "保留意见": "非标审计意见", "无法表示意见": "非标审计意见",
    "质押": "股东质押", "冻结": "股东冻结/占用", "占用": "股东冻结/占用", "违规担保": "违规担保",
    "对外担保": "对外担保", "涉诉": "重大诉讼", "诉讼": "重大诉讼", "债务违约": "债务违约",
    "澄清": "媒体质疑澄清", "媒体报道": "媒体质疑澄清", "退市": "退市风险", "*ST": "退市风险",
    "延期回复": "拖延回复",
}


def classify_title(title: str) -> str | None:
    """标题命中哪类定性红旗；没命中返回 None。"""
    for kw, cat in REDFLAG_KEYWORDS.items():
        if kw in title:
            return cat
    return None


def fetch_disclosures(code: str, start: str, end: str, limit: int = 400) -> list[dict]:
    """拉巨潮公告标题+链接+时间。返回 [{title, date, url, category}]，失败返回 []。"""
    import akshare as ak
    try:
        df = ak.stock_zh_a_disclosure_report_cninfo(
            symbol=code, market="沪深京", start_date=start, end_date=end)
    except Exception:
        return []
    out = []
    for _, r in df.head(limit).iterrows():
        title = str(r.get("公告标题", "")).strip()
        if not title:
            continue
        out.append({
            "title": title,
            "date": str(r.get("公告时间", ""))[:10],
            "url": str(r.get("公告链接", "")).strip(),
            "category": classify_title(title),
        })
    return out


def screen_redflags(disclosures: list[dict]) -> list[dict]:
    """粗筛出命中定性红旗关键词的公告（去重标题，保留最早一条便于溯源）。"""
    seen, flags = set(), []
    for d in disclosures:
        if d["category"] and d["title"] not in seen:
            seen.add(d["title"])
            flags.append(d)
    return flags


def fetch_news(code: str, limit: int = 8) -> list[dict]:
    """拉东财个股新闻标题+链接（舆情补充）。失败返回 []。"""
    import akshare as ak
    try:
        df = ak.stock_news_em(symbol=code)
    except Exception:
        return []
    out = []
    for _, r in df.head(limit).iterrows():
        out.append({
            "title": str(r.get("新闻标题", "")).strip(),
            "date": str(r.get("发布时间", ""))[:10],
            "url": str(r.get("新闻链接", "")).strip(),
            "source": str(r.get("文章来源", "")).strip(),
        })
    return out


def gather(code: str, year_from: int, year_to: int) -> dict:
    """
    一站式取材：给定年份区间，拉公告并粗筛红旗 + 拉新闻。
    返回 {disclosures_total, redflags:[...], news:[...]}，均可为空（降级）。
    """
    start, end = f"{year_from}0101", f"{year_to}1231"
    disc = fetch_disclosures(code, start, end)
    return {
        "disclosures_total": len(disc),
        "redflags": screen_redflags(disc),
        "news": fetch_news(code),
    }
