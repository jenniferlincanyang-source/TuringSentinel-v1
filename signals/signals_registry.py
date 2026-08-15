# -*- coding: utf-8 -*-
"""
图灵哨兵 · 信号定义注册表

每条信号都长在 03-舞弊识别方法论.md 的"四类矛盾武器"上，且只用【公开年报可算】的科目。
新增信号：在此文件下面照葫芦画瓢再写一个 @register("信号号") 的函数即可，引擎自动纳入。

字段约定（fiscal_years[year] 里的 key，单位:元）：
  monetary_funds, total_assets, equity_parent,
  interest_bearing_debt{total}, interest_income, interest_expense,
  revenue, operating_profit, operating_cashflow, accounts_receivable,
  cost_of_sales, other_receivables, related_party_occupation
缺失的科目允许为 None/缺省，信号内部自行判断能否评估。
"""
from __future__ import annotations

from typing import Optional

from engine import Hit, SignalOutput, register, YI

# ---------------- 可配置阈值（均有方法论出处，非拍脑袋）----------------
# 信号001 货币资金
TH_MONEY_TO_ASSETS = 0.40   # 货币资金/总资产 畸高
TH_MONEY_TO_EQUITY = 0.70   # 货币资金/净资产 异常集中
TH_CASH_YIELD = 0.015       # 现金隐含收益率下限（市场存款利率参考1.5%）
TH_DEBT_MATERIAL = 0.05     # 有息负债/总资产 视为"有实质举债"
# 信号002 收入质量
TH_REV_GROWTH = 0.20        # 营收增速阈值（暴增）
TH_AR_OVER_REV_GROWTH = 1.5 # 应收增速 / 营收增速 背离倍数
TH_AR_MATERIAL = 0.05       # 应收/营收 需达此下限，增速背离才有意义（滤掉先款后货的低应收公司）
TH_OCF_TO_PROFIT = 0.30     # 经营现金流/营业利润 过低（利润没转成现金）
# 信号003 利润质量
TH_GROSS_MARGIN_EXCESS = 0.15  # 毛利率超同行均值的绝对差
TH_OCF_NEGATIVE_YEARS = 1      # 经营现金流为负


def _g(d: dict, key: str):
    """安全取数，缺失返回 None。"""
    v = d.get(key)
    return v if isinstance(v, (int, float)) else None


# ============================================================
# 信号 #001：货币资金真实性存疑（资金流核对 / 结构关系）
# 方法论 A 类：账上有钱却大额举债、现金不生息、大股东占用
# ============================================================
@register("001")
def signal_monetary_funds(d: dict, prev: Optional[dict], peer: Optional[dict]) -> SignalOutput:
    out = SignalOutput("001", "货币资金真实性存疑")
    money = _g(d, "monetary_funds")
    assets = _g(d, "total_assets")
    equity = _g(d, "equity_parent")
    debt = (d.get("interest_bearing_debt") or {}).get("total")
    int_income = _g(d, "interest_income") or 0
    rp = _g(d, "related_party_occupation")

    if money is None or assets is None or equity is None:
        out.note = "缺货币资金/总资产/净资产"
        return out

    m2a = money / assets
    m2e = money / equity
    yield_ = int_income / money if money else 0
    debt_material = (debt is not None and assets and debt / assets >= TH_DEBT_MATERIAL)

    if m2a >= TH_MONEY_TO_ASSETS:
        out.hits.append(Hit(f"货币资金/总资产={m2a:.1%}≥{TH_MONEY_TO_ASSETS:.0%}（畸高）", 1))
        if debt_material:
            out.hits.append(Hit(
                f"存贷双高：账上货币资金{money/YI:.1f}亿 却背有息负债{debt/YI:.1f}亿", 1))
    if m2e >= TH_MONEY_TO_EQUITY:
        tag = "，已超净资产本身(极端)" if m2e >= 1.0 else ""
        out.hits.append(Hit(f"货币资金/净资产={m2e:.1%}≥{TH_MONEY_TO_EQUITY:.0%}{tag}",
                            2 if m2e >= 1.0 else 1))
    if yield_ < TH_CASH_YIELD:
        # 存贷双高时"现金不生息"是最硬红旗，加重权重
        pts = 3 if m2a >= TH_MONEY_TO_ASSETS else 2
        out.hits.append(Hit(
            f"现金隐含收益率={yield_:.2%}<{TH_CASH_YIELD:.1%}"
            f"（{money/YI:.0f}亿只产生{int_income/YI:.2f}亿利息，钱'不生息'）", pts))
    if rp and rp > 0:
        out.hits.append(Hit(f"关联方非经营性占用 {rp/YI:.1f}亿", 2))

    out.score = sum(h.points for h in out.hits)
    return out


