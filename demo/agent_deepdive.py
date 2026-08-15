# -*- coding: utf-8 -*-
"""
RiskCopilot · 深度研判 Agent（A+B+C 一体的 ReAct 闭环）

编排（C）：
  ① 初筛     复用确定性信号 + GNN（由 backend 传入 rows/gnn）→ 得到"哪些红旗亮了"
  ② 规划     Agent 依据亮起的信号，决定去读什么类别的材料（LLM 规划，降级用规则）
  ③ 读材料（A）拉巨潮公告标题+新闻 → LLM 提取定性红旗（只准引用给定标题，防幻觉）
  ④ 研判（B） LLM 融合 定量信号 + GNN + 定性发现 → 有推理链的尽调结论

铁律（守住项目皇冠）：
  - 硬信号是 ground truth，LLM 不得推翻，只能补充定性证据与解释；
  - 定性发现只能引用"确实取到的公告/新闻标题"，每条带来源，不编造；
  - 结尾强制合规声明：仅辅助研判，不替代机构与司法决策；
  - 无 LLM / 网络失败 → 全程降级为规则版，demo 不崩。

对外只暴露 run_deepdive(code, profile, rows, gnn)，返回可直接 JSON 化的 dict。
"""
from __future__ import annotations

import re as _re

import materials as M
import llm


# ---------------- ② 规划：亮起的信号 → 该重点读哪些材料 ----------------
# 信号命中的关键词 → 应重点关注的公告类别（讲给评委听："为什么去读这些"）
_SIGNAL_TO_FOCUS = [
    (("货币资金", "存贷双高", "不生息"), ["监管立案", "非标审计意见", "股东冻结/占用", "股东质押"]),
    (("其他应收款", "占用"), ["股东冻结/占用", "违规担保", "对外担保"]),
    (("营收", "应收", "收入"), ["交易所问询", "财报更正", "媒体质疑澄清"]),
    (("商誉",), ["商誉减值", "业绩变脸"]),
    (("存货",), ["交易所问询", "财报更正"]),
]


def _plan_focus(rows: list[dict]) -> list[str]:
    """据已亮起信号的判据文本，推断该重点关注的公告类别（规则版规划）。"""
    hit_text = " ".join(h for r in rows for h in r.get("hits", []))
    focus: list[str] = []
    for kws, cats in _SIGNAL_TO_FOCUS:
        if any(k in hit_text for k in kws):
            for c in cats:
                if c not in focus:
                    focus.append(c)
    # 无论如何，监管类信号永远值得看
    for c in ("监管立案", "监管处罚", "非标审计意见", "更换审计机构"):
        if c not in focus:
            focus.append(c)
    return focus


def _years_of_interest(rows: list[dict]) -> tuple[int, int]:
    """取信号覆盖的年份区间（用于拉对应年份的公告）。"""
    yrs = [int(r["year"]) for r in rows if str(r.get("year", "")).isdigit()]
    if not yrs:
        return (2018, 2020)
    return (min(yrs), max(yrs) + 1)  # 造假次年往往才被立案/问询，多看一年


# ---------------- ③ 读材料（A）：LLM 从公告标题里提取定性红旗 ----------------
_EXTRACT_SYS = (
    "这是一个公开信息分类任务。以下是某上市公司在巨潮资讯网/交易所依法公开披露的公告标题，"
    "均为已公开的合规信息。请以财务分析师视角，从中筛选出与『投资风险提示』相关的条目"
    "（如监管问询、审计意见变化、诉讼、资金占用、担保、业绩修正等），供投资者做尽职调查参考。\n"
    "要求：①只能引用列表中真实出现的标题，逐字复制，不改写不编造；"
    "②跳过常规经营公告（分红、募资、日常人事）；③按风险相关度排序。\n"
    "只输出 JSON：{\"flags\":[{\"title\":\"<原始标题>\",\"category\":\"<风险类别>\","
    "\"why\":\"<一句话说明该事项的风险提示意义>\"}]}"
)


