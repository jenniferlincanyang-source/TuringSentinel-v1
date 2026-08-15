# -*- coding: utf-8 -*-
"""
图灵哨兵 · 种子信号库 —— 通用信号引擎（确定性，不依赖大模型）

设计目标（对应 GOAI 大赛"可复现/独立/可扩展"）：
    - 每条信号 = 一个纯函数：输入某年财务科目 dict → 输出命中判据 + 得分
    - 信号统一注册到 REGISTRY，新增信号只需在 signals_registry.py 里再写一个函数
    - 引擎负责：遍历公司各年数据 → 跑所有信号 → 汇总档位 → 打印可审计报告
    - 全程无 AI、无网络，结果确定可复现

档位规则（组合触发，源自方法论"单点合规、交叉见鬼"）：
    得分 ≥6 极高 / ≥4 高 / ≥2 中 / ≥1 低 / 0 无
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

YI = 1e8  # 元 → 亿元


@dataclass
class Hit:
    """一条命中的判据。"""
    text: str
    points: int


@dataclass
class SignalOutput:
    """单条信号对某年数据的评估结果。"""
    signal_id: str
    signal_name: str
    hits: List[Hit] = field(default_factory=list)
    score: int = 0
    note: str = ""      # 数据缺失等说明

    @property
    def triggered(self) -> bool:
        return self.score > 0


def level_of(score: int) -> str:
    if score >= 6:
        return "极高"
    if score >= 4:
        return "高"
    if score >= 2:
        return "中"
    if score >= 1:
        return "低"
    return "无"


BADGE = {"极高": "🔴 极高", "高": "🟠 高", "中": "🟡 中", "低": "🔵 低", "无": "⚪ 无"}


# 信号函数签名：(year_data, prev_year_data, peer_benchmark) -> SignalOutput
SignalFn = Callable[[dict, Optional[dict], Optional[dict]], SignalOutput]

# 全局注册表：signal_id -> SignalFn
REGISTRY: Dict[str, SignalFn] = {}


def register(signal_id: str):
    """装饰器：把一条信号注册进信号库。"""
    def deco(fn: SignalFn) -> SignalFn:
        REGISTRY[signal_id] = fn
        return fn
    return deco


def score_year(d: dict, prev: Optional[dict] = None,
               peer: Optional[dict] = None) -> tuple[list[str], int]:
    """
    对单年数据跑全部已注册信号，返回 (命中判据文本列表, 合计得分)。
    这是给程序调用的口子（demo 后端/编排层复用），与打印版 evaluate_company 共用同一套信号，
    杜绝"信号逻辑写两遍、改一处漏一处"。
    """
    hits: list[str] = []
    total = 0
    for sid in sorted(REGISTRY.keys()):
        out = REGISTRY[sid](d, prev, peer)
        total += out.score
        hits.extend(h.text for h in out.hits)
    return hits, total


def score_year_detail(d: dict, prev: Optional[dict] = None,
                       peer: Optional[dict] = None) -> list[dict]:
    """
    对单年数据跑全部信号，返回**按信号分维度**的明细（矩阵表用）。
    与 score_year 共用同一套 REGISTRY，仍是唯一真相源；这里只是不做平铺、保留每条信号的归属。
    返回：[{id, name, level, score, status, basis:[...], note}]
      status: triggered(命中) / clear(未触发) / na(数据缺失未评估)
    """
    detail: list[dict] = []
    for sid in sorted(REGISTRY.keys()):
        out = REGISTRY[sid](d, prev, peer)
        if out.triggered:
            status = "triggered"
        elif out.note:
            status = "na"
        else:
            status = "clear"
        detail.append({
            "id": out.signal_id,
            "name": out.signal_name,
            "level": level_of(out.score),
            "score": out.score,
            "status": status,
            "basis": [h.text for h in out.hits],
            "note": out.note,
        })
    return detail


def evaluate_company(data: dict) -> None:
    """对一家公司的多年数据，跑全部已注册信号并打印可审计报告。"""
    company = data.get("company", "未知公司")
    ticker = data.get("ticker", "")
    peer = data.get("industry_peer_benchmark")
    fy = data["fiscal_years"]
    years = sorted(fy.keys())

    print("=" * 80)
    print(f"图灵哨兵 · 种子信号库  |  {company}（{ticker}）")
    print(f"已加载信号：{', '.join(sorted(REGISTRY.keys()))}")
    print("=" * 80)
    print(f"数据来源：{data.get('source_note', '')}\n")

    for i, year in enumerate(years):
        d = fy[year]
        prev = fy[years[i - 1]] if i > 0 else None
        total_score = 0
        outs: List[SignalOutput] = []
        for sid in sorted(REGISTRY.keys()):
            out = REGISTRY[sid](d, prev, peer)
            outs.append(out)
            total_score += out.score

        agg_level = level_of(total_score)
        print(f"—— {year} 年 ——————————————————————————————————————————————")
        for out in outs:
            if out.triggered:
                lv = BADGE[level_of(out.score)]
                print(f"  [{out.signal_id}] {out.signal_name} → {lv}（{out.score}分）")
                for h in out.hits:
                    print(f"      ✓ {h.text}")
            elif out.note:
                print(f"  [{out.signal_id}] {out.signal_name} → 未评估（{out.note}）")
            else:
                print(f"  [{out.signal_id}] {out.signal_name} → ⚪ 未触发")

        print(f"  ───> 本年综合风险档位：{BADGE[agg_level]}（合计 {total_score} 分）")

        inflated = d.get("fraud_inflated_monetary_funds")
        if inflated:
            real = d.get("monetary_funds", 0) - inflated
            print(f"  [事后验证] 证监会认定虚增货币资金 {inflated/YI:.2f} 亿 "
                  f"→ 真实约 {real/YI:.2f} 亿")
        print()

    print("-" * 80)
    print("说明：以上判定全部由【公开财报数字 + 确定性规则】得出，无大模型参与，可逐条审计复现。")
    print("      系统输出为风险信号提示，不构成审计结论或投资建议；高风险线索需人工复核。")
    print("-" * 80)
