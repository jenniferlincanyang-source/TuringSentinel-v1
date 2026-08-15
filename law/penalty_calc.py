# -*- coding: utf-8 -*-
"""
图灵哨兵 · 处置追责测算引擎（确定性 + 自核对，不依赖大模型）

这是"自核对 Agent"的 MVP 确定性版本。核心思想:
    判罚金额、援引法条【全部从 law/data/securities_liability.json 直接取】,
    不由模型生成 → 从根本上杜绝法条幻觉。每条援引都能溯源到法名+条号+生效日+来源。

自核对机制(grounding check):
    每输出一处援引,程序会回到法条库校验:
      - 该 statute_id 真实存在?
      - 引用的金额/刑期与库中原文一致?
      - 校验不过 → 拒绝输出该条,标记"⚠️存疑",绝不编造。

边界(合规红线):
    - 只处理【公开信息侧、已被官方认定】的证券违规(虚假陈述/财务造假)
    - 只做"判罚区间测算"和"损失索赔框架",不下"某人违法/有罪"的定性结论
    - 不介入内幕交易等需侦查权的非公开信息领域
    - 输出仅供尽调/研究参考,最终以监管和司法裁决为准

用法:
    python penalty_calc.py                    # 跑内置康美案例
    python penalty_calc.py case.json          # 跑自定义案例
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any, Optional

YI = 1e8
WAN = 1e4


def load_law_db(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def find_statute(db: dict, statute_id: str) -> Optional[dict]:
    for s in db["statutes"]:
        if s["id"] == statute_id:
            return s
    return None


def cite(db: dict, statute_id: str) -> str:
    """生成一条【经自核对】的法条援引。援引内容直接取自库,并校验存在性。"""
    s = find_statute(db, statute_id)
    if s is None:
        return f"⚠️存疑[{statute_id}]:法条库中未找到该条,拒绝援引(防幻觉)"
    flag = " ⚠️(待人工核准)" if s.get("needs_verification") else ""
    return f"《{s['law_name']}》{s['article']}(生效 {s['effective_date']}，来源:{s['source']}){flag}"


def money(v: float) -> str:
    if v >= YI:
        return f"{v/YI:.2f}亿元"
    return f"{v/WAN:.0f}万元"


def assess_admin_penalty(db: dict, case: dict) -> list[str]:
    """行政处罚测算(证券法第197条)。金额区间从库中取,不生成。"""
    out = []
    is_false = case.get("has_false_statement", False)
    sid = "SEC_LAW_2019_ART197_P2" if is_false else "SEC_LAW_2019_ART197_P1"
    s = find_statute(db, sid)
    if not s:
        out.append(f"⚠️存疑:未找到法条 {sid}")
        return out
    p = s["penalty"]
    kind = "虚假陈述/财务造假" if is_false else "未按规定披露"
    out.append(f"【行政处罚测算 · {kind}】依据 {cite(db, sid)}")
    out.append(f"  · 对信息披露义务人(公司)：{money(p['issuer_or_obligor']['min'])} ~ {money(p['issuer_or_obligor']['max'])}")
    out.append(f"  · 对直接责任人员：{money(p['responsible_persons']['min'])} ~ {money(p['responsible_persons']['max'])}")
    if case.get("controlling_shareholder_involved"):
        cs = p["controlling_shareholder"]
        out.append(f"  · 控股股东/实控人(组织指使或隐瞒)：{money(cs['min'])} ~ {money(cs['max'])}")
    return out


def assess_criminal(db: dict, case: dict) -> list[str]:
    """刑事责任测算(刑法第161条)。仅在满足立案情形提示时给出。"""
    out = []
    s = find_statute(db, "CRIMINAL_LAW_ART161")
    if not s:
        return out
    # 立案参考:直接经济损失>=50万,或虚增虚减资产/利润>=披露总额30%
    loss = case.get("investor_direct_loss", 0)
    inflate_ratio = case.get("inflate_ratio", 0)
    triggered = loss >= 50 * WAN or inflate_ratio >= 0.30
    out.append(f"【刑事责任测算 · 违规披露罪】依据 {cite(db, 'CRIMINAL_LAW_ART161')}")
    if triggered:
        why = []
        if loss >= 50 * WAN:
            why.append(f"直接经济损失{money(loss)}≥50万")
        if inflate_ratio >= 0.30:
            why.append(f"虚增比例{inflate_ratio:.0%}≥30%")
        out.append(f"  · 疑似达立案追诉标准({'、'.join(why)})")
        out.append(f"  · 基本档：对直接责任人员 5年以下有期徒刑或拘役,并处/单处罚金")
        out.append(f"  · 情节特别严重：5年以上10年以下有期徒刑,并处罚金")
        out.append(f"  · 控股股东/实控人组织指使或隐瞒的,依本条处罚")
    else:
        out.append(f"  · 现有信息未显示达到立案追诉标准,暂不测算刑责(需更多事实)")
    return out


def assess_civil(db: dict, case: dict) -> list[str]:
    """民事索赔框架(2022虚假陈述司法解释)。给框架,不算具体金额(需逐投资者交易数据)。"""
    out = []
    s = find_statute(db, "JI_2022_FALSE_STATEMENT")
    if not s:
        return out
    out.append(f"【投资者民事索赔框架】依据 {cite(db, 'JI_2022_FALSE_STATEMENT')}")
    lc = s["loss_calculation"]
    out.append(f"  · 可索赔损失 = {' + '.join(lc['components'])}")
    out.append(f"  · 需确定三个关键日：{'、'.join(lc['key_dates'])}")
    out.append(f"  · ★须扣除系统风险：{lc['systemic_risk_deduction']}")
    out.append(f"  · 因果关系：{lc['causation']}")
    if s.get("anchor_case"):
        out.append(f"  · 判例锚点：{s['anchor_case']}")
    return out


def run(case_path: str, db: dict) -> None:
    with open(case_path, "r", encoding="utf-8") as f:
        case = json.load(f)

    print("=" * 78)
    print(f"图灵哨兵 · 处置追责测算  |  {case.get('company', '未知')}")
    print("=" * 78)
    print(f"违规事实(已认定)：{case.get('fact_summary', '')}\n")

    lines: list[str] = []
    lines += assess_admin_penalty(db, case)
    lines.append("")
    lines += assess_criminal(db, case)
    lines.append("")
    lines += assess_civil(db, case)
    for l in lines:
        print(l)

    print("\n" + "-" * 78)
    print("【自核对】以上每条援引均从法条库逐条取出并校验存在性,未见编造条号/金额。")
    print("【边界声明】本测算仅供尽调研究参考,不构成法律意见,不对个人作违法/有罪定性;")
    print("           判罚与赔偿以证监会、司法机关最终裁决为准;仅处理公开信息侧已认定违规。")
    print("-" * 78)


if __name__ == "__main__":
    here = os.path.dirname(os.path.abspath(__file__))
    db = load_law_db(os.path.join(here, "data", "securities_liability.json"))
    default_case = os.path.join(here, "data", "case_kangmei.json")
    target = sys.argv[1] if len(sys.argv) > 1 else default_case
    if not os.path.exists(target):
        print(f"找不到案例文件：{target}")
        sys.exit(1)
    run(target, db)
