# -*- coding: utf-8 -*-
"""
RiskCopilot Demo 后端 —— 输入任意A股代码，实时拉取真实财报，跑信号库研判。

数据源：AkShare（免费公开财经数据，东方财富接口）
引擎：复用 signals/ 信号库的确定性规则（不依赖大模型）

启动：
  cd demo && python3 backend.py
  浏览器打开 RiskCopilot-demo.html（前端会调 http://127.0.0.1:8199）

接口：
  GET /api/analyze?code=600519  → 返回该股近几年的信号研判(真实数据实时算)
"""
from __future__ import annotations
import warnings
warnings.filterwarnings("ignore")

import json
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

# 复用 signals 引擎的阈值与判定逻辑
SIG_DIR = Path(__file__).resolve().parent.parent / "signals"
sys.path.insert(0, str(SIG_DIR))

import signals_registry  # noqa: F401  触发 @register 把 7 条信号注册进引擎
from engine import score_year, score_year_detail, level_of  # 唯一的信号真相源，demo 不再手抄一份

YI = 1e8

# 结果缓存（录 demo 提速：预热一次后，同代码再查秒出，消除 LLM 等待的干等空白）。
# 缓存的是"真实计算结果"，非造假——只是把已算过的 deepdive/debate 存下来复用。
# 加 ?fresh=1 可强制绕过缓存重新计算。进程重启即清空。
_CACHE: dict[str, dict] = {}

app = FastAPI(title="RiskCopilot Demo API")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)

# 加载 GNN 关系图谱风险分（FiGraph 2019，GraphSAGE 预计算导出）
GNN_SCORES = {}
try:
    with open(Path(__file__).resolve().parent / "gnn_scores.json", encoding="utf-8") as f:
        GNN_SCORES = json.load(f)
except Exception:
    pass


def _f(v):
    try:
        if v is None:
            return 0.0
        f = float(v)
        return 0.0 if f != f else f  # NaN → 0
    except Exception:
        return 0.0


def fetch_financials(code: str, years: int = 4):
    """用 akshare 拉取指定A股近 years 个年报期的关键财务科目。"""
    import akshare as ak
    prefix = "SH" if code.startswith(("6", "5")) else "SZ"
    sym = f"{prefix}{code}"
    bs = ak.stock_balance_sheet_by_report_em(symbol=sym)
    inc = ak.stock_profit_sheet_by_report_em(symbol=sym)
    try:
        cf = ak.stock_cash_flow_sheet_by_report_em(symbol=sym)
    except Exception:
        cf = None

    def annual(df):
        df = df.copy()
        df["d"] = df["REPORT_DATE"].astype(str).str[:10]
        df = df[df["d"].str.endswith("12-31")]  # 只取年报
        return df

    bs_a, inc_a = annual(bs), annual(inc)
    cf_a = annual(cf) if cf is not None else None
    out = []
    for i in range(min(years, len(bs_a))):
        b = bs_a.iloc[i]
        year = str(b["d"])[:4]
        inc_row = inc_a[inc_a["d"] == b["d"]]
        ic = inc_row.iloc[0] if len(inc_row) else None
        ocf = None
        if cf_a is not None:
            cf_row = cf_a[cf_a["d"] == b["d"]]
            if len(cf_row):
                ocf = _f(cf_row.iloc[0].get("NETCASH_OPERATE"))
        debt = _f(b.get("SHORT_LOAN")) + _f(b.get("LONG_LOAN")) + \
               _f(b.get("BOND_PAYABLE")) + _f(b.get("SHORT_BOND_PAYABLE"))
        # 字段名与结构严格对齐 signals 引擎的 schema（interest_bearing_debt 为 {total}、
        # cost_of_sales / operating_cashflow），以便直接喂给同一套信号，不再手抄逻辑。
        rec = {
            "year": year,
            "monetary_funds": _f(b.get("MONETARYFUNDS")),
            "total_assets": _f(b.get("TOTAL_ASSETS")),
            "equity_parent": _f(b.get("TOTAL_PARENT_EQUITY")),
            "interest_bearing_debt": {"total": debt},
            "accounts_receivable": _f(b.get("ACCOUNTS_RECE")),
            "inventory": _f(b.get("INVENTORY")),
            "other_receivables": _f(b.get("TOTAL_OTHER_RECE")),
            "goodwill": _f(b.get("GOODWILL")),
            # 利息收入口径：资产端 INTEREST_INCOME 与 财务费用冲抵的 FE_INTEREST_INCOME
            # 两个科目口径不同(制造业+财务公司常见)，取较大值更贴近真实资金收益，避免低估→误判"钱不生息"
            "interest_income": max(_f(ic.get("INTEREST_INCOME")), _f(ic.get("FE_INTEREST_INCOME"))) if ic is not None else 0,
            "revenue": _f(ic.get("OPERATE_INCOME")) if ic is not None else 0,
            "operating_profit": _f(ic.get("OPERATE_PROFIT")) if ic is not None else 0,
            "cost_of_sales": _f(ic.get("OPERATE_COST")) if ic is not None else 0,
            "operating_cashflow": ocf,
        }
        out.append(rec)
    return list(reversed(out))  # 时间正序


