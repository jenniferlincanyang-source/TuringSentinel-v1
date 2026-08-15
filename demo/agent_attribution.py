# -*- coding: utf-8 -*-
"""
RiskCopilot · 归属核验 Agent（Attribution Verification）—— 价值发现的"皇冠"

治的是一个真实的认知陷阱（作者亲历的"亨通陷阱"）：
  亨通光电从40涨到100，作者当时讲给自己的故事是"它是被漏看的光通信成员"；
  但亨通主业是光纤光缆+海缆(海风/海底)+电网电缆，而那波冲高的是光模块
  (中际旭创/新易盛，吃AI算力800G/1.6T)——严格说**它俩不在同一条链**。
  那次赚钱大概率是"题材情绪外溢 + 亨通自己的海缆基本面"，不是纯正的"链热单体冷"。
  → 教训：会不会把"赢了"编成"我早看穿了"，取决于能不能分清**真实产业关联 vs 题材联想**。

本 Agent 就用【控辩互搏】把这一步做成可复现的能力（复用 agent_debate.py 的三方骨架）：

  ┌── 控方 Agent：论证"该标的与龙头是**真实产业关联**"——拿披露里的供货/客户关系、
  │        同一可验证的终端需求驱动当证据。
  ├── 辩方 Agent：论证"这只是**题材联想/情绪外溢**"——它主业其实在别处、
  │        需求驱动不同（正是亨通海缆 vs 光模块这种）。
  └── 裁判 Agent：裁定归属为【real(真实关联) / theme_only(仅题材联想) / uncertain(存疑)】，
           只有 real 才放行进入下一步排雷；theme_only 踢出候选。

═══════════════════ 铁律（与 agent_debate 同源，不可动摇）═══════════════════
  1. 硬事实不可辩：披露里白纸黑字的供货/客户/持股关系客观存在，只辩"这算不算同一条链"。
  2. 三方只基于传入的既有证据说话，不得编造新的产业关系。
  3. **默认怀疑**：证据不足以证明真实关联时，裁定 uncertain 或 theme_only，不放行——
     宁可漏掉一个真机会，不可把题材联想当成产业关联送进后续（防幸存者偏差固化成代码）。
  4. 只做归属判定，不预测涨跌、不给买入建议。
  5. 无 LLM / 超时 → 降级为规则版裁定（基于关系证据的硬计数），demo 不崩。

对外只暴露 run_attribution(member, chain, relations)，返回可直接 JSON 化的 dict。

relations dict（该标的与龙头的可查关系证据；来自年报关联方/前五大供应商客户/巨潮公告）：
  supply_links   : [{leader, role, ratio?, year?, source}]  供货/客户关系（role: supplier/customer）
  equity_links   : [{leader, desc, source}]                 股权/合资关系
  shared_demand  : "<该标的与龙头是否共享同一终端需求的描述>"
  own_main_biz   : "<该标的自己的主业（用于辩方论证'主业在别处'）>"
  leader_demand  : "<龙头的需求驱动（用于对比是否同源）>"
"""
from __future__ import annotations

import llm


# ---------------- 证据打包：把关系证据整理成给三方 Agent 看的事实清单 ----------------
def _pack_evidence(member: dict, chain: dict, relations: dict) -> tuple[str, dict]:
    """整理成文本（喂三方 Agent）+ 结构化摘要（供降级与裁判锚定）。"""
    name = member.get("name", member.get("code", "该标的"))
    chain_name = chain.get("name", "该产业链")
    rel = relations or {}

    hard_links = []      # 不可辩的硬关系（披露白纸黑字）
    supply = rel.get("supply_links") or []
    equity = rel.get("equity_links") or []
    for s in supply:
        ratio = f"，占比{s['ratio']:.0%}" if isinstance(s.get("ratio"), (int, float)) else ""
        yr = f"{s['year']}年" if s.get("year") else ""
        hard_links.append(
            f"{yr}与龙头「{s.get('leader','?')}」存在{('供应商' if s.get('role')=='supplier' else '客户')}关系"
            f"{ratio}（来源：{s.get('source','披露')}）")
    for e in equity:
        hard_links.append(f"与龙头「{e.get('leader','?')}」存在股权/合资关系：{e.get('desc','')}"
                          f"（来源：{e.get('source','披露')}）")

    lines = [f"【标的】{name}（{member.get('code','')}）",
             f"【所在链】{chain_name}",
             f"【标的自身主业】{rel.get('own_main_biz','（未提供）')}",
             f"【龙头需求驱动】{rel.get('leader_demand','（未提供）')}",
             f"【是否共享终端需求】{rel.get('shared_demand','（未提供）')}",
             "【与龙头的可查关系（披露硬事实）】："]
    lines += ([f"  · {h}" for h in hard_links] if hard_links
              else ["  · 未在披露中找到与龙头的直接供货/客户/股权关系（重要盲区）"])
    evidence_text = "\n".join(lines)

    summary = {
        "name": name, "chain": chain_name,
        "hard_link_count": len(hard_links),
        "hard_links": hard_links[:8],
        "has_supply_link": bool(supply),
        "has_equity_link": bool(equity),
        "has_shared_demand": bool(rel.get("shared_demand")),
    }
    return evidence_text, summary


