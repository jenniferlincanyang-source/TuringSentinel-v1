# -*- coding: utf-8 -*-
"""
RiskCopilot · 多 Agent 控辩互搏（Adversarial Debate）

复赛级差异化：从"单 Agent 调多工具"升级为**名副其实的多 Agent 协同**——
三个持不同立场的 Agent 就同一套硬证据展开对抗，靠辩论收敛出更可信、更少误报的研判。

  ┌── 控方 Agent（Prosecutor）：站在最审慎的审计视角，尽力论证"这是风险"
  │
  ├── 辩方 Agent（Defender）：为公司辩护，为每条指控找"合理经营解释"
  │        （辩方越强 = 系统越不容易误报，直接强化"不轻易定性"的合规红线）
  │
  └── 裁判 Agent（Judge）：听取控辩双方，逐条裁定「成立 / 被合理解释 / 存疑」，
           给出经过对抗后的最终风险画像 + 每条裁定的理由（可解释、可留痕）

════════════════════════ 项目皇冠·铁律（不可动摇）════════════════════════
  1. 硬信号是 ground truth：确定性信号命中的事实、证监会已认定的违规，**不可辩驳**。
     控辩双方只能就"灰色地带的解释"交锋（如存贷双高是否属正常类金融业务），
     绝不允许把"已发生的事实"辩没。
  2. 三方均只基于传入的既有证据（信号判据 / GNN / 公告红旗）说话，不得编造新事实。
  3. 裁判不得推翻硬信号；它裁定的是"这些事实是否足以构成风险指控"，而非"事实是否存在"。
  4. 结论只做风险提示与建议核查，不给"一定造假"的定性，不替代机构与司法。
  5. 无 LLM / 超时 → 优雅降级为规则版裁定（基于硬信号档位），demo 不崩。

对外只暴露 run_debate(name, rows, gnn, redflags)，返回可直接 JSON 化的 dict。
"""
from __future__ import annotations

import llm


# ---------------- 证据打包：把三源证据整理成给 Agent 看的事实清单 ----------------
def _pack_evidence(name: str, rows: list[dict], gnn: dict | None,
                   redflags: list[dict] | None) -> tuple[str, dict]:
    """把硬证据整理成文本（喂给三方 Agent）+ 结构化摘要（供降级与裁判锚定）。"""
    hard_facts = []          # 不可辩的硬事实
    gray_points = []         # 可辩的灰色解释点

    sig_lines = []
    for r in rows:
        if r.get("hits"):
            sig_lines.append(f"· {r['year']}年【{r['level']}风险，{r['score']}分】："
                             + "；".join(r["hits"]))
            for h in r["hits"]:
                hard_facts.append(f"{r['year']}年 {h}")
    max_score = max((r.get("score", 0) for r in rows), default=0)
    max_level = "无"
    for r in rows:
        if r.get("score", 0) == max_score and max_score > 0:
            max_level = r.get("level", "无")
            break

    gnn_line = "无 GNN 记录（该公司不在关系图谱快照内）"
    if gnn and gnn.get("prob") is not None:
        gnn_line = (f"GNN 舞弊概率 {gnn['prob']:.2f}，风险排名 {gnn.get('rank')}/{gnn.get('total')}"
                    f"，关联 {len(gnn.get('fraud_neighbors', []))} 个已知舞弊邻居")
        if gnn["prob"] >= 0.5:
            hard_facts.append(f"关系图谱结构异常：{gnn_line}")

    rf = redflags or []
    rf_line = "无显著定性红旗"
    if rf:
        cats = sorted({f.get("category", "") for f in rf if f.get("category")})
        rf_line = f"{len(rf)} 条公开公告红旗，类别涵盖：{'、'.join(cats[:6])}"
        # 监管类红旗（立案/处罚/非标）视为硬事实，其余为可讨论线索
        for f in rf[:12]:
            cat = f.get("category", "")
            if cat in ("监管立案", "监管处罚", "非标审计意见"):
                hard_facts.append(f"公告事实：{cat}·{f.get('title', '')[:30]}")
            else:
                gray_points.append(f"{cat}·{f.get('title', '')[:30]}")

    # 灰色地带：存贷双高、高毛利、高应收等"有异常但可能有合理解释"的项
    for r in rows:
        for h in r.get("hits", []):
            if any(k in h for k in ("存贷双高", "毛利率", "应收账款/营收", "商誉", "现金隐含收益率")):
                gray_points.append(f"{r['year']}年 {h}")

    evidence_text = (
        f"【标的】{name}\n"
        f"【确定性财务信号】（规则引擎算出，是硬事实）：\n"
        + ("\n".join(sig_lines) if sig_lines else "各年均未触发显著财务信号") + "\n"
        f"【GNN 关系图谱】：{gnn_line}\n"
        f"【公开公告定性红旗】：{rf_line}"
    )
    summary = {
        "max_score": max_score, "max_level": max_level,
        "hard_facts": hard_facts[:12], "gray_points": gray_points[:10],
        "gnn_high": bool(gnn and gnn.get("prob", 0) >= 0.5),
        "regulatory_hit": any("监管立案" in x or "监管处罚" in x or "非标" in x
                              for x in hard_facts),
    }
    return evidence_text, summary


