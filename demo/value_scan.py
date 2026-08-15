# -*- coding: utf-8 -*-
"""
RiskCopilot · 价值发现流水线（端到端编排）—— "价值发现正脸"的总控

把一次真实的赚钱直觉，做成可复现、可审计、不越权的四步闭环：

  ① 错配扫描（value_engine）：这条链里，谁"链热+单体冷"？          [确定性规则]
  ② 归属核验（agent_attribution）：它和龙头是真关联还是题材联想？  [控辩互搏]★
  ③ 兜底排雷（RiskCopilot 双引擎）：它便宜是被漏看还是有雷？       [复用反舞弊]
  ④ 出清单：带证据链 + 已知盲区的"值得深看候选"，不是买入建议     [诚实交付]

★ 舞弊识别在这里的角色 = 价值发现的"排雷/验真"兜底环节（③），
  不是招牌，是地基——没有它，价值发现就是接飞刀。

一键复现（像 signals/run.py）：
  python3 value_scan.py                    # 用样例数据跑（无需网络/LLM）
  python3 value_scan.py --no-attr          # 跳过归属核验（无LLM时更快看骨架）

铁律：不预测涨跌、不给买入建议、不给估值数字；机器发现错配，人判断机会还是陷阱。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import value_engine

HERE = Path(__file__).resolve().parent
SAMPLE = HERE / "data" / "value_sample.json"


# ---------------- ③ 兜底排雷：复用 RiskCopilot 双引擎（有真实财报才跑，样例下降级为占位） ----------------
def _derisk(code: str, member: dict) -> dict:
    """
    对通过归属核验的候选跑排雷。真实模式：调 backend 的信号+GNN 判'冷得干净还是有雷'。
    样例模式（无网络/财报）：用成长护栏结果做轻量占位，并标注"需接真实财报做完整排雷"。
    返回：{clean, level, basis, mode}
    """
    # 真实模式：尝试用 signals 引擎（需要真实财报字段，样例里没有 → 优雅降级）
    # 这里保持与 backend.py 一致的"失败降级不阻断"精神。
    rg = member.get("rev_growth")
    pg = member.get("profit_growth")
    shrinking = (isinstance(rg, (int, float)) and rg <= 0) or \
                (isinstance(pg, (int, float)) and pg <= 0)
    if shrinking:
        return {"clean": False, "level": "警惕", "mode": "sample",
                "basis": ["基本面在萎缩，'便宜'可能有基本面理由（样例占位；需接真实财报+7信号+GNN做完整排雷）"]}
    return {"clean": True, "level": "未见明显雷", "mode": "sample",
            "basis": ["样例数据未见财务红旗占位——真实模式下这里会跑 RiskCopilot 7类信号+GNN 完整排雷"]}


# ---------------- ② 归属核验：控辩互搏（无 LLM 自动降级为规则版） ----------------
def _verify_attribution(member: dict, chain: dict, run_attr: bool) -> dict:
    if not run_attr:
        return {"verdict": "skipped", "reason": "已用 --no-attr 跳过归属核验", "pass_to_next": True,
                "mode": "skipped", "rounds": []}
    import agent_attribution
    return agent_attribution.run_attribution(member, chain, member.get("relations") or {})


# ---------------- 主流水线 ----------------
def run_pipeline(chain: dict, members: list[dict], run_attr: bool = True) -> dict:
    # ① 错配扫描
    scan = value_engine.scan_chain(chain, members)

    results = []
    for cand in scan["candidates"]:
        code = cand["code"]
        member = next((m for m in members if m.get("code") == code), {})
        # ② 归属核验
        attr = _verify_attribution(member, chain, run_attr)
        entry = {**cand, "attribution": attr}
        # ③ 排雷：只对'归属为真'的候选做（题材联想的直接踢出，不浪费排雷算力——天然分级）
        if attr.get("pass_to_next"):
            entry["derisk"] = _derisk(code, member)
            # ④ 最终归类
            if entry["derisk"]["clean"]:
                entry["final"] = "candidate"     # 值得人工深看
            else:
                entry["final"] = "trap_suspect"  # 冷得有理由，疑价值陷阱
        else:
            entry["derisk"] = None
            entry["final"] = "theme_rejected"    # 仅题材联想，不放行
        results.append(entry)

    return {"chain": scan["chain"], "chain_hot": scan["chain_hot"],
            "chain_hot_reason": scan["chain_hot_reason"], "skipped_note": scan["skipped_note"],
            "results": results, "all_scored": scan["all_scored"]}


# ---------------- 命令行报告（可复现、可读、可审计） ----------------
_FINAL_BADGE = {
    "candidate": "✅ 值得深看的候选",
    "trap_suspect": "🟠 疑价值陷阱（冷得有理由）",
    "theme_rejected": "⚠️ 仅题材联想，已排除",
}
_VERDICT_LABEL = {"real": "真实产业关联 ✅", "theme_only": "仅题材联想 ⚠️",
                  "uncertain": "存疑 ❓", "skipped": "（已跳过）"}


def print_report(out: dict) -> None:
    print("=" * 82)
    print(f"RiskCopilot · 价值发现  |  链：{out['chain']}")
    print("=" * 82)
    print("① 链温度：" + ("🔥 在热" if out["chain_hot"] else "❄️ 不热"))
    for r in out["chain_hot_reason"]:
        print(f"     · {r}")
    if out["skipped_note"]:
        print(out["skipped_note"])
    print()

    if not out["results"]:
        print("本链未扫出'链热单体冷'错配候选（错配得分未达门槛）。")
    for i, e in enumerate(out["results"], 1):
        print(f"—— 候选 {i}：{e['name']}（{e['code']}）"
              f" · 错配强度 {value_engine.BADGE.get(e['cold_level'], e['cold_level'])}"
              f"（{e['cold_score']}分）——————————")
        print("  ① 错配信号（为什么说它'链热单体冷'）：")
        for s in e["signals"]:
            print(f"      · {s}")

        attr = e["attribution"]
        print(f"  ② 归属核验（控辩互搏 · {attr.get('mode','')}）："
              f"{_VERDICT_LABEL.get(attr.get('verdict'), attr.get('verdict'))}"
              f"  置信度{attr.get('confidence','')}")
        for rd in attr.get("rounds", []):
            print(f"      → {rd['role']}：{rd['action']} —— {rd['result']}")
        if attr.get("reason"):
            print(f"      裁定理由：{attr['reason']}")

        if e.get("derisk"):
            d = e["derisk"]
            print(f"  ③ 兜底排雷（价值发现的验真环节）：{d['level']}")
            for b in d["basis"]:
                print(f"      · {b}")
        else:
            print("  ③ 兜底排雷：未执行（归属未通过，题材联想候选直接排除，不浪费排雷算力）")

        print(f"  ④ 结论：{_FINAL_BADGE.get(e['final'], e['final'])}")
        print()

    # 验算题复盘
    print("-" * 82)
    print("【验算题复盘】亨通光电(600487)：")
    hty = next((e for e in out["results"] if e["code"] == "600487"), None)
    if hty:
        v = hty["attribution"].get("verdict")
        if v in ("theme_only", "uncertain"):
            print("  ✅ 归属核验把它识别为'非真实产业关联'——系统成功避开了'亨通陷阱'：")
            print("     它错配信号确实亮（估值冷、被漏看），但主业是海缆/光纤，与光模块(AI算力)不同源，")
            print("     若只看'链热单体冷'会误纳，控辩核验这一步把'运气'和'能力'分开了。")
        else:
            print(f"  ⚠️ 归属被裁定为 {v}——需复核证据，警惕把题材联想当成真实关联。")
    else:
        print("  （亨通未进入错配候选，或数据缺失）")
    print("-" * 82)
    print("说明：错配扫描为确定性规则、可复现；归属核验为控辩互搏（无LLM降级为规则）；")
    print("      本系统输出为'值得人工深看的候选 + 证据链'，不预测涨跌、不构成投资建议。")
    print("      机器发现错配，人判断机会还是陷阱。")
    print("-" * 82)


def main():
    ap = argparse.ArgumentParser(description="价值发现流水线（链热单体冷错配扫描）")
    ap.add_argument("--data", default=str(SAMPLE), help="链+成员数据 JSON（默认样例）")
    ap.add_argument("--no-attr", action="store_true", help="跳过归属核验（无LLM时快速看骨架）")
    ap.add_argument("--json", action="store_true", help="输出 JSON（供前端/接口）")
    args = ap.parse_args()

    with open(args.data, encoding="utf-8") as f:
        data = json.load(f)
    chain = data["chain"]
    members = data["members"]

    out = run_pipeline(chain, members, run_attr=not args.no_attr)
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        if "_meta" in data and data["_meta"].get("honesty_note"):
            print("ℹ️  " + data["_meta"]["honesty_note"] + "\n")
        print_report(out)


if __name__ == "__main__":
    main()
