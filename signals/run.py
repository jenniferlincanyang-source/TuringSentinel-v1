# -*- coding: utf-8 -*-
"""
图灵哨兵 · 种子信号库 统一入口

用法：
    python run.py                 # 跑内置康美案例（data/kangmei.json）
    python run.py data/xxx.json   # 跑指定公司数据

导入 signals_registry 会自动把所有 @register 的信号注册进引擎。
"""
from __future__ import annotations

import json
import os
import sys

import signals_registry  # noqa: F401  仅为触发信号注册
from engine import evaluate_company


def main() -> None:
    here = os.path.dirname(os.path.abspath(__file__))
    default = os.path.join(here, "data", "kangmei.json")
    target = sys.argv[1] if len(sys.argv) > 1 else default
    if not os.path.isabs(target):
        target = os.path.join(here, target) if not os.path.exists(target) else target
    if not os.path.exists(target):
        print(f"找不到数据文件：{target}")
        sys.exit(1)
    with open(target, "r", encoding="utf-8") as f:
        data = json.load(f)
    evaluate_company(data)


if __name__ == "__main__":
    main()