def fetch_profile(code: str) -> dict:
    """拉取企业基本档案（巨潮公司概况 + 同花顺主营介绍）。失败不阻断主流程。"""
    import akshare as ak
    prof = {}
    # 巨潮公司概况
    try:
        df = ak.stock_profile_cninfo(symbol=code)
        if len(df):
            r = df.iloc[0]
            def g(k):
                v = r.get(k, "")
                return "" if v is None or str(v) == "nan" else str(v).strip()
            prof.update({
                "name": g("公司名称"), "short": g("A股简称"), "en": g("英文名称"),
                "industry": g("所属行业"), "market": g("所属市场"),
                "legal": g("法人代表"), "capital": g("注册资金"),
                "found": g("成立日期"), "listed": g("上市日期"),
                "site": g("官方网站"), "addr": g("办公地址") or g("注册地址"),
                "index": g("入选指数"), "main": g("主营业务"),
            })
    except Exception:
        pass
    # 同花顺主营介绍（补主营/经营范围，更口语）
    try:
        df2 = ak.stock_zyjs_ths(symbol=code)
        if len(df2):
            r2 = df2.iloc[0]
            def g2(k):
                v = r2.get(k, "")
                return "" if v is None or str(v) == "nan" else str(v).strip()
            if not prof.get("main"):
                prof["main"] = g2("主营业务")
            prof["biz_main"] = g2("主营业务")
            prof["products"] = g2("产品名称") or g2("产品类型")
            prof["scope"] = g2("经营范围")
    except Exception:
        pass
    return prof


# 维度 → 归属的方法论大类（供矩阵分组，对标成熟尽调技能的"十部分框架"分区）
_DIM_GROUP = {
    "001": "资金真实性", "005": "资金真实性",
    "002": "收入质量", "007": "收入质量",
    "003": "利润质量", "004": "存货与成本",
    "006": "并购与商誉",
}


