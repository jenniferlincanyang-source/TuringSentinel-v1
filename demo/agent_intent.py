# -*- coding: utf-8 -*-
"""
RiskCopilot · 意图理解层（对齐参赛手册 §8.2.1 任务输入 + §8.2.2 意图理解）

把用户的自然语言任务 → 结构化意图：
  "康美药业有没有财务造假风险" → {companies:[600518], intent_type:"single_risk", focus:[...]}
  "600519 和 000651 哪个财务更干净" → {companies:[600519,000651], intent_type:"compare"}

设计：
  - LLM 解析为主（懂"康美/茅台"这种公司名 → 代码）；
  - 正则抓 6 位代码兜底（LLM 挂掉也能用）；
  - 域外问题（不是财务风险研判）礼貌拒答，不硬套。
"""
from __future__ import annotations

import re

import llm

# 少量常见公司名→代码映射（LLM 不可用时的兜底；LLM 可用时它自己会认更多）
_NAME_HINTS = {
    "康美": "600518", "康美药业": "600518",
    "茅台": "600519", "贵州茅台": "600519",
    "格力": "000651", "格力电器": "000651",
    "大华": "002236", "大华股份": "002236",
    "比亚迪": "002594", "宁德时代": "300750", "万科": "000002",
    "康得新": "002450", "瑞幸": "",  # 瑞幸非A股，占位提示
}

_INTENT_SYS = (
    "你是金融风险研判助手的意图解析器。用户会用自然语言提出关于 A 股上市公司财务风险/舞弊的问题。"
    "请解析出结构化意图。规则：\n"
    "1. 识别问题涉及的公司，给出6位A股代码（你知道的：康美600518/茅台600519/格力000651/"
    "大华002236/比亚迪002594/宁德时代300750/万科000002 等，其他按你的知识补全）；\n"
    "2. intent_type 取值：single_risk(单家风险研判)/compare(多家对比)/explain(解释某现象)/unknown；\n"
    "3. 若问题与'A股上市公司财务风险研判'无关（如闲聊、其他领域），intent_type 填 out_of_scope；\n"
    "4. focus 填用户特别关注的点（如'货币资金''存贷双高''关联方'），没有则空数组。\n"
    "只输出 JSON：{\"companies\":[{\"name\":\"..\",\"code\":\"6位\"}],"
    "\"intent_type\":\"..\",\"focus\":[..],\"restated\":\"用一句话复述用户想问什么\"}"
)


def _regex_codes(text: str) -> list[str]:
    """兜底：从文本里抓所有 6 位数字代码（用前后非数字断言，兼容中文夹数字）。"""
    return re.findall(r"(?<!\d)(\d{6})(?!\d)", text)


def _hint_codes(text: str) -> list[dict]:
    """兜底：用公司名关键词映射。"""
    out = []
    for name, code in _NAME_HINTS.items():
        if code and name in text and not any(c["code"] == code for c in out):
            out.append({"name": name, "code": code})
    return out


def parse_intent(text: str) -> dict:
    """
    解析用户任务为结构化意图。返回：
      {companies:[{name,code}], intent_type, focus:[], restated, source}
    source 标注是 LLM 解析还是规则兜底，便于前端如实展示。
    """
    text = (text or "").strip()
    if not text:
        return {"companies": [], "intent_type": "unknown", "focus": [],
                "restated": "", "source": "empty", "error": "请输入一个任务或问题"}

    # ① LLM 解析
    if llm.available():
        res = llm.chat(_INTENT_SYS, f"用户任务：{text}", model=llm.MODEL_FAST,
                       want_json=True, max_tokens=600, timeout=30)
        if isinstance(res, dict) and "intent_type" in res:
            comps = []
            for c in res.get("companies", []):
                code = str(c.get("code", "")).strip()
                if re.fullmatch(r"\d{6}", code):
                    comps.append({"name": c.get("name", code), "code": code})
            res["companies"] = comps
            res["source"] = "llm"
            res.setdefault("focus", [])
            res.setdefault("restated", text)
            return res

    # ② 规则兜底（LLM 不可用/失败）
    codes = _regex_codes(text)
    comps = [{"name": c, "code": c} for c in codes] or _hint_codes(text)
    intent = "compare" if len(comps) >= 2 else ("single_risk" if comps else "unknown")
    return {
        "companies": comps, "intent_type": intent, "focus": [],
        "restated": text, "source": "rule",
    }