# ---------------- 控方 Agent（论证"真实产业关联"）----------------
_PROS_SYS = (
    "你扮演【控方·产业分析师】。目标：论证'该标的与所在链的龙头是**真实的产业关联**'，"
    "即它的业绩会真实受益于这条链的景气，而不仅仅是名字/题材上的联想。\n"
    "要求：①每条论证必须引用给定证据中的具体关系事实（供货/客户/股权/共享终端需求），不得编造；"
    "②若证据里根本没有直接关系，诚实说明只能靠'同终端需求'间接论证，且强度有限；"
    "③按证据强度排序，最多4条。\n"
    "只输出 JSON：{\"arguments\":[{\"point\":\"<真实关联的论据>\","
    "\"evidence\":\"<引用的关系事实>\",\"strength\":\"强|中|弱\"}]}"
)


def _prosecute(evidence: str) -> dict | None:
    return llm.chat(_PROS_SYS, evidence, model=llm.MODEL_SMART,
                    want_json=True, max_tokens=1000, timeout=60)


# ---------------- 辩方 Agent（论证"仅题材联想"——治亨通陷阱的关键角色）----------------
_DEF_SYS = (
    "你扮演【辩方·怀疑者】，专门戳破'题材联想'。目标：论证'该标的与龙头的关联可能只是"
    "**题材/情绪外溢**，而非真实产业关联'——它涨/被期待，可能只是因为顶着同一个板块名词。\n"
    "重点武器（这正是'亨通陷阱'的教训）：①指出该标的**自己的主业其实在别处**、"
    "需求驱动与龙头不同（如龙头吃AI算力、它其实吃海上风电，只是都叫'光XX'）；"
    "②指出披露里缺乏直接供货/客户/股权关系，所谓关联是市场脑补；"
    "③不得编造证据；若某条关联确实扎实、无法归为题材，诚实承认。\n"
    "只输出 JSON：{\"rebuttals\":[{\"target\":\"<针对的控方论据>\","
    "\"argument\":\"<为何这更像题材联想，或承认确属真实关联>\",\"strength\":\"有力|部分|难以反驳\"}]}"
)


def _defend(evidence: str, arguments: list[dict]) -> dict | None:
    at = "\n".join(f"{i+1}. [{a.get('strength','')}] {a.get('point','')}（依据：{a.get('evidence','')}）"
                   for i, a in enumerate(arguments))
    user = f"{evidence}\n\n【控方（主张真实关联）的论据】：\n{at}"
    return llm.chat(_DEF_SYS, user, model=llm.MODEL_SMART,
                    want_json=True, max_tokens=1000, timeout=60)


# ---------------- 裁判 Agent（裁定归属）----------------
_JUDGE_SYS = (
    "你扮演中立的【裁判】。给你控方（主张真实产业关联）、辩方（主张仅题材联想）的交锋和原始证据。"
    "你的职责：裁定该标的与龙头的关联属于哪一类，并给理由。\n"
    "裁定铁律：①**默认怀疑**——只有当披露中存在直接供货/客户/股权关系、或明确共享同一终端需求时，"
    "才可裁定 real(真实关联)；②若关联主要靠板块名词/市场脑补、或该标的主业明显在别处，裁定 theme_only(仅题材联想)；"
    "③证据不足以判断则 uncertain(存疑)；④宁严勿松：把题材联想误判为真实关联的代价，"
    "比漏掉一个机会更大；⑤不预测涨跌、不给买入建议，结尾一句：本判定仅辅助人工尽调，不构成投资建议。\n"
    "只输出 JSON：{\"verdict\":\"real|theme_only|uncertain\","
    "\"confidence\":\"高|中|低\",\"reason\":\"<引用控辩双方观点的裁定理由，100-180字>\","
    "\"key_evidence\":\"<支撑裁定的最关键一条证据或盲区>\"}"
)