def build_matrix(recs: list[dict], gnn: dict | None, redflags: list[dict] | None = None) -> dict:
    """
    把 7 信号(取各年最严重档) + GNN + 定性红旗，收敛成一张可一眼读的风险矩阵。
    对标成熟股权尽调技能的"16维风险矩阵"交付形态，但每格判据来自确定性引擎/图模型/真实公告，
    可溯源、可复现——抄它的交付形态，不抄它"LLM归纳"的判据方式。
    返回：{dims:[{code,name,group,level,score,status,basis,year,source}], summary}
    """
    # 1) 财务信号维度：跨年取每条信号的最严重表现，并记录发生年份
    #    用 enumerate 取上年数据(prev)，避免 recs.index() 在两年数据雷同时误取下标
    worst: dict[str, dict] = {}
    for i, r in enumerate(recs):
        prev = recs[i - 1] if i > 0 else None
        for row in score_year_detail(r, prev):
            sid = row["id"]
            cur = worst.get(sid)
            # 命中优先于未评估；同为命中取分高者；记录该表现所在年份
            better = (cur is None
                      or (row["status"] == "triggered" and cur["status"] != "triggered")
                      or (row["status"] == "triggered" and cur["status"] == "triggered"
                          and row["score"] > cur["score"]))
            if better:
                worst[sid] = {**row, "year": r["year"]}

    dims = []
    for sid in sorted(worst.keys()):
        row = worst[sid]
        dims.append({
            "code": f"F{int(sid)}", "name": row["name"], "group": _DIM_GROUP.get(sid, "其他"),
            "level": row["level"] if row["status"] == "triggered" else ("—" if row["status"] == "na" else "无"),
            "score": row["score"], "status": row["status"],
            "basis": row["basis"] or ([row["note"]] if row["note"] else ["未见异常"]),
            "year": row.get("year", ""), "source": "确定性财务信号（年报）",
        })

    # 2) GNN 关系图谱维度
    # ⚠️ 编码约定：财务信号占 F1-F7（信号号 001-007），GNN 固定 F8、红旗固定 F9。
    #    若将来 @register("008") 新增第 8 条信号，需把 GNN/红旗的编码顺延，避免与 F8 撞码。
    if gnn and gnn.get("prob") is not None:
        prob = gnn["prob"]
        lv = "极高" if prob >= 0.9 else "高" if prob >= 0.5 else "中" if prob >= 0.2 else "低"
        dims.append({
            "code": "F8", "name": "关系图谱结构异常", "group": "关联网络",
            "level": lv, "score": None, "status": "triggered" if prob >= 0.5 else "clear",
            "basis": [f"GNN舞弊概率{prob:.2f}，风险排名{gnn.get('rank')}/{gnn.get('total')}"
                      + (f"，关联{len(gnn.get('fraud_neighbors',[]))}个已知舞弊邻居"
                         if gnn.get("fraud_neighbors") else "")],
            "year": "", "source": "GNN 图谱（FiGraph 2019）",
        })

    # 3) 定性红旗维度（有 deepdive 才有；按类别聚合成一格）
    if redflags:
        cats = sorted({f.get("category", "") for f in redflags if f.get("category")})
        dims.append({
            "code": "F9", "name": "公开公告定性红旗", "group": "公开舆情",
            "level": "高" if len(redflags) >= 5 else "中", "score": None, "status": "triggered",
            "basis": [f"{'、'.join(cats[:5])} 等 {len(redflags)} 条（可溯源至原始公告）"],
            "year": "", "source": "巨潮公告/东财新闻",
        })

    order = {"极高": 4, "高": 3, "中": 2, "低": 1, "无": 0, "—": -1}
    triggered = [d for d in dims if d["status"] == "triggered"]
    top = max(triggered, key=lambda d: order.get(d["level"], 0)) if triggered else None
    summary = {
        "total_dims": len(dims),
        "triggered": len(triggered),
        "na": len([d for d in dims if d["status"] == "na"]),
        "top_level": top["level"] if top else "无",
        "top_dim": top["name"] if top else "",
    }
    return {"dims": dims, "summary": summary}