# ============================================================
# 信号 #002：收入质量存疑（收入类 B）
# 方法论 B 类：收入暴增但现金/物流不匹配、应收虚高
# 用公开财报替代物流单：营收暴增 + 经营现金流背离 + 应收增速远超营收
# ============================================================
@register("002")
def signal_revenue_quality(d: dict, prev: Optional[dict], peer: Optional[dict]) -> SignalOutput:
    out = SignalOutput("002", "收入质量存疑")
    rev = _g(d, "revenue")
    op = _g(d, "operating_profit")
    ocf = _g(d, "operating_cashflow")
    ar = _g(d, "accounts_receivable")

    if rev is None or prev is None:
        out.note = "缺营收或上年数据(无法算增速)"
        return out
    prev_rev = _g(prev, "revenue")
    if not prev_rev:
        out.note = "缺上年营收"
        return out

    rev_growth = (rev - prev_rev) / prev_rev

    # ① 营收暴增
    surged = rev_growth >= TH_REV_GROWTH
    if surged:
        out.hits.append(Hit(f"营收同比+{rev_growth:.1%}（暴增，≥{TH_REV_GROWTH:.0%}）", 1))

    # ② 应收账款增速远超营收增速（收入挂账未收现）
    # 前置门槛：应收本身要占营收有实质比重(≥5%)才有意义——否则像茅台这种先款后货、
    # 应收/营收仅0.04%的公司，"从几乎为0涨到还是几乎为0"会被增速倍数误判为风险。
    prev_ar = _g(prev, "accounts_receivable")
    if ar is not None and prev_ar and rev and ar / rev >= TH_AR_MATERIAL:
        ar_growth = (ar - prev_ar) / prev_ar
        if rev_growth > 0 and ar_growth >= TH_AR_OVER_REV_GROWTH * rev_growth and ar_growth > 0:
            out.hits.append(Hit(
                f"应收账款增速{ar_growth:.1%} 远超营收增速{rev_growth:.1%}"
                f"（≥{TH_AR_OVER_REV_GROWTH}倍，收入挂账未收现）", 2))

    # ③ 经营现金流与利润背离（利润没变成现金）
    if ocf is not None and op is not None and op > 0:
        ocf_ratio = ocf / op
        if ocf < 0:
            out.hits.append(Hit(
                f"营业利润{op/YI:.1f}亿 但经营现金流为负({ocf/YI:.1f}亿)，利润无现金支撑", 2))
        elif ocf_ratio < TH_OCF_TO_PROFIT:
            out.hits.append(Hit(
                f"经营现金流/营业利润={ocf_ratio:.1%}<{TH_OCF_TO_PROFIT:.0%}（利润未转成现金）", 1))

    out.score = sum(h.points for h in out.hits)
    return out


# ============================================================
# 信号 #003：利润质量存疑（存货/成本类 C）
# 方法论 C 类：毛利率显著高于同行 + 经营现金流常年为负 → 利润含金量存疑
# ============================================================
@register("003")
def signal_profit_quality(d: dict, prev: Optional[dict], peer: Optional[dict]) -> SignalOutput:
    out = SignalOutput("003", "利润质量存疑")
    rev = _g(d, "revenue")
    cost = _g(d, "cost_of_sales")
    ocf = _g(d, "operating_cashflow")

    if rev is None or cost is None:
        out.note = "缺营收或营业成本(无法算毛利率)"
        return out

    gross_margin = (rev - cost) / rev if rev else 0
    peer_gm = (peer or {}).get("gross_margin_industry_avg")

    if peer_gm is not None and gross_margin - peer_gm >= TH_GROSS_MARGIN_EXCESS:
        out.hits.append(Hit(
            f"毛利率{gross_margin:.1%} 显著高于同行均值{peer_gm:.1%}"
            f"（高{gross_margin-peer_gm:.1%}，≥{TH_GROSS_MARGIN_EXCESS:.0%}）", 2))

    if ocf is not None and ocf < 0:
        out.hits.append(Hit(f"经营活动现金流为负({ocf/YI:.1f}亿)，高毛利未带来现金", 2))

    out.score = sum(h.points for h in out.hits)
    return out


# ============================================================
# 信号 #004：存货异常（存货/成本类 C · 物理约束反推）
# 方法论 C 类：存货虚增——存货占比畸高 + 存货增速远超营收（塞不进的货）
# 字段：inventory, revenue, total_assets, prev.inventory, prev.revenue
# ============================================================
TH_INV_TO_ASSETS = 0.30      # 存货/总资产 畸高
TH_INV_OVER_REV_GROWTH = 1.5 # 存货增速/营收增速 背离倍数