# ---------------- 控方 Agent ----------------
_PROS_SYS = (
    "你是一名极其审慎的独立审计师，扮演【控方】。以下是某上市公司的已核实公开证据。"
    "你的职责：站在保护投资者的立场，从这些证据中提出最有力的风险指控——"
    "指出哪些迹象单独或交叉印证地指向财务粉饰、资金占用、收入注水或治理缺陷。\n"
    "要求：①每条指控必须引用给定证据中的具体事实，不得编造；"
    "②优先指出'单点看似合规、交叉才见问题'的结构性矛盾（如账上巨额现金却大额举债）；"
    "③按严重度排序，最多 5 条。\n"
    "只输出 JSON：{\"charges\":[{\"point\":\"<指控要点>\",\"evidence\":\"<引用的证据事实>\","
    "\"severity\":\"高|中|低\"}]}"
)


def _prosecute(evidence: str) -> dict | None:
    return llm.chat(_PROS_SYS, evidence, model=llm.MODEL_SMART,
                    want_json=True, max_tokens=1200, timeout=60)


# ---------------- 辩方 Agent ----------------
_DEF_SYS = (
    "你是资深上市公司 CFO 的财务顾问，扮演【辩方】。下面是控方对本公司提出的风险指控，"
    "以及原始证据。你的职责：为公司做合理辩护——对每条指控，指出是否存在正当的经营解释，"
    "或该指控是否夸大。\n"
    "重要边界：①对于'确定性信号命中的事实、监管已认定的违规'，你**不得否认事实本身**，"
    "只能论证其'是否具有合理商业解释'（如存贷双高可能源于真实的集团资金归集或类金融业务）；"
    "②不得编造未在证据中出现的信息为公司开脱；③诚实：若某条指控确实难以辩解，明确承认。\n"
    "只输出 JSON：{\"rebuttals\":[{\"target\":\"<对应的控方指控要点>\","
    "\"defense\":\"<合理解释或承认无法辩解>\",\"strength\":\"有力|部分|难以辩解\"}]}"
)


def _defend(evidence: str, charges: list[dict]) -> dict | None:
    charge_text = "\n".join(
        f"{i+1}. [{c.get('severity','')}] {c.get('point','')}（依据：{c.get('evidence','')}）"
        for i, c in enumerate(charges))
    user = f"{evidence}\n\n【控方指控】：\n{charge_text}"
    return llm.chat(_DEF_SYS, user, model=llm.MODEL_SMART,
                    want_json=True, max_tokens=1200, timeout=60)


# ---------------- 裁判 Agent ----------------
_JUDGE_SYS = (
    "你是中立的财务风险仲裁者，扮演【裁判】。下面给你控方指控、辩方辩护、以及原始证据。"
    "你的职责：逐条裁定每项指控经过辩护后是否仍然成立，并给出经过对抗后的最终风险画像。\n"
    "裁定铁律：①你**不能推翻硬事实**（确定性信号命中的数据、监管已认定的违规客观存在）；"
    "你裁定的是'这些事实经辩方解释后，是否仍构成有效风险指控'；"
    "②对每条指控给出裁定：sustained(成立) / mitigated(被合理解释削弱) / uncertain(存疑需人工核实)；"
    "③综合给出最终风险等级（极高/高/中/低/无）与一段 120-200 字的裁定说明；"
    "④只做风险提示与建议核查，不给'一定造假'的定性；结尾一句：本研判仅辅助，不替代机构与司法决策。\n"
    "只输出 JSON：{\"rulings\":[{\"charge\":\"<指控要点>\",\"verdict\":\"sustained|mitigated|uncertain\","
    "\"reason\":\"<裁定理由，引用控辩双方观点>\"}],\"final_level\":\"极高|高|中|低|无\","
    "\"opinion\":\"<综合裁定说明>\"}"
)


def _judge(evidence: str, charges: list[dict], rebuttals: list[dict]) -> dict | None:
    ct = "\n".join(f"C{i+1}. [{c.get('severity','')}] {c.get('point','')}"
                   for i, c in enumerate(charges))
    dt = "\n".join(f"D{i+1}. 针对「{r.get('target','')}」[{r.get('strength','')}]：{r.get('defense','')}"
                   for i, r in enumerate(rebuttals))
    user = f"{evidence}\n\n【控方指控】：\n{ct}\n\n【辩方辩护】：\n{dt}"
    return llm.chat(_JUDGE_SYS, user, model=llm.MODEL_SMART,
                    want_json=True, max_tokens=1500, timeout=75)