@app.get("/api/analyze")
def analyze(code: str):
    code = code.strip()
    if not (code.isdigit() and len(code) == 6):
        return JSONResponse({"error": "请输入6位A股代码，如 600519"}, status_code=400)
    profile = fetch_profile(code)  # 企业档案（失败返回{}，不阻断）
    try:
        recs = fetch_financials(code)
    except Exception as ex:
        msg = str(ex)[:120]
        hint = "（该股可能已退市或代码有误，数据源无记录）" if "'data'" in msg or "index" in msg else ""
        return JSONResponse({"error": f"拉取数据失败{hint}：{msg}", "profile": profile}, status_code=502)
    if not recs:
        return JSONResponse({"error": "未获取到年报数据（可能已退市）", "profile": profile}, status_code=404)

    # 行业适用性：本信号体系针对一般工商企业；金融机构(银行/保险/券商)财报结构不同,不适用
    latest = recs[-1]
    if latest["total_assets"] <= 0 or latest["monetary_funds"] <= 0:
        return JSONResponse({
            "code": code, "rows": [], "overall": "不适用",
            "not_applicable": True, "profile": profile,
            "note": "该标的关键科目缺失或为金融机构（银行/保险/券商）——其资产负债表结构与工商企业不同，"
                    "'存贷双高''货币资金占比'等信号不适用。金融机构需专用风控模型（复赛可扩展）。"
        })

    rows = []
    for i, r in enumerate(recs):
        prev = recs[i - 1] if i > 0 else None
        hits, score = score_year(r, prev)
        rows.append({
            "year": r["year"], "level": level_of(score), "score": score, "hits": hits,
            "monetary_funds_yi": round(r["monetary_funds"] / YI, 1),
            "total_assets_yi": round(r["total_assets"] / YI, 1),
            "debt_yi": round((r["interest_bearing_debt"] or {}).get("total", 0) / YI, 1),
            "m2a": round(r["monetary_funds"] / r["total_assets"], 3) if r["total_assets"] else None,
            "m2e": round(r["monetary_funds"] / r["equity_parent"], 3) if r["equity_parent"] else None,
        })
    maxrow = max(rows, key=lambda x: x["score"]) if rows else None

    # GNN 关系图谱风险分（FiGraph 2019 快照，若该公司在图中）
    gnn = None
    g = GNN_SCORES.get(code)
    if g and isinstance(g, dict) and "prob" in g:
        meta = GNN_SCORES.get("_meta", {})
        gnn = {
            "prob": g["prob"], "rank": g["rank"], "total": g["total"], "pct": g["pct"],
            "degree": g.get("degree", 0), "fraud_neighbors": g.get("fraud_neighbors", []),
            "edge_types": g.get("edge_types", {}),
            "fraud_avg": meta.get("fraud_avg"), "normal_avg": meta.get("normal_avg"),
            "label": g.get("label"),
        }

    matrix = build_matrix(recs, gnn)
    return {"code": code, "rows": rows, "overall": maxrow["level"] if maxrow else "无",
            "gnn": gnn, "profile": profile, "matrix": matrix,
            "note": "财务数据：AkShare（东方财富，实时公开）；GNN：FiGraph 2019 关系图谱 GraphSAGE 打分；均无大模型参与。"}


def _build_rows_gnn(code: str):
    """复用 analyze 的取数+信号+GNN 逻辑，返回 (rows, gnn, profile, recs, err_response)。"""
    profile = fetch_profile(code)
    try:
        recs = fetch_financials(code)
    except Exception as ex:
        return None, None, profile, None, JSONResponse(
            {"error": f"拉取数据失败：{str(ex)[:120]}", "profile": profile}, status_code=502)
    if not recs:
        return None, None, profile, None, JSONResponse(
            {"error": "未获取到年报数据（可能已退市）", "profile": profile}, status_code=404)
    latest = recs[-1]
    if latest["total_assets"] <= 0 or latest["monetary_funds"] <= 0:
        return None, None, profile, None, JSONResponse(
            {"code": code, "not_applicable": True, "profile": profile,
             "note": "金融机构或关键科目缺失，本信号体系不适用。"})
    rows = []
    for i, r in enumerate(recs):
        prev = recs[i - 1] if i > 0 else None
        hits, score = score_year(r, prev)
        rows.append({"year": r["year"], "level": level_of(score), "score": score, "hits": hits})
    gnn = None
    g = GNN_SCORES.get(code)
    if g and isinstance(g, dict) and "prob" in g:
        gnn = {"prob": g["prob"], "rank": g["rank"], "total": g["total"], "pct": g["pct"],
               "fraud_neighbors": g.get("fraud_neighbors", [])}
    return rows, gnn, profile, recs, None


@app.get("/api/deepdive")
def deepdive(code: str, fresh: int = 0):
    """深度研判：初筛→规划→读公告材料(A)→LLM研判综述(B) 的 Agent 闭环。"""
    code = code.strip()
    if not (code.isdigit() and len(code) == 6):
        return JSONResponse({"error": "请输入6位A股代码，如 600519"}, status_code=400)
    ck = f"deepdive:{code}"
    if not fresh and ck in _CACHE:
        return {**_CACHE[ck], "cached": True}
    rows, gnn, profile, recs, err = _build_rows_gnn(code)
    if err is not None:
        return err
    import agent_deepdive
    result = agent_deepdive.run_deepdive(code, profile, rows, gnn)
    matrix = build_matrix(recs, gnn, result.get("redflags"))
    result.update({"code": code, "profile": profile, "rows": rows, "gnn": gnn, "matrix": matrix})
    _CACHE[ck] = result
    return result


