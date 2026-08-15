# -*- coding: utf-8 -*-
"""
前沿追踪 Agent（Frontier Radar）—— 让"文献版图"保持最新，而不是写完就过时

设计哲学（重要，回应"我们的 agent 是不是只是脚本"）：
  · 金融研判 agent 必须**受约束、可审计**（错误代价高：诽谤/亏钱）——所以那边是"脚本化编排"，刻意的；
  · 前沿追踪 agent **可以真正自主判断**（错误代价极低：多读一篇论文而已）——所以这里让 LLM 做
    "相关性判断 + 它是支持我还是挑战我"的真判断，而不只是关键词 grep。
  → 同一个人，按"错误代价"决定给多少自主性，这本身就是成熟的 agent 工程判断。

它做什么：
  ① 拉 arXiv 最近 N 天、指定分类（q-fin / cs.LG）的新论文；
  ② 对每篇，LLM 判断：和"我的四个关注方向"相关吗？相关在哪？是【支持/挑战/待跟进缺口】？
  ③ 把"值得读的 + 为什么相关 + 关系类型"追加进 前沿追踪.md，附 arXiv 链接（可直接下载）。
  ④ 特别标出"强相关/挑战型"——这些最可能改变研究计划或信号哲学。

用法（手动运行，不依赖会话常开）：
  cd demo && python3 track_frontier.py               # 默认最近 7 天
  python3 track_frontier.py --days 14 --max 40        # 自定义窗口与抓取上限
  python3 track_frontier.py --no-llm                  # 无 LLM：只按关键词粗筛（降级，仍可用）

铁律（与项目同源）：
  - 只做"筛选+提示"，不改动研究结论；LLM 判断仅为辅助分类，不编造论文内容；
  - 无 LLM / 网络失败 → 优雅降级为关键词粗筛，不崩；
  - 输出附 arXiv 原始链接，人可自行核对，绝不替代人读原文。
"""
from __future__ import annotations

import argparse
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from xml.etree import ElementTree as ET

import llm

HERE = Path(__file__).resolve().parent
OUT = HERE.parent / "前沿追踪.md"

# 四个关注方向（来自研究计划；要加/减在此改一处即可）
FOCUS = {
    "GNN资产定价": ["graph neural network", "GNN", "asset pricing", "stock prediction", "relational graph"],
    "可解释关系图": ["interpretable", "explainable", "relational graph", "supply chain graph", "dynamic graph"],
    "供应链动量/网络传导": ["supply chain", "momentum", "lead-lag", "economic link", "network"],
    "LLM金融/多Agent": ["large language model", "LLM", "agent", "financial", "RAG", "factor mining"],
}
# arXiv 分类：量化金融 + 机器学习
CATEGORIES = ["q-fin.PM", "q-fin.ST", "q-fin.TR", "q-fin.CP", "q-fin.GN"]

ARXIV_API = "https://export.arxiv.org/api/query"
NS = {"a": "http://www.w3.org/2005/Atom"}


def fetch_arxiv(days: int, max_results: int) -> list[dict]:
    """拉取指定分类、最近 days 天的 arXiv 新论文。"""
    cat_q = "+OR+".join(f"cat:{c}" for c in CATEGORIES)
    url = (f"{ARXIV_API}?search_query={cat_q}"
           f"&start=0&max_results={max_results}"
           f"&sortBy=submittedDate&sortOrder=descending")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "frontier-radar/1.0"})
        with urllib.request.urlopen(req, timeout=40) as r:
            root = ET.fromstring(r.read())
    except Exception as e:
        print(f"⚠️ arXiv 拉取失败：{e}", file=sys.stderr)
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    papers = []
    for e in root.findall("a:entry", NS):
        pub_s = e.find("a:published", NS).text
        try:
            pub = datetime.fromisoformat(pub_s.replace("Z", "+00:00"))
        except Exception:
            pub = None
        if pub and pub < cutoff:
            continue
        papers.append({
            "title": " ".join(e.find("a:title", NS).text.split()),
            "abstract": " ".join(e.find("a:summary", NS).text.split()),
            "authors": [a.find("a:name", NS).text for a in e.findall("a:author", NS)][:4],
            "published": pub_s[:10],
            "url": e.find("a:id", NS).text,
            "arxiv_id": e.find("a:id", NS).text.split("/abs/")[-1],
        })
    return papers


def keyword_prefilter(p: dict) -> list[str]:
    """降级用：按关键词判断命中哪些关注方向。"""
    text = (p["title"] + " " + p["abstract"]).lower()
    hit = []
    for focus, kws in FOCUS.items():
        if any(k.lower() in text for k in kws):
            hit.append(focus)
    return hit


