# -*- coding: utf-8 -*-
"""
RiskCopilot · 总控编排 Agent（Orchestrator）——所有能力/Agent 的"项目组长"

它站在最上层，把散落的能力（意图理解 / 信号引擎 / 深度研判 Agent / 控辩互搏 Agent / 追责引擎）
统筹成一次完整的尽调项目。核心不是"把所有 Agent 都跑一遍"（浪费且越权），而是像
【审计项目组长】一样按风险分级、自主升级：

  意图理解 → 对每个标的：
    ① 粗筛（信号+GNN）——永远先做，成本最低
    ② 据风险局面决定升级到哪一层：
         · 清白        → 出低风险简报，止步（不浪费算力、不越权深挖）
         · 有信号亮起  → 升级【深度研判 Agent】（读公告 + LLM 推理）
         · 高风险      → 再升级【控辩互搏 Agent】（控/辩/裁三方对抗）
         · 裁定成立且达追责档 → 标记【追责测算】（law/ 引擎）
  → 汇总统一结论 + 完整"决策轨迹"（我为什么对 A 止步、对 B 一路查到追责）

★决策轨迹是本模块的灵魂：同一套代码，清白公司止步粗筛、高危公司一路升级，
  不同输入 → 不同编排路径，可解释、可审计——这就是"自主编排"的实证（对齐手册 §8.2.3）。

铁律：
  - 硬信号是 ground truth，编排只决定"查多深"，不推翻事实；
  - 分级升级 = 天然的"不越权"（低风险不深挖、不谈追责）；
  - 依赖注入设计（resolve/deepdive/debate 作为回调传入）→ 纯逻辑、可离线测、不绑 akshare。

对外主入口：orchestrate(task, resolve_fn, deepdive_fn, debate_fn) -> dict
"""
from __future__ import annotations

import agent_intent

_ORDER = {"无": 0, "低": 1, "中": 2, "高": 3, "极高": 4}


def _max_level_score(rows: list[dict]) -> tuple[str, int]:
    """取多年里最严重的风险档与最高单年得分。"""
    if not rows:
        return "无", 0
    top = max(rows, key=lambda r: r.get("score", 0))
    return top.get("level", "无"), top.get("score", 0)


def _regulatory_in_redflags(redflags: list[dict] | None) -> bool:
    """定性红旗里是否含监管硬事实（立案/处罚/非标）——升级控辩/追责的强信号。"""
    for f in (redflags or []):
        if f.get("category") in ("监管立案", "监管处罚", "非标审计意见"):
            return True
    return False