@app.get("/api/debate")
def debate(code: str, fresh: int = 0):
    """多 Agent 控辩互搏：控方指控→辩方辩护→裁判裁定，对抗后收敛风险画像。"""
    code = code.strip()
    if not (code.isdigit() and len(code) == 6):
        return JSONResponse({"error": "请输入6位A股代码，如 600519"}, status_code=400)
    ck = f"debate:{code}"
    if not fresh and ck in _CACHE:
        return {**_CACHE[ck], "cached": True}
    rows, gnn, profile, recs, err = _build_rows_gnn(code)
    if err is not None:
        return err
    # 复用深度研判的资料理解，拿到定性红旗作为控辩证据的一部分
    import agent_deepdive, agent_debate
    dd = agent_deepdive.run_deepdive(code, profile, rows, gnn)
    redflags = dd.get("redflags") or []
    name = (profile or {}).get("short") or (profile or {}).get("name") or code
    result = agent_debate.run_debate(name, rows, gnn, redflags)
    matrix = build_matrix(recs, gnn, redflags)
    result.update({"code": code, "profile": profile, "rows": rows, "gnn": gnn,
                   "matrix": matrix, "redflags": redflags,
                   "overall": max(rows, key=lambda x: x["score"])["level"] if rows else "无"})
    _CACHE[ck] = result
    return result


@app.get("/api/orchestrate")
def orchestrate(q: str):
    """
    总控编排 Agent（项目组长）：意图理解 → 对每标的分级升级
    （粗筛→深度研判→控辩互搏→追责标记）→ 汇总 + 决策轨迹。
    展示"同代码不同输入走不同编排路径"，直击手册 §8.2.3 任务规划。
    """
    q = (q or "").strip()
    if not q:
        return JSONResponse({"error": "请输入一个任务或问题"}, status_code=400)
    ck = f"orchestrate:{q}"
    if ck in _CACHE:
        return {**_CACHE[ck], "cached": True}

    import agent_orchestrator, agent_deepdive, agent_debate

    # 依赖注入：把已有能力包装成 orchestrator 需要的回调
    def resolve_fn(code):
        rows, gnn, profile, recs, err = _build_rows_gnn(code)
        return rows, gnn, profile, err

    def deepdive_fn(code, profile, rows, gnn):
        return agent_deepdive.run_deepdive(code, profile, rows, gnn)

    def debate_fn(name, rows, gnn, redflags):
        return agent_debate.run_debate(name, rows, gnn, redflags)

    out = agent_orchestrator.orchestrate(q, resolve_fn, deepdive_fn, debate_fn)
    _CACHE[ck] = out
    return out


