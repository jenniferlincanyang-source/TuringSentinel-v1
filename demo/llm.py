# -*- coding: utf-8 -*-
"""
RiskCopilot · LLM 薄封装（资料理解层与研判综述层的推理内核）

设计原则：
1. 只读环境变量里的凭据（ANTHROPIC_BASE_URL / ANTHROPIC_AUTH_TOKEN），不硬编码 key；
2. 优雅降级——无凭据 / 超时 / 出错，一律返回 None，调用方回退到纯确定性模式，demo 不崩；
3. 只发送公开财报数字与公告标题，不含任何隐私数据。

对外只暴露两个函数：
  available()               → LLM 是否可用（决定前端是否走"深度研判"）
  chat(system, user, ...)   → 返回模型文本；json=True 时解析为对象，失败返回 None
"""
from __future__ import annotations

import json
import os
import urllib.request

# 资料理解用轻量模型（快、便宜），研判综述用更强模型
MODEL_FAST = "claude-haiku-4-5-20251001"
MODEL_SMART = "claude-sonnet-4-6"

_BASE = os.environ.get("ANTHROPIC_BASE_URL", "").rstrip("/")
_TOKEN = os.environ.get("ANTHROPIC_AUTH_TOKEN", "") or os.environ.get("ANTHROPIC_API_KEY", "")


def available() -> bool:
    """是否配了可用的 LLM 凭据。"""
    return bool(_BASE and _TOKEN)


def chat(system: str, user: str, model: str = MODEL_FAST,
         max_tokens: int = 1500, want_json: bool = False, timeout: int = 40):
    """
    调一次 Claude。成功返回文本(str)或解析后的对象(want_json=True)；任何失败返回 None。
    调用方务必处理 None（降级路径），不要假设一定拿到结果。
    """
    if not available():
        return None
    body = json.dumps({
        "model": model,
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }).encode("utf-8")
    req = urllib.request.Request(
        _BASE + "/v1/messages", data=body,
        headers={
            "content-type": "application/json",
            "x-api-key": _TOKEN,
            "authorization": "Bearer " + _TOKEN,
            "anthropic-version": "2023-06-01",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read())
        parts = data.get("content") or []
        text = "".join(p.get("text", "") for p in parts if p.get("type") == "text").strip()
        if not text:
            return None
        if not want_json:
            return text
        return _parse_json(text)
    except Exception:
        return None


def _parse_json(text: str):
    """从模型输出里稳健地抠出 JSON（容忍 ```json 代码块包裹）。失败返回 None。"""
    t = text.strip()
    if t.startswith("```"):
        t = t.strip("`")
        if t.lower().startswith("json"):
            t = t[4:]
    # 截取第一个 { 到最后一个 } / 第一个 [ 到最后一个 ]
    for lo, hi in (("{", "}"), ("[", "]")):
        i, j = t.find(lo), t.rfind(hi)
        if 0 <= i < j:
            try:
                return json.loads(t[i:j + 1])
            except Exception:
                continue
    return None