# ---------------- 降级版（无 LLM 时基于硬信号给出确定性裁定）----------------
def _debate_rule(name: str, summary: dict) -> dict:
    lvl = summary["max_level"]
    charges, rulings = [], []
    for f in summary["hard_facts"][:5]:
        charges.append({"point": f, "evidence": f, "severity":
                        "高" if lvl in ("极高", "高") else "中"})
        rulings.append({"charge": f, "verdict": "sustained",
                        "reason": "确定性信号/监管认定的硬事实，客观成立（规则裁定，未启用LLM辩论）"})
    opinion = (f"{name} 经确定性信号裁定为「{lvl}」风险档。"
               + ("存在监管已认定或强结构性矛盾的硬事实，指控成立。" if summary["regulatory_hit"] or lvl in ("极高", "高")
                  else "未见显著硬信号，风险可控。")
               + "（当前为规则降级裁定，未启用控辩 LLM 互搏）本研判仅辅助，不替代机构与司法决策。")
    return {
        "mode": "rule", "charges": charges, "rebuttals": [], "rulings": rulings,
        "final_level": lvl, "opinion": opinion,
    }


# ---------------- 对外主入口 ----------------
def run_debate(name: str, rows: list[dict], gnn: dict | None,
               redflags: list[dict] | None = None) -> dict:
    """
    执行控辩裁三角互搏。返回：
      {mode, evidence_summary, charges, rebuttals, rulings, final_level, opinion, rounds}
    mode: 'debate'(LLM三方互搏) / 'rule'(降级)。
    """
    evidence, summary = _pack_evidence(name, rows, gnn, redflags)

    if not llm.available():
        out = _debate_rule(name, summary)
        out["evidence_summary"] = summary
        out["rounds"] = []
        return out

    rounds = []
    # 第①轮：控方指控
    pros = _prosecute(evidence)
    charges = (pros or {}).get("charges") or []
    rounds.append({"role": "控方 Agent", "action": "提出风险指控",
                   "result": f"提出 {len(charges)} 条指控" if charges else "未形成有效指控"})
    if not charges:  # 控方都挑不出问题 → 直接清白裁定
        out = _debate_rule(name, summary)
        out["mode"] = "debate"
        out["opinion"] = (f"控方 Agent 基于现有公开证据未能形成有效风险指控，{name} 风险可控。"
                          "本研判仅辅助，不替代机构与司法决策。")
        out["evidence_summary"] = summary
        out["rounds"] = rounds
        out["charges"] = []
        return out

    # 第②轮：辩方辩护
    deff = _defend(evidence, charges)
    rebuttals = (deff or {}).get("rebuttals") or []
    rounds.append({"role": "辩方 Agent", "action": "逐条辩护/给合理解释",
                   "result": f"回应 {len(rebuttals)} 条指控"
                             + ("（含承认难以辩解项）" if any(r.get("strength") == "难以辩解"
                                                        for r in rebuttals) else "")})

    # 第③轮：裁判裁定
    judged = _judge(evidence, charges, rebuttals)
    if not judged:  # 裁判失败 → 降级但保留控辩过程
        out = _debate_rule(name, summary)
        out.update({"mode": "debate", "charges": charges, "rebuttals": rebuttals,
                    "evidence_summary": summary, "rounds": rounds})
        return out

    rulings = judged.get("rulings") or []
    sustained = sum(1 for r in rulings if r.get("verdict") == "sustained")
    mitigated = sum(1 for r in rulings if r.get("verdict") == "mitigated")
    rounds.append({"role": "裁判 Agent", "action": "对抗后逐条裁定",
                   "result": f"{sustained} 条成立、{mitigated} 条被合理解释削弱，"
                             f"最终判定「{judged.get('final_level', summary['max_level'])}」风险"})

    # 硬约束：裁判给出的 final_level 不得低于硬信号已确证的档位（防止被"辩没"）
    order = {"无": 0, "低": 1, "中": 2, "高": 3, "极高": 4}
    judge_lvl = judged.get("final_level", summary["max_level"])
    if summary["regulatory_hit"] or summary["max_level"] in ("极高", "高"):
        # 有监管认定/硬信号高档，最终档不得低于硬信号档
        if order.get(judge_lvl, 0) < order.get(summary["max_level"], 0):
            judge_lvl = summary["max_level"]

    return {
        "mode": "debate",
        "evidence_summary": summary,
        "charges": charges,
        "rebuttals": rebuttals,
        "rulings": rulings,
        "final_level": judge_lvl,
        "opinion": judged.get("opinion", ""),
        "rounds": rounds,
    }