@app.get("/api/task")
def task(q: str):
    """
    自然语言任务入口（对齐手册 §8.2 完整闭环）：
      任务输入(q) → 意图理解 → 对每家公司跑 Planner 规划+执行 → 汇总可操作结论。
    返回 {restated, intent_type, source, results:[{code,name,...deepdive...}], compare?}
    """
    import agent_intent, agent_deepdive
    q = (q or "").strip()
    if not q:
        return JSONResponse({"error": "请输入一个任务或问题"}, status_code=400)
    ck = f"task:{q}"
    if ck in _CACHE:
        return {**_CACHE[ck], "cached": True}

    # 【§8.2.2 意图理解】
    intent = agent_intent.parse_intent(q)
    if intent["intent_type"] == "out_of_scope":
        return {"restated": q, "intent_type": "out_of_scope", "source": intent["source"],
                "message": "本工具专注 A 股上市公司财务风险研判，暂不能回答该问题。"
                           "可以试着问：'康美药业有没有财务造假风险'。", "results": []}
    if not intent["companies"]:
        return {"restated": q, "intent_type": intent["intent_type"], "source": intent["source"],
                "message": "没能从你的问题里识别出具体公司。请补充公司名或6位A股代码，"
                           "例如'查一下格力电器(000651)的风险'。", "results": []}

    # 【§8.2.3-8.2.5 规划→执行→交付】对每家公司跑完整深度研判（最多2家，控范围）
    results = []
    for c in intent["companies"][:2]:
        code = c["code"]
        rows, gnn, profile, recs, err = _build_rows_gnn(code)
        if err is not None:
            results.append({"code": code, "name": c.get("name", code),
                            "error": "该标的数据拉取失败或为金融机构/退市（不适用）"})
            continue
        dd = agent_deepdive.run_deepdive(code, profile, rows, gnn)
        dd.update({"code": code, "name": (profile or {}).get("short") or c.get("name", code),
                   "profile": profile, "rows": rows, "gnn": gnn,
                   "matrix": build_matrix(recs, gnn, dd.get("redflags")),
                   "overall": max(rows, key=lambda x: x["score"])["level"] if rows else "无"})
        results.append(dd)

    out = {"restated": intent.get("restated", q), "intent_type": intent["intent_type"],
           "source": intent["source"], "focus": intent.get("focus", []), "results": results}
    # 对比场景：给一句谁更干净的结论
    if intent["intent_type"] == "compare" and len([r for r in results if "error" not in r]) == 2:
        order = {"无": 0, "低": 1, "中": 2, "高": 3, "极高": 4}
        a, b = results[0], results[1]
        cleaner = a if order.get(a.get("overall"), 0) <= order.get(b.get("overall"), 0) else b
        out["compare"] = (f"{cleaner['name']} 相对更干净：{a['name']}综合{a.get('overall')}风险、"
                          f"{b['name']}综合{b.get('overall')}风险（仅基于公开财报信号，非投资建议）")
    _CACHE[ck] = out
    return out


@app.get("/api/warmup")
def warmup(codes: str = "600519,600518"):
    """录 demo 前预热：预先跑好指定代码的 deepdive+debate 并入缓存，录制时点按钮秒出。"""
    done = []
    for code in [c.strip() for c in codes.split(",") if c.strip().isdigit()]:
        try:
            deepdive(code)
            debate(code)
            done.append(code)
        except Exception as e:
            done.append(f"{code}(失败:{str(e)[:40]})")
    return {"warmed": done, "cache_keys": list(_CACHE.keys())}