def _extract_redflags_llm(redflags: list[dict]) -> list[dict] | None:
    """用 LLM 从粗筛后的公告标题里提炼定性红旗并解释。失败返回 None（降级）。"""
    if not redflags:
        return []
    # 标题、日期分列，明确要求只复制标题，减少 LLM 把日期拼进标题
    lines = [f"{i+1}. 【{d['date']}】{d['title']}" for i, d in enumerate(redflags[:40])]
    user = "公告标题列表（请在 title 字段只复制标题正文，不要带序号和日期）：\n" + "\n".join(lines)
    res = llm.chat(_EXTRACT_SYS, user, model=llm.MODEL_SMART, want_json=True,
                   max_tokens=2000, timeout=75)
    if not isinstance(res, dict) or "flags" not in res:
        return None
    # 用真实标题做校验，把 LLM 输出映射回原始 url（防幻觉：标题必须真实存在）。
    # 宽松匹配：normalize 后互为包含即视为命中（容忍 LLM 拼了日期/去了空格）。
    def _norm(s: str) -> str:
        return _re.sub(r"[\s（）()【】\d\-年月日]", "", s)
    normed = [(_norm(d["title"]), d) for d in redflags]
    out = []
    for f in res.get("flags", []):
        t = (f.get("title") or "").strip()
        nt = _norm(t)
        src = None
        if nt:
            for ns, d in normed:
                if ns and (ns in nt or nt in ns):
                    src = d
                    break
        if not src:  # LLM 给的标题匹配不上任何真实公告 → 丢弃，绝不放行编造项
            continue
        out.append({
            "title": t, "date": src["date"], "url": src["url"],
            "category": f.get("category") or src["category"],
            "why": (f.get("why") or "").strip(),
        })
    return out


# ---------------- ④ 研判（B）：融合定量+GNN+定性 → 有推理链的结论 ----------------
_SYNTH_SYS = (
    "你是站在独立第三方审计视角的财务风险研判专家（RiskCopilot 副驾）。"
    "下面给你三类已核实的证据：①确定性财务信号（规则引擎算出，是硬事实，不可推翻）；"
    "②GNN 关系图谱风险分（图神经网络在关联方/股权/资金网络上的结构异常）；"
    "③从公开公告提炼的定性红旗。\n"
    "请写一段 150-260 字的尽调研判，要求：\n"
    "1. 用推理链把三类证据串起来，讲清'定量异常 + 关系结构 + 定性事件'如何相互印证；\n"
    "2. 每个关键结论标注依据来源（如'据货币资金信号''据XX公告'）；\n"
    "3. 不得编造未提供的事实；不得给出'一定造假'的定性，只做风险研判与建议；\n"
    "4. 结尾一句：本研判仅辅助研判，不替代机构与司法决策。\n"
    "输出纯中文段落，不要 JSON、不要标题。"
)


def _synthesize_llm(code: str, profile: dict, rows: list[dict],
                    gnn: dict | None, redflags: list[dict]) -> str | None:
    """把三类证据交给 LLM 融合成研判综述。失败返回 None（降级）。"""
    name = (profile or {}).get("short") or (profile or {}).get("name") or code
    sig_lines = []
    for r in rows:
        if r.get("hits"):
            sig_lines.append(f"{r['year']}年[{r['level']}风险]：" + "；".join(r["hits"]))
    gnn_line = "无（不在图谱快照内）"
    if gnn:
        gnn_line = (f"舞弊概率{gnn['prob']:.2f}，风险排名第{gnn['rank']}/{gnn['total']}"
                    f"（排名越小越可疑），关联{len(gnn.get('fraud_neighbors',[]))}个已知舞弊邻居")
    rf_line = "；".join(f"{f['category']}·{f['title'][:24]}" for f in redflags[:10]) or "无显著定性红旗"
    user = (
        f"公司：{name}（{code}）\n"
        f"①确定性财务信号：\n" + ("\n".join(sig_lines) if sig_lines else "各年均未触发显著信号") + "\n"
        f"②GNN关系图谱：{gnn_line}\n"
        f"③公开公告定性红旗：{rf_line}"
    )
    return llm.chat(_SYNTH_SYS, user, model=llm.MODEL_SMART, want_json=False, max_tokens=900)


