# -*- coding: utf-8 -*-
"""
RiskCopilot · 价值发现引擎 —— "链热单体冷"错配扫描（确定性，不依赖大模型）

这是"价值发现正脸"的入口引擎。它把用户一次真实赚钱的直觉
（"同产业别的标的都冲高了，这个还在40没人注意，买入→100落袋"）
拆成**不带公司名、可机械复现**的错配规则，长在四条判据上：

  ① 链热：这条产业链的锚（龙头）估值/涨幅已抬到高位
  ② 单体冷：某成员自己的估值分位/关注度/换手还在低位（← 本引擎主场）
  ③ 归属为真：它和龙头是真实产业关联，不是题材联想（← 交给 agent_attribution 控辩核验）
  ④ 冷得干净：便宜是因为被漏看，不是因为有雷（← 交给 RiskCopilot 双引擎排雷）

═══════════════════ 铁律（与反舞弊同源，不可动摇）═══════════════════
  1. 本引擎**不预测涨跌、不给买入建议、不给估值数字**。它只回答一个"事实问题"：
     这个标的相对同链龙头，是否处在"链热+单体冷"的结构错配位置。
  2. 输出是"值得人工深看的候选 + 证据"，机器发现错配，人判断机会还是陷阱。
  3. "冷"必须叠加成长护栏：冷且基本面在长 = 候选；冷但在萎缩 = 大概率"冷得有理由"，降级。
  4. 全程无 AI、无预测模型，纯确定性规则，可逐条复现（对齐 signals/ 的可复现精神）。

设计与 signals/engine.py 同构：dataclass + @register 装饰器，新增判据照葫芦画瓢。

字段约定（member dict，缺失允许为 None，判据内部自行判断能否评估）：
  code, name                     标的代码/简称
  pe, pb                         估值（市盈率/市净率）
  pe_pct_in_chain                该标的 PE 在本链成员中的分位（0-1，越低越"冷"）
  ret_1y                         近一年涨幅（0.30 = +30%）
  turnover                       近期换手率（低 = 关注度低）
  analyst_coverage               研报覆盖家数（少 = 被漏看）
  rev_growth                     营收同比增速（成长护栏：防"冷得有理由"）
  profit_growth                  净利润同比增速（成长护栏）
chain dict（这条链的整体温度，龙头锚）：
  name                           链/板块名
  leader_ret_1y                  龙头近一年涨幅（衡量"链有多热"）
  leader_pe_pct                  龙头 PE 所处历史/板块分位（衡量估值是否已抬）
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional


# ---------------- 可配置阈值（均有直觉/方法论出处，非拍脑袋）----------------
# 链热判据（锚：龙头）
TH_LEADER_RET = 0.30        # 龙头近一年涨幅 ≥30% 视为"链在热"
TH_LEADER_PE_PCT = 0.60     # 龙头估值分位 ≥60% 视为"预期已打进价格"
# 单体冷判据
TH_PE_PCT_COLD = 0.40       # 该标的 PE 在链内分位 ≤40% 视为"估值still冷"
TH_RET_LAG = 0.15           # 自己近一年涨幅 ≤15% 视为"没跟上"
TH_COVERAGE_THIN = 8        # 研报覆盖 ≤8 家视为"被漏看"
TH_TURNOVER_LOW = 0.02      # 换手率 ≤2% 视为"关注度低"
# 成长护栏（防"冷得有理由"）
TH_GROWTH_FLOOR = 0.0       # 营收/利润增速需 >0，否则"冷"可能是萎缩活该便宜


@dataclass
class Signal:
    """一条命中的错配判据。"""
    text: str
    points: int


@dataclass
class MismatchOutput:
    """单条错配判据对某成员的评估结果。"""
    rule_id: str
    rule_name: str
    signals: List[Signal] = field(default_factory=list)
    score: int = 0
    note: str = ""      # 数据缺失等说明

    @property
    def triggered(self) -> bool:
        return self.score > 0


def cold_level(score: int) -> str:
    """错配强度档位（分越高，'链热单体冷'的结构越明显，越值得人工深看）。"""
    if score >= 6:
        return "强错配"
    if score >= 4:
        return "中错配"
    if score >= 2:
        return "弱错配"
    if score >= 1:
        return "微错配"
    return "无错配"


BADGE = {"强错配": "🟢 强错配", "中错配": "🟡 中错配", "弱错配": "🔵 弱错配",
         "微错配": "⚪ 微错配", "无错配": "⚪ 无错配"}

# 判据函数签名：(member, chain) -> MismatchOutput
RuleFn = Callable[[dict, dict], MismatchOutput]
REGISTRY: Dict[str, RuleFn] = {}


def register(rule_id: str):
    """装饰器：把一条错配判据注册进引擎（与 signals 引擎同一套写法）。"""
    def deco(fn: RuleFn) -> RuleFn:
        REGISTRY[rule_id] = fn
        return fn
    return deco


def _g(d: dict, key: str):
    """安全取数，缺失返回 None。"""
    v = d.get(key)
    return v if isinstance(v, (int, float)) else None


# ============================================================
# 判据 V1：链热（这条链的锚——龙头——是否已被市场充分定价）
#   这是前置门槛：链不热，就没有"错配"可言（谈不上"别人冲高了它没跟上"）。
# ============================================================
@register("V1")
def rule_chain_hot(member: dict, chain: dict) -> MismatchOutput:
    out = MismatchOutput("V1", "链热（龙头已被充分定价）")
    ret = _g(chain, "leader_ret_1y")
    pe_pct = _g(chain, "leader_pe_pct")
    if ret is None and pe_pct is None:
        out.note = "缺龙头涨幅/估值分位，无法判断链是否在热"
        return out
    if ret is not None and ret >= TH_LEADER_RET:
        out.signals.append(Signal(
            f"龙头近一年+{ret:.0%}（≥{TH_LEADER_RET:.0%}，链在热）", 1))
    if pe_pct is not None and pe_pct >= TH_LEADER_PE_PCT:
        out.signals.append(Signal(
            f"龙头估值分位{pe_pct:.0%}（≥{TH_LEADER_PE_PCT:.0%}，预期已打进价格）", 1))
    out.score = sum(s.points for s in out.signals)
    return out


# ============================================================
# 判据 V2：估值still冷（该标的自己估值分位低 + 没跟上龙头涨幅）
#   这是"单体冷"的核心——它便宜、且没被这波行情带起来。
# ============================================================
@register("V2")
def rule_valuation_cold(member: dict, chain: dict) -> MismatchOutput:
    out = MismatchOutput("V2", "估值still冷（没跟上）")
    pe_pct = _g(member, "pe_pct_in_chain")
    ret = _g(member, "ret_1y")
    if pe_pct is None and ret is None:
        out.note = "缺该标的估值分位/涨幅"
        return out
    if pe_pct is not None and pe_pct <= TH_PE_PCT_COLD:
        out.signals.append(Signal(
            f"估值分位在链内仅{pe_pct:.0%}（≤{TH_PE_PCT_COLD:.0%}，链里最便宜的一档）", 2))
    if ret is not None and ret <= TH_RET_LAG:
        gap = ""
        lr = _g(chain, "leader_ret_1y")
        if lr is not None:
            gap = f"，龙头+{lr:.0%}它只+{ret:.0%}，明显掉队"
        out.signals.append(Signal(
            f"自己近一年仅+{ret:.0%}（≤{TH_RET_LAG:.0%}{gap}）", 1))
    out.score = sum(s.points for s in out.signals)
    return out


# ============================================================
# 判据 V3：被漏看（关注度低——研报覆盖少 / 换手低）
#   "一直没人注意"的量化：卖方没覆盖、成交不活跃 = 市场注意力还没到。
# ============================================================
@register("V3")
def rule_overlooked(member: dict, chain: dict) -> MismatchOutput:
    out = MismatchOutput("V3", "被漏看（关注度低）")
    cov = _g(member, "analyst_coverage")
    turn = _g(member, "turnover")
    if cov is None and turn is None:
        out.note = "缺研报覆盖/换手数据"
        return out
    if cov is not None and cov <= TH_COVERAGE_THIN:
        out.signals.append(Signal(
            f"研报覆盖仅{int(cov)}家（≤{TH_COVERAGE_THIN}，卖方注意力还没到）", 1))
    if turn is not None and turn <= TH_TURNOVER_LOW:
        out.signals.append(Signal(
            f"换手率{turn:.1%}（≤{TH_TURNOVER_LOW:.0%}，交投清淡、未被资金关注）", 1))
    out.score = sum(s.points for s in out.signals)
    return out


# ============================================================
# 判据 V4：成长护栏（★防"冷得有理由"——这是把机会和陷阱分开的关键）
#   冷 + 基本面在长 = 可能是被漏看的机会（加分）
#   冷 + 基本面在萎缩 = 大概率"便宜是活该"的价值陷阱（扣分/否决）
#   注意：这条会给负分，用来把"冷得有理由"的标的从候选里压下去。
# ============================================================
@register("V4")
def rule_growth_guard(member: dict, chain: dict) -> MismatchOutput:
    out = MismatchOutput("V4", "成长护栏（冷得干净 vs 冷得有理由）")
    rg = _g(member, "rev_growth")
    pg = _g(member, "profit_growth")
    if rg is None and pg is None:
        out.note = "缺营收/利润增速，无法判断'冷'是被漏看还是在萎缩"
        return out
    growing = False
    if rg is not None and rg > TH_GROWTH_FLOOR:
        out.signals.append(Signal(f"营收仍在增长（+{rg:.0%}，'冷'更可能是被漏看而非衰退）", 1))
        growing = True
    if pg is not None and pg > TH_GROWTH_FLOOR:
        out.signals.append(Signal(f"净利润仍在增长（+{pg:.0%}）", 1))
        growing = True
    # 萎缩 → 负分，把"冷得有理由"的标的压下去（护栏的核心作用）
    if not growing:
        shrink = []
        if rg is not None and rg <= TH_GROWTH_FLOOR:
            shrink.append(f"营收{rg:+.0%}")
        if pg is not None and pg <= TH_GROWTH_FLOOR:
            shrink.append(f"净利润{pg:+.0%}")
        if shrink:
            out.signals.append(Signal(
                f"⚠️ 基本面在萎缩（{'、'.join(shrink)}）——'冷得有理由'，疑价值陷阱", -2))
    out.score = sum(s.points for s in out.signals)
    return out


# ---------------- 引擎主入口 ----------------
def scan_member(member: dict, chain: dict) -> dict:
    """
    对单个成员跑全部错配判据，返回结构化结果。
    注意：链热判据（V1）是链级前置——链不热则整条链无错配可谈，由 scan_chain 统一处理。
    返回：{code, name, cold_level, cold_score, signals:[...], details:[...], chain_hot}
    """
    details = []
    total = 0
    for rid in sorted(REGISTRY.keys()):
        out = REGISTRY[rid](member, chain)
        if out.triggered or out.score != 0:
            status = "triggered"
        elif out.note:
            status = "na"
        else:
            status = "clear"
        total += out.score
        details.append({
            "rule_id": out.rule_id, "rule_name": out.rule_name,
            "score": out.score, "status": status,
            "signals": [s.text for s in out.signals], "note": out.note,
        })
    by_rule = {d["rule_id"]: d for d in details}
    # "冷证据"前提：必须先有 V2(估值冷) 或 V3(被漏看) 命中，才谈得上"单体冷"。
    # 否则像龙头那样估值高、涨幅大、只是还在成长(V4正分)的标的，会被误纳为"错配候选"。
    cold_evidence = by_rule.get("V2", {}).get("score", 0) > 0 or \
                    by_rule.get("V3", {}).get("score", 0) > 0

    # 成员自身的"冷"得分：不含链级 V1（V1 衡量的是链，不是这个成员）。
    # 无冷证据时，V4 的正向成长分不单独计入错配分（成长本身不是"错配"，只是护栏）。
    def _counts(d):
        if d["rule_id"] == "V1":
            return False
        if d["rule_id"] == "V4" and not cold_evidence and d["score"] > 0:
            return False  # 无冷证据的成长分不推入候选
        return True
    member_score = sum(d["score"] for d in details if _counts(d))
    signals = [s for d in details if _counts(d) for s in d["signals"]]
    return {
        "code": member.get("code", ""), "name": member.get("name", ""),
        "cold_level": cold_level(max(member_score, 0)),
        "cold_score": member_score,
        "cold_evidence": cold_evidence,
        "signals": signals, "details": details,
    }


def scan_chain(chain: dict, members: List[dict]) -> dict:
    """
    对一条链的所有成员跑错配扫描，排出"链热+单体冷"候选。
    返回：{chain, chain_hot, chain_hot_reason, candidates:[...按错配强度降序], skipped_note}
    """
    # 先判链是否在热（V1）——不热则不谈错配
    hot_out = REGISTRY["V1"]({}, chain)
    chain_hot = hot_out.score >= 1
    hot_reason = [s.text for s in hot_out.signals] or [hot_out.note or "无法判断链温度"]

    scored = [scan_member(m, chain) for m in members]
    # 候选门槛：错配得分≥2（至少弱错配）且未被成长护栏否决为负
    candidates = sorted(
        [s for s in scored if s["cold_score"] >= 2],
        key=lambda s: s["cold_score"], reverse=True)

    return {
        "chain": chain.get("name", ""),
        "chain_hot": chain_hot,
        "chain_hot_reason": hot_reason,
        "candidates": candidates,
        "all_scored": scored,
        "skipped_note": ("" if chain_hot else
                         "⚠️ 这条链的龙头并未明显走热——'链热单体冷'的前提不成立，"
                         "此时的'冷'只是'普通便宜'，不构成错配机会。"),
    }