def _judge(evidence: str, arguments: list[dict], rebuttals: list[dict]) -> dict | None:
    at = "\n".join(f"A{i+1}. [{a.get('strength','')}] {a.get('point','')}"
                   for i, a in enumerate(arguments))
    rt = "\n".join(f"R{i+1}. 针对「{r.get('target','')}」[{r.get('strength','')}]：{r.get('argument','')}"
                   for i, r in enumerate(rebuttals))
    user = f"{evidence}\n\n【控方·主张真实关联】：\n{at}\n\n【辩方·主张仅题材联想】：\n{rt}"
    return llm.chat(_JUDGE_SYS, user, model=llm.MODEL_SMART,
                    want_json=True, max_tokens=900, timeout=75)


# ---------------- 降级版（无 LLM 时基于关系证据硬计数裁定）----------------
def _attribution_rule(summary: dict) -> dict:
    """规则裁定：有直接供货/客户/股权关系 → real；仅共享需求 → uncertain；啥都没有 → theme_only。"""
    if summary["has_supply_link"] or summary["has_equity_link"]:
        verdict, conf = "real", "中"
        reason = (f"披露中存在 {summary['hard_link_count']} 条与龙头的直接供货/客户/股权关系，"
                  "构成真实产业关联的硬证据（规则裁定，未启用LLM控辩）。")
    elif summary["has_shared_demand"]:
        verdict, conf = "uncertain", "低"
        reason = ("未见直接供货/客户/股权关系，仅共享终端需求——可能真实关联也可能题材联想，"
                  "需人工核实（规则裁定，未启用LLM控辩）。")
    else:
        verdict, conf = "theme_only", "中"
        reason = ("披露中未找到与龙头的任何直接关系，所谓关联缺乏产业事实支撑，"
                  "更像题材联想（规则裁定，未启用LLM控辩）。")
    return {
        "mode": "rule", "arguments": [], "rebuttals": [],
        "verdict": verdict, "confidence": conf, "reason": reason,
        "key_evidence": (summary["hard_links"][0] if summary["hard_links"] else "无直接关系证据（盲区）"),
    }


# ---------------- 对外主入口 ----------------
def run_attribution(member: dict, chain: dict, relations: dict) -> dict:
    """
    执行归属核验控辩互搏。返回：
      {mode, evidence_summary, arguments, rebuttals, verdict, confidence, reason, key_evidence, rounds, pass_to_next}
    mode: 'debate'(LLM三方) / 'rule'(降级)
    pass_to_next: 仅 verdict=='real' 为 True（放行进入排雷环节）
    """
    evidence, summary = _pack_evidence(member, chain, relations)

    if not llm.available():
        out = _attribution_rule(summary)
        out["evidence_summary"] = summary
        out["rounds"] = []
        out["pass_to_next"] = (out["verdict"] == "real")
        return out

    rounds = []
    # ① 控方论证真实关联
    pros = _prosecute(evidence)
    arguments = (pros or {}).get("arguments") or []
    rounds.append({"role": "控方 Agent", "action": "论证真实产业关联",
                   "result": f"提出 {len(arguments)} 条论据" if arguments else "证据不足，未能论证真实关联"})

    # ② 辩方论证题材联想
    deff = _defend(evidence, arguments)
    rebuttals = (deff or {}).get("rebuttals") or []
    rounds.append({"role": "辩方 Agent", "action": "论证仅题材联想（防亨通陷阱）",
                   "result": f"反驳 {len(rebuttals)} 条"
                             + ("（含承认确属真实关联项）" if any(r.get("strength") == "难以反驳"
                                                          for r in rebuttals) else "")})

    # ③ 裁判裁定
    judged = _judge(evidence, arguments, rebuttals)
    if not judged:  # 裁判失败 → 降级但保留控辩过程
        out = _attribution_rule(summary)
        out.update({"mode": "debate", "arguments": arguments, "rebuttals": rebuttals,
                    "evidence_summary": summary, "rounds": rounds})
        out["pass_to_next"] = (out["verdict"] == "real")
        return out

    verdict = judged.get("verdict", "uncertain")
    vlabel = {"real": "真实产业关联 ✅", "theme_only": "仅题材联想 ⚠️", "uncertain": "存疑 ❓"}
    rounds.append({"role": "裁判 Agent", "action": "裁定归属",
                   "result": f"裁定：{vlabel.get(verdict, verdict)}（置信度{judged.get('confidence','')}）"})

    return {
        "mode": "debate",
        "evidence_summary": summary,
        "arguments": arguments,
        "rebuttals": rebuttals,
        "verdict": verdict,
        "confidence": judged.get("confidence", ""),
        "reason": judged.get("reason", ""),
        "key_evidence": judged.get("key_evidence", ""),
        "rounds": rounds,
        "pass_to_next": (verdict == "real"),
    }