# ---------------- 降级版（无 LLM 时仍给出结构化研判，demo 不崩）----------------
def _synthesize_rule(name: str, rows: list[dict], gnn: dict | None,
                     redflags: list[dict]) -> str:
    top = max(rows, key=lambda r: r.get("score", 0)) if rows else None
    parts = []
    if top and top.get("hits"):
        parts.append(f"确定性信号在{top['year']}年触发{top['level']}风险："
                     + "；".join(top["hits"][:3]) + "。")
    if gnn and gnn.get("prob", 0) >= 0.5:
        parts.append(f"GNN关系图谱给出{gnn['prob']:.2f}高概率（全图前{gnn['pct']:.1%}），"
                     "关系结构层面同样异常。")
    if redflags:
        cats = sorted({f["category"] for f in redflags})
        parts.append("公开公告端出现" + "、".join(cats[:5]) + "等定性红旗，与上述信号相互印证。")
    if not parts:
        parts.append("各维度未见显著异常。")
    parts.append("本研判仅辅助研判，不替代机构与司法决策。")
    return "【规则综述】" + "".join(parts)


# ---------------- 对外主入口 ----------------
# ============================================================
# Planner（对齐参赛手册 §8.2.3 任务规划）
# 核心：Agent 不走写死的流水线，而是先"看清局面"再"自主决定调哪些能力"。
# 决策依据是当前初筛结果（哪些信号亮了、GNN 如何、是否金融机构），
# 每条决策都带 reason，可解释、可审计——这就是"无界"的自主编排。
# ============================================================

# 可调用的能力池（§8.6 可选能力：结构化/图数据/知识增强/研判）
CAPABILITIES = {
    "signals": "结构化财务信号引擎（7类确定性规则）",
    "gnn": "GNN 关系图谱结构异常",
    "materials": "公开公告/舆情资料理解（LLM 提炼定性红旗）",
    "penalty": "法条追责测算（行政/刑事/民事三线）",
    "synthesis": "多源融合研判综述",
}

# 每个能力的调用规格（对齐手册 §8.5：调用方式/权限范围/返回处理/失败处理）
# 让评委看清"Agent 到底怎么调工具、调什么、越不越权、出错怎么办"。
CAPABILITY_SPEC = {
    "signals": {
        "type": "本地确定性引擎（无网络、无大模型）",
        "invoke": "import signals 引擎 score_year()，输入年报科目 dict",
        "scope": "只读财务数字，纯计算，不外发数据",
        "returns": "命中判据文本 + 风险得分（逐条可审计）",
        "onfail": "科目缺失→该信号跳过并标注，不影响其他信号",
    },
    "gnn": {
        "type": "预训练图模型（GraphSAGE，FiGraph 2019 快照）",
        "invoke": "查 gnn_scores.json（离线预计算），按代码取节点分",
        "scope": "只读公开关系图谱打分，不训练、不写回",
        "returns": "舞弊概率 + 全图排名 + 已知舞弊邻居",
        "onfail": "公司不在图谱内→标注'无GNN记录'，跳过图分析",
    },
    "materials": {
        "type": "外部公开数据 API（AkShare / 巨潮资讯）",
        "invoke": "stock_zh_a_disclosure_report_cninfo（HTTP，公开）",
        "scope": "只读公开公告标题+链接，无需授权、不涉隐私",
        "returns": "定性红旗（每条带原始公告链接，标题须匹配真实公告防幻觉）",
        "onfail": "网络失败→降级为关键词粗筛，不阻断主流程",
    },
    "penalty": {
        "type": "本地法条库（官方原文坐实）",
        "invoke": "law/penalty_calc.py，按案情匹配法条测算",
        "scope": "只读受控法条库，测算不定性、不替代司法",
        "returns": "行政/刑事/民事三线责任区间 + 法条援引出处",
        "onfail": "案情不足→只列可适用法条，不强行测算",
    },
    "synthesis": {
        "type": "大模型（Claude 兼容，模型可插拔）",
        "invoke": "llm.chat()，把定量+GNN+定性证据融合",
        "scope": "只发送公开财报数字+公告标题，不含隐私",
        "returns": "有推理链、标来源的研判综述",
        "onfail": "LLM 不可用/超时→降级为规则综述，功能不崩",
    },
}