@register("004")
def signal_inventory(d: dict, prev: Optional[dict], peer: Optional[dict]) -> SignalOutput:
    out = SignalOutput("004", "存货真实性存疑")
    inv = _g(d, "inventory")
    assets = _g(d, "total_assets")
    rev = _g(d, "revenue")
    if inv is None or assets is None:
        out.note = "缺存货或总资产"
        return out

    if assets > 0 and inv / assets >= TH_INV_TO_ASSETS:
        out.hits.append(Hit(f"存货/总资产={inv/assets:.1%}≥{TH_INV_TO_ASSETS:.0%}（存货占比畸高）", 1))

    # 存货增速远超营收增速（生产/销售不匹配，疑似虚增存货）
    if prev:
        prev_inv, prev_rev = _g(prev, "inventory"), _g(prev, "revenue")
        if prev_inv and prev_rev and rev:
            inv_g = (inv - prev_inv) / prev_inv
            rev_g = (rev - prev_rev) / prev_rev
            if inv_g > 0 and rev_g > 0 and inv_g >= TH_INV_OVER_REV_GROWTH * rev_g:
                out.hits.append(Hit(
                    f"存货增速{inv_g:.1%} 远超营收增速{rev_g:.1%}"
                    f"（≥{TH_INV_OVER_REV_GROWTH}倍，产销不匹配，疑虚增存货）", 2))
    out.score = sum(h.points for h in out.hits)
    return out


# ============================================================
# 信号 #005：其他应收款异常（资金类 A · 大股东占用藏身处）
# 方法论 A 类：其他应收款畸高 = 关联方非经营性占用（康美母公司78亿即此）
# 字段：other_receivables, total_assets, equity_parent
# ============================================================
TH_OTHREC_TO_ASSETS = 0.10   # 其他应收款/总资产 畸高（正常经营占比很小）
TH_OTHREC_TO_EQUITY = 0.20   # 其他应收款/净资产


@register("005")
def signal_other_receivables(d: dict, prev: Optional[dict], peer: Optional[dict]) -> SignalOutput:
    out = SignalOutput("005", "其他应收款异常（资金占用）")
    oth = _g(d, "other_receivables")
    assets = _g(d, "total_assets")
    equity = _g(d, "equity_parent")
    if oth is None or assets is None:
        out.note = "缺其他应收款或总资产"
        return out

    if assets > 0 and oth / assets >= TH_OTHREC_TO_ASSETS:
        out.hits.append(Hit(
            f"其他应收款/总资产={oth/assets:.1%}≥{TH_OTHREC_TO_ASSETS:.0%}"
            f"（其他应收款{oth/YI:.1f}亿畸高，警惕关联方/大股东非经营性占用）", 2))
    if equity and equity > 0 and oth / equity >= TH_OTHREC_TO_EQUITY:
        out.hits.append(Hit(f"其他应收款/净资产={oth/equity:.1%}≥{TH_OTHREC_TO_EQUITY:.0%}", 1))
    out.score = sum(h.points for h in out.hits)
    return out


# ============================================================
# 信号 #006：商誉减值风险（结构类 · 并购爆雷前兆）
# 商誉占净资产畸高——高溢价并购堆积，一旦标的不达标则巨额减值爆雷（康得新等）
# 字段：goodwill, equity_parent, total_assets
# ============================================================
TH_GOODWILL_TO_EQUITY = 0.30  # 商誉/净资产 畸高


@register("006")
def signal_goodwill(d: dict, prev: Optional[dict], peer: Optional[dict]) -> SignalOutput:
    out = SignalOutput("006", "商誉减值风险")
    gw = _g(d, "goodwill")
    equity = _g(d, "equity_parent")
    if gw is None or equity is None or gw <= 0:
        out.note = "无商誉或缺净资产（无并购溢价，不适用）"
        return out

    if equity > 0 and gw / equity >= TH_GOODWILL_TO_EQUITY:
        out.hits.append(Hit(
            f"商誉/净资产={gw/equity:.1%}≥{TH_GOODWILL_TO_EQUITY:.0%}"
            f"（商誉{gw/YI:.1f}亿，高溢价并购堆积，警惕业绩不达标时巨额减值爆雷）", 2))
    out.score = sum(h.points for h in out.hits)
    return out


# ============================================================
# 信号 #007：应收账款激进（收入类 B 补强 · 收入注水）
# 应收账款/营收畸高——收入靠赊销堆出、回款存疑
# 字段：accounts_receivable, revenue
# ============================================================
TH_AR_TO_REV = 0.40  # 应收账款/营收 畸高


@register("007")
def signal_accounts_receivable(d: dict, prev: Optional[dict], peer: Optional[dict]) -> SignalOutput:
    out = SignalOutput("007", "应收账款激进（收入注水）")
    ar = _g(d, "accounts_receivable")
    rev = _g(d, "revenue")
    if ar is None or rev is None or rev <= 0:
        out.note = "缺应收账款或营收"
        return out

    if ar / rev >= TH_AR_TO_REV:
        out.hits.append(Hit(
            f"应收账款/营收={ar/rev:.1%}≥{TH_AR_TO_REV:.0%}"
            f"（应收{ar/YI:.1f}亿占营收畸高，收入靠赊销堆出、回款存疑）", 2))
    out.score = sum(h.points for h in out.hits)
    return out


# ---- 新增信号从这里往下加：@register("008") def signal_xxx(...) ----