_JUDGE_SYS = (
    "你是一个前沿文献雷达，服务于一个'用可解释GNN+多Agent做A股财务舞弊检测与价值发现'的研究者。"
    "他关注四个方向：①GNN资产定价 ②可解释关系图（尤其批评'预定义静态图/不可解释'的工作）"
    "③供应链动量/网络传导 ④LLM金融/多Agent。\n"
    "给你一篇论文的标题和摘要，判断：\n"
    "1. relevant：是否与上述任一方向相关（true/false）；\n"
    "2. focus：相关的方向（从四个里选，可多选）；\n"
    "3. relation：它与研究者的关系——'支持'(支撑其思路)/'挑战'(可能质疑或竞争其方法)/"
    "'缺口'(指出可跟进的开放问题)/'背景'(一般相关)；\n"
    "4. why：一句话说明为什么相关（≤40字，不编造摘要没有的内容）。\n"
    "只输出JSON：{\"relevant\":bool,\"focus\":[..],\"relation\":\"..\",\"why\":\"..\"}"
)


def judge_paper(p: dict) -> dict | None:
    """LLM 判断相关性与关系类型。失败返回 None（调用方降级）。"""
    user = f"标题：{p['title']}\n\n摘要：{p['abstract'][:1500]}"
    return llm.chat(_JUDGE_SYS, user, model=llm.MODEL_FAST,
                    want_json=True, max_tokens=400, timeout=40)


_REL_BADGE = {"支持": "🟢 支持", "挑战": "🔴 挑战", "缺口": "🟡 缺口", "背景": "⚪ 背景"}


def run(days: int, max_results: int, use_llm: bool) -> None:
    print(f"📡 拉取 arXiv 最近 {days} 天（{'+'.join(CATEGORIES)}）…")
    papers = fetch_arxiv(days, max_results)
    print(f"   取到 {len(papers)} 篇，开始筛选…")

    llm_on = use_llm and llm.available()
    if use_llm and not llm_on:
        print("   ⚠️ 无 LLM 凭据，降级为关键词粗筛。")

    hits = []
    for p in papers:
        if llm_on:
            j = judge_paper(p)
            if j and j.get("relevant"):
                hits.append({**p, "focus": j.get("focus", []),
                             "relation": j.get("relation", "背景"), "why": j.get("why", "")})
        else:
            f = keyword_prefilter(p)
            if f:
                hits.append({**p, "focus": f, "relation": "背景", "why": "关键词命中（未启用LLM判断）"})

    # 强相关（挑战/缺口型）排前面——这些最可能改变研究计划
    order = {"挑战": 0, "缺口": 1, "支持": 2, "背景": 3}
    hits.sort(key=lambda h: order.get(h["relation"], 9))

    _write_report(days, len(papers), hits, llm_on)
    print(f"✅ 命中 {len(hits)} 篇 → 已写入 {OUT.name}")
    chal = [h for h in hits if h["relation"] in ("挑战", "缺口")]
    if chal:
        print(f"   ⭐ 其中 {len(chal)} 篇为【挑战/缺口】型，值得优先看：")
        for h in chal[:5]:
            print(f"      · [{h['relation']}] {h['title'][:60]}  ({h['arxiv_id']})")


def _write_report(days, total, hits, llm_on) -> None:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    lines = [f"# 前沿追踪雷达 · 更新于 {stamp}", "",
             f"> 由 `demo/track_frontier.py` 生成。本次扫描 arXiv 最近 {days} 天 "
             f"（{'+'.join(CATEGORIES)}），共 {total} 篇，命中 {len(hits)} 篇。"
             f"{'（LLM 判断）' if llm_on else '（关键词粗筛，未启用 LLM）'}",
             "> 关系标注：🔴挑战（可能质疑/竞争我的方法）｜🟡缺口（可跟进的开放问题）｜🟢支持｜⚪背景。",
             "> ⚠️ 仅为筛选提示，不替代读原文；LLM 判断可能有误，链接可自行核对。", ""]
    if not hits:
        lines.append("_本次窗口内未命中相关论文。可加大 --days 窗口重试。_")
    for i, h in enumerate(hits, 1):
        badge = _REL_BADGE.get(h["relation"], h["relation"])
        au = ", ".join(h["authors"]) + (" 等" if len(h["authors"]) >= 4 else "")
        lines += [
            f"## {i}. {badge} · {h['title']}",
            f"- **方向**：{'、'.join(h['focus']) or '—'}　**发布**：{h['published']}　**作者**：{au}",
            f"- **为何相关**：{h['why']}",
            f"- **链接**：{h['url']}　（下载：`https://arxiv.org/pdf/{h['arxiv_id']}`）",
            "",
        ]
    lines += ["---", "",
              "## 历史归档说明",
              "本文件每次运行会**覆盖**为最新一期。若要留存历史，运行前手动改名归档，",
              "或把值得长期跟踪的条目手动搬进 `文献综述_成文版.md` 的相应方向。"]
    OUT.write_text("\n".join(lines), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description="前沿文献追踪雷达")
    ap.add_argument("--days", type=int, default=7, help="回溯天数（默认7）")
    ap.add_argument("--max", type=int, default=40, help="arXiv 抓取上限（默认40）")
    ap.add_argument("--no-llm", action="store_true", help="不用LLM，仅关键词粗筛")
    args = ap.parse_args()
    run(args.days, args.max, use_llm=not args.no_llm)


if __name__ == "__main__":
    main()