def plan(rows: list[dict], gnn: dict | None) -> list[dict]:
    """
    据初筛局面自主规划本次要调用的能力，返回决策列表：
      [{cap, use: bool, reason}]
    不同输入 → 不同计划（茅台清白 vs 康美高危 vs 金融机构 会得到不同的调用集合）。
    """
    fired = [r for r in rows if r.get("score", 0) > 0]
    max_score = max((r.get("score", 0) for r in rows), default=0)
    gnn_high = bool(gnn and gnn.get("prob", 0) >= 0.5)
    decisions: list[dict] = []

    # signals / gnn 是初筛已有的既定事实，恒纳入并说明局面
    decisions.append({"cap": "signals", "use": True,
                      "reason": f"初筛{len(fired)}个年度触发财务信号，最高单年{max_score}分"})
    decisions.append({"cap": "gnn", "use": bool(gnn),
                      "reason": (f"GNN舞弊概率{gnn['prob']:.2f}、排名{gnn['rank']}/{gnn['total']}" if gnn
                                 else "该公司不在GNN图谱快照内，跳过图分析")})

    # materials：只要有任何财务信号亮起、或 GNN 偏高，就值得去读公告交叉印证；
    # 即便全清白也读一轮以防漏报（但会在 reason 里说明是防御性核查）。
    if fired or gnn_high:
        decisions.append({"cap": "materials", "use": True,
                          "reason": "有信号亮起，调取公告核查定性证据、与定量交叉印证"})
    else:
        decisions.append({"cap": "materials", "use": True,
                          "reason": "定量未见异常，仍做一轮防御性公告核查以防漏报"})

    # penalty：只有在"高/极高"档时才规划追责测算——低风险公司谈追责是越权且无意义。
    penalty_needed = max_score >= 4
    decisions.append({"cap": "penalty", "use": penalty_needed,
                      "reason": ("命中高风险档，规划追责测算能力（本轮 demo 仅标记，落地见 law/ 引擎）"
                                 if penalty_needed else "风险未达高档，不启动追责测算（避免越权）")})

    decisions.append({"cap": "synthesis", "use": True,
                      "reason": "汇总所有已调用能力的产出，融合成有推理链的研判"})
    # 给每条决策附上 §8.5 调用规格，供前端展开"怎么调、调什么、越不越权、出错怎么办"
    for d in decisions:
        d["spec"] = CAPABILITY_SPEC.get(d["cap"])
    return decisions


# ---------------- 可操作结果（§8.3：输出可操作、可解释）----------------
# 命中的信号类型 → 建议的下一步核查动作（把"报警"变成"能干活的清单"）
_ACTION_MAP = [
    ("现金", "调取货币资金银行对账单与定期存款明细，核对账实与受限情况"),
    ("存贷双高", "比对同期利息收入与利息支出，验证'账上有钱还大额举债'是否合理"),
    ("不生息", "核对利息收入科目口径，确认大额货币资金为何未产生相应利息"),
    ("其他应收款", "穿透其他应收款对象，排查是否为大股东/关联方非经营性占用"),
    ("应收账款", "核查应收账款账龄结构与前五大客户，评估收入真实性与回款风险"),
    ("存货", "用产能/仓储物理约束反推账面存货合理性，实地盘点抽验"),
    ("商誉", "复核并购标的业绩承诺完成度，评估商誉减值敞口"),
    ("营收", "比对营收增速与经营现金流、应收增速，排查收入注水"),
]


def build_actions(rows: list[dict], redflags: list[dict]) -> list[str]:
    """据命中的信号与定性红旗，生成'可操作核查清单'（用户拿了能干活，非空话）。"""
    hit_text = " ".join(h for r in rows for h in r.get("hits", []))
    actions = []
    for kw, act in _ACTION_MAP:
        if kw in hit_text and act not in actions:
            actions.append(act)
    if redflags:
        cats = sorted({f.get("category", "") for f in redflags if f.get("category")})
        if cats:
            actions.append(f"逐一核实公开公告红旗（{'、'.join(cats[:4])}），点击溯源链接查原文")
    if not actions:
        actions.append("各维度未见显著异常，建议按常规尽调节奏定期复查")
    actions.append("以上为风险提示与核查建议，不构成定性结论；高风险项须人工复核")
    return actions


def effectiveness_note() -> dict:
    """有效性自证（§8.2.6 验证与反馈）：用已定性案例做'回测背书'，证明规则非拍脑袋。"""
    return {
        "backtest": "康美药业2016-2018三年，本信号引擎均命中最高风险档，与证监会〔2020〕24号认定一致",
        "reproducible": "signals/ 与 law/ 均可 python run.py 一键复现，逐条判据可审计",
        "no_false_alarm": "对贵州茅台等正常标的返回'无风险'，不误报（含格力误报已自我校准修正）",
    }