@app.get("/api/value_scan")
def value_scan(chain: str = "光通信", no_attr: int = 0, fresh: int = 0):
    """
    价值发现（"价值发现正脸"）：链热单体冷错配扫描 → 归属核验(控辩) → 兜底排雷 → 候选清单。

    ① 错配扫描(value_engine 确定性规则) ② 归属核验(agent_attribution 控辩互搏，识破"亨通陷阱")
    ③ 兜底排雷(复用 RiskCopilot 双引擎，仅对'真实关联'候选) ④ 带证据链+盲区的候选清单。

    ⚠️ 数据来源诚实声明：当前用 data/value_sample.json（结构示意，真值待接 akshare 板块成员+
       行情+估值 与 巨潮披露）。判据逻辑本身完全确定性、可复现。真实取数层为复赛 TODO。
    ⚠️ 全流水线带 LLM 控辩较慢（每候选3段请求），结果进缓存；录制/演示前先命中缓存。
    """
    import value_scan as vs
    ck = f"value_scan:{chain}:{no_attr}"
    if not fresh and ck in _CACHE:
        return {**_CACHE[ck], "cached": True}
    # 按链名选数据文件：光通信=value_sample.json；其余=value_sample_<链>.json
    data_dir = Path(__file__).resolve().parent / "data"
    fname = "value_sample.json" if chain in ("光通信", "") else f"value_sample_{chain}.json"
    fpath = data_dir / fname
    if not fpath.exists():
        avail = ["光通信"] + [p.stem.replace("value_sample_", "")
                             for p in data_dir.glob("value_sample_*.json")]
        return JSONResponse({"error": f"暂无「{chain}」链的样例数据。可选：{'、'.join(avail)}"
                             + "（真实任意板块取数为复赛 TODO：接 akshare 板块成分）"},
                            status_code=404)
    try:
        with open(fpath, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as ex:
        return JSONResponse({"error": f"样例数据读取失败：{str(ex)[:120]}"}, status_code=500)

    out = vs.run_pipeline(data["chain"], data["members"], run_attr=not no_attr)
    out["data_note"] = (data.get("_meta", {}) or {}).get("honesty_note", "")
    out["disclaimer"] = ("本系统输出为'值得人工深看的候选 + 证据链'，"
                         "不预测涨跌、不构成投资建议。机器发现错配，人判断机会还是陷阱。")
    _CACHE[ck] = out
    return out


# ============ 决策日志：录入判断 / 补结果 / 诚实统计 ============
# 存本地 JSON。诚实边界：只对"已补真实结果"的记录做统计，一条没有就显示"待积累"，绝不虚报胜率。
LOG_FILE = Path(__file__).resolve().parent / "data" / "decision_log.json"


def _load_log() -> list:
    try:
        with open(LOG_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _save_log(rows: list) -> None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)


@app.get("/api/log/add")
def log_add(date: str, target: str, source: str = "", sys_judge: str = "",
            my_judge: str = "", action: str = "观察"):
    """录入一条决策判断（前瞻记录，结果留空待补）。用 GET+query 便于前端直接调。"""
    rows = _load_log()
    rid = (max([r.get("id", 0) for r in rows], default=0) + 1)
    rows.append({"id": rid, "date": date.strip(), "target": target.strip(),
                 "source": source.strip(), "sys_judge": sys_judge.strip(),
                 "my_judge": my_judge.strip(), "action": action.strip(),
                 "result": "", "correct": None, "review": ""})
    _save_log(rows)
    return {"ok": True, "id": rid, "total": len(rows),
            "note": "已前瞻记录（结果待N月后补）。决策日志不记资金，只记判断——事后不许改。"}


@app.get("/api/log/resolve")
def log_resolve(id: int, result: str, correct: int, review: str = ""):
    """补一条记录的真实结果（correct: 1=判断对 / 0=判断错）。"""
    rows = _load_log()
    for r in rows:
        if r.get("id") == id:
            r["result"] = result.strip()
            r["correct"] = bool(correct)
            r["review"] = review.strip()
            _save_log(rows)
            return {"ok": True, "id": id}
    return JSONResponse({"error": f"未找到记录 id={id}"}, status_code=404)


@app.get("/api/log/stats")
def log_stats():
    """
    诚实统计：只对"已补真实结果"的记录算准确率；未补结果的只计入"待验证"，绝不虚报。
    """
    rows = _load_log()
    resolved = [r for r in rows if r.get("correct") is not None]
    pending = [r for r in rows if r.get("correct") is None]
    by_source = {}
    for r in resolved:
        s = r.get("source") or "未标"
        by_source.setdefault(s, {"n": 0, "hit": 0})
        by_source[s]["n"] += 1
        by_source[s]["hit"] += 1 if r.get("correct") else 0
    hit = sum(1 for r in resolved if r.get("correct"))
    return {
        "total": len(rows), "resolved": len(resolved), "pending": len(pending),
        "accuracy": (round(hit / len(resolved), 3) if resolved else None),
        "hit": hit, "by_source": by_source, "rows": rows,
        "honest_note": ("尚无已验证结果，无法给准确率——待前瞻积累（诚实标注，不虚报未发生的胜率）。"
                        if not resolved else
                        f"基于 {len(resolved)} 条已验证记录统计；{len(pending)} 条待N月后补结果。"),
    }


@app.get("/api/health")
def health():
    import llm
    return {"ok": True, "llm": llm.available(), "cached": list(_CACHE.keys())}


@app.get("/api/shutdown")
def shutdown():
    """供前端「关闭后端」按钮调用：延迟半秒退出，先让本次响应正常返回。"""
    import threading, os, signal

    def _die():
        import time
        time.sleep(0.5)
        os.kill(os.getpid(), signal.SIGINT)  # 触发 uvicorn 优雅退出

    threading.Thread(target=_die, daemon=True).start()
    return {"ok": True, "msg": "后端将在 0.5 秒后关闭"}


if __name__ == "__main__":
    import uvicorn
    print("RiskCopilot Demo 后端启动 → http://127.0.0.1:8199")
    print("接口示例：http://127.0.0.1:8199/api/analyze?code=600519")
    uvicorn.run(app, host="127.0.0.1", port=8199, log_level="warning")