def _run_one(code, name, resolve_fn, deepdive_fn, debate_fn):
    """
    对单个标的执行"分级升级"编排，返回该标的的完整结果 + 决策轨迹。
    结构：{code,name,reached,final_level,rows,gnn,deepdive,debate,penalty_flag,trail:[...]}
    reached 标记最终升级到了哪一层：screen / deepdive / debate。
    """
    trail = []  # 决策轨迹：[{stage, decision, reason}]

    # ── ① 粗筛（永远先做，成本最低）────────────────────────────
    rows, gnn, profile, err = resolve_fn(code)
    if err is not None:
        return {"code": code, "name": name, "error": "数据拉取失败或为金融机构/退市（不适用）",
                "reached": "none", "trail": [{"stage": "① 粗筛", "decision": "跳过",
                "reason": "该标的无有效年报数据（金融机构/退市/代码异常）"}]}
    disp = (profile or {}).get("short") or name or code
    level, score = _max_level_score(rows)
    gnn_high = bool(gnn and gnn.get("prob", 0) >= 0.5)
    trail.append({"stage": "① 粗筛 · 信号+GNN", "decision": "已执行",
                  "reason": f"{disp}：财务信号最高档「{level}」({score}分)"
                            + (f"，GNN 舞弊概率{gnn['prob']:.2f}(排名{gnn['rank']}/{gnn['total']})"
                               if gnn else "，无 GNN 记录")})

    result = {"code": code, "name": disp, "profile": profile, "rows": rows, "gnn": gnn,
              "reached": "screen", "final_level": level, "deepdive": None, "debate": None,
              "penalty_flag": False, "trail": trail}

    # ── ② 分级决策：清白止步，否则升级深度研判 ──────────────────
    if score == 0 and not gnn_high:
        trail.append({"stage": "② 升级决策", "decision": "止步于粗筛",
                      "reason": "各维度未见异常，判为低风险；不深挖、不谈追责——节省算力且避免越权深查"})
        result["final_level"] = level if level != "无" else "无"
        return result

    trail.append({"stage": "② 升级决策", "decision": "升级 → 深度研判 Agent",
                  "reason": f"粗筛已亮起风险信号（{level}/{score}分{'、GNN偏高' if gnn_high else ''}），"
                            "值得读公告、跑推理做交叉印证"})
    dd = deepdive_fn(code, profile, rows, gnn)
    result["deepdive"] = dd
    result["reached"] = "deepdive"
    redflags = dd.get("redflags") or []
    trail.append({"stage": "③ 深度研判 · 已执行", "decision": "读材料+推理",
                  "reason": f"Planner 自主规划并调用能力，提炼 {len(redflags)} 条定性红旗、"
                            "输出有推理链的研判综述"})

    # ── ③ 分级决策：是否升级控辩互搏 ────────────────────────────
    reg_hit = _regulatory_in_redflags(redflags)
    escalate_debate = (score >= 4) or gnn_high or reg_hit
    if not escalate_debate:
        trail.append({"stage": "④ 升级决策", "decision": "止步于深度研判",
                      "reason": f"风险为「{level}」中低档、无监管硬事实——深度研判已足够，"
                                "不启动控辩对抗（避免过度研判）"})
        return result

    trail.append({"stage": "④ 升级决策", "decision": "升级 → 控辩互搏 Agent",
                  "reason": "命中高风险档"
                            + ("、监管硬事实" if reg_hit else "")
                            + ("、GNN 结构异常" if gnn_high else "")
                            + "——升级三方对抗，让辩方交叉检验、收敛更可信结论"})
    deb = debate_fn(disp, rows, gnn, redflags)
    result["debate"] = deb
    result["reached"] = "debate"
    result["final_level"] = deb.get("final_level", level)
    sustained = sum(1 for r in (deb.get("rulings") or []) if r.get("verdict") == "sustained")
    trail.append({"stage": "⑤ 控辩互搏 · 已执行", "decision": "三方对抗收敛",
                  "reason": f"控方指控 {len(deb.get('charges', []))} 条、辩方逐条回应、"
                            f"裁判裁定 {sustained} 条成立，最终判「{result['final_level']}」"})

    # ── ④ 分级决策：是否标记追责测算 ───────────────────────────
    if _ORDER.get(result["final_level"], 0) >= 3:  # 高/极高
        result["penalty_flag"] = True
        trail.append({"stage": "⑥ 升级决策", "decision": "标记 → 追责测算（law/ 引擎）",
                      "reason": f"对抗后仍判「{result['final_level']}」高风险，衔接法条库测算"
                                "行政/刑事/民事三线责任（见 law/penalty_calc.py）"})
    else:
        trail.append({"stage": "⑥ 升级决策", "decision": "不启动追责测算",
                      "reason": f"对抗后收敛为「{result['final_level']}」，未达追责档——避免越权定性"})
    return result


def orchestrate(task, resolve_fn, deepdive_fn, debate_fn, max_targets=2):
    """
    总控主入口。task=自然语言任务；三个 *_fn 为依赖注入的能力回调。
    返回 {restated, intent_type, source, targets:[...单标的结果], summary, compare?}
    """
    intent = agent_intent.parse_intent(task)
    if intent["intent_type"] == "out_of_scope":
        return {"restated": task, "intent_type": "out_of_scope", "source": intent["source"],
                "message": "本工具专注 A 股上市公司财务风险研判，暂不能回答该问题。", "targets": []}
    if not intent["companies"]:
        return {"restated": intent.get("restated", task), "intent_type": intent["intent_type"],
                "source": intent["source"],
                "message": "未识别出具体公司，请补充公司名或6位代码。", "targets": []}

    targets = []
    for c in intent["companies"][:max_targets]:
        targets.append(_run_one(c["code"], c.get("name", c["code"]),
                                 resolve_fn, deepdive_fn, debate_fn))

    ok = [t for t in targets if "error" not in t]
    out = {"restated": intent.get("restated", task), "intent_type": intent["intent_type"],
           "source": intent["source"], "targets": targets}

    # 汇总：编排概览（各标的升级到哪层）
    reach_cn = {"screen": "止步粗筛", "deepdive": "深度研判", "debate": "控辩互搏", "none": "不适用"}
    out["summary"] = {
        "n": len(targets),
        "orchestration": [{"name": t.get("name", t["code"]),
                           "reached": reach_cn.get(t.get("reached"), t.get("reached")),
                           "level": t.get("final_level", "—")} for t in targets],
    }
    # 对比意图：给一句谁更干净
    if intent["intent_type"] == "compare" and len(ok) == 2:
        a, b = ok[0], ok[1]
        cleaner = a if _ORDER.get(a.get("final_level"), 0) <= _ORDER.get(b.get("final_level"), 0) else b
        out["compare"] = (f"{cleaner['name']} 相对更干净：{a['name']}「{a.get('final_level')}」、"
                          f"{b['name']}「{b.get('final_level')}」（仅基于公开信息研判，非投资建议）")
    return out