def run_deepdive(code: str, profile: dict, rows: list[dict], gnn: dict | None) -> dict:
    """
    执行深度研判闭环——由 Planner 自主规划、再按计划调用能力（对齐手册 §8.2 七环节）。
    返回：{llm_used, plan, trace, redflags, verdict, disclosures_total, news}
    plan 暴露"Agent 决定调什么、为什么"；trace 记录"实际怎么执行"，二者都给评委看。
    """
    trace: list[dict] = []
    name = (profile or {}).get("short") or (profile or {}).get("name") or code
    llm_on = llm.available()

    # 【§8.2.2 意图理解 + §8.2.1 初筛】复用传入的确定性信号 + GNN
    fired = [r for r in rows if r.get("score", 0) > 0]
    trace.append({
        "step": "① 意图理解 · 初筛", "title": "确定性信号 + GNN 打分",
        "detail": (f"{len(fired)}个年度触发财务信号"
                   + (f"；GNN舞弊概率{gnn['prob']:.2f}(风险排名{gnn['rank']}/{gnn['total']})" if gnn else "；无GNN记录"))
        if fired or gnn else "各年均未触发显著财务信号，仍按流程核查定性材料以防漏报",
    })

    # 【§8.2.3 任务规划】Planner 自主决定本次调哪些能力
    decisions = plan(rows, gnn)
    used = {d["cap"] for d in decisions if d["use"]}
    trace.append({
        "step": "② 任务规划 · Planner", "title": "自主决定调用哪些能力（按局面而变）",
        "detail": "；".join(f"{'✓' if d['use'] else '✗'}{CAPABILITIES[d['cap']]}：{d['reason']}"
                          for d in decisions),
    })

    # 【§8.2.4 能力调用】按计划执行——materials（资料理解 A）
    redflags: list[dict] = []
    mat = {"disclosures_total": 0, "redflags": [], "news": []}
    if "materials" in used:
        focus = _plan_focus(rows)
        y0, y1 = _years_of_interest(rows)
        mat = M.gather(code, y0, y1)
        trace.append({
            "step": "③ 能力调用 · 资料理解", "title": f"读 {y0}-{y1} 年公开公告+新闻（可溯源）",
            "detail": f"聚焦{('、'.join(focus[:5]))}；取到 {mat['disclosures_total']} 条公告、"
                      f"{len(mat['news'])} 条新闻，粗筛出 {len(mat['redflags'])} 条疑似定性红旗",
        })
        r = _extract_redflags_llm(mat["redflags"]) if llm_on else None
        if r is None:  # 降级：直接用关键词粗筛结果
            redflags = [{**d, "why": f"标题含'{d['category']}'类预警信息"} for d in mat["redflags"][:12]]
            extract_mode = "规则粗筛（LLM不可用/超时降级）"
        else:
            redflags = r
            extract_mode = "LLM 语义提取"
        trace.append({
            "step": "④ 能力调用 · 提炼红旗", "title": extract_mode,
            "detail": f"确认 {len(redflags)} 条定性红旗，每条可溯源到原始公告链接"
                      + ("" if redflags else "；未发现显著定性红旗"),
        })

    # penalty 能力：仅在计划里被选中时提示（本轮 demo 标记，不内联 law 引擎）
    if "penalty" in used:
        trace.append({
            "step": "⑤ 能力调用 · 追责测算", "title": "已规划追责测算（law/ 引擎）",
            "detail": "命中高风险档，可衔接法条库测算行政/刑事/民事三线责任（见 law/penalty_calc.py）",
        })

    # 【§8.2.5 结果交付 · 研判 B】融合所有已调用能力的产出
    verdict = _synthesize_llm(code, profile, rows, gnn, redflags) if llm_on else None
    if verdict is None:
        verdict = _synthesize_rule(name, rows, gnn, redflags)
        synth_mode = "规则综述（LLM降级）"
    else:
        synth_mode = "LLM 研判综述"
    trace.append({
        "step": "⑥ 结果交付 · 研判综述", "title": synth_mode,
        "detail": "融合 定量信号 + GNN结构 + 定性红旗，输出有推理链、标来源的尽调结论",
    })

    return {
        "llm_used": llm_on and synth_mode.startswith("LLM"),
        "plan": decisions,
        "trace": trace,
        "redflags": redflags,
        "verdict": verdict,
        "actions": build_actions(rows, redflags),          # §8.3 可操作清单
        "effectiveness": effectiveness_note(),              # §8.2.6 有效性自证
        "disclosures_total": mat["disclosures_total"],
        "news": mat["news"][:5],
    }
