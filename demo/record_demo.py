# -*- coding: utf-8 -*-
"""
════════════════════════════════════════════════════════════════════════
 RiskCopilot · 完整功能自动录屏脚本（Playwright 驱动 + 内置录像，全程无需人工）
════════════════════════════════════════════════════════════════════════

【这个脚本录什么】——覆盖 demo 全部 5 个交互入口 + 全部结果板块：

  入口1  run()        经典案例（康美）   → 5-Agent 流水线 + 风险矩阵
  入口2  runTask()    一句话提任务       → 自然语言意图理解 → 自动拆解 → 对比两家（最像"自主 Agent"）
  入口3  runLive()    快查任意 A 股      → 实时拉真实年报 + 信号表 + GNN + 追责 + 溯源
  入口4  runDeepdive()深度研判           → Planner 自主规划 + 执行轨迹 + 读公告红旗 + LLM 综述 + 可操作清单
  入口5  runDebate()  控辩互搏           → 控方/辩方/裁判三 Agent 对抗收敛（复赛级差异化）

【为什么不卡】后端已对 deepdive/debate/task 做结果缓存，录前 _warmup() 预热 →
              录制时点按钮秒出，无 30-60s LLM 等待的干等空白（缓存的是真实结果，非造假）。

【本脚本性质】写死的操作序列（自动播放器），不含 LLM 决策，非自主 agent——仅用于录 demo。

用法：
  1. 另一终端跑后端：python3 backend.py
  2. python3 record_demo.py
  3. 产出：demo/recording/RiskCopilot-demo.mp4
════════════════════════════════════════════════════════════════════════
"""
from __future__ import annotations

import json
import subprocess
import urllib.parse
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
DEMO_HTML = HERE / "RiskCopilot-demo.html"
OUT_DIR = HERE / "recording"
API = "http://127.0.0.1:8199"
VW, VH = 1440, 900               # 录像分辨率
COMPARE_Q = "600519和000651哪个财务更干净"   # 一句话任务的示例问题

# ── 旁白时间轴（单一数据源）──────────────────────────────────────────
# 录制时按“累计停顿秒数”记录每句字幕出现的真实时间点，落盘 narration.json；
# add_voice.py 直接读它对齐配音，改了任何停顿都不会再错位。
_CLOCK = {"t": 0.0}          # 累计视频时间（只统计 wait_for_timeout）
NARRATION: list[dict] = []   # [{t, sub}]，__OUTRO__ 标记尾页起点
NARRATION_JSON = OUT_DIR / "narration.json"


def _warmup():
    """录前预热：把三家公司的 deepdive/debate + 对比任务查询预跑入缓存，录制时秒出。"""
    try:
        urllib.request.urlopen(f"{API}/api/warmup?codes=600519,600518,000651", timeout=400).read()
        q = urllib.parse.quote(COMPARE_Q)
        urllib.request.urlopen(f"{API}/api/task?q={q}", timeout=200).read()
        print("✓ 后端预热完成（康美/茅台/格力 + 对比任务 已入缓存）")
    except Exception as e:
        print(f"⚠ 预热失败（后端没起？）：{e}")


def _pause(page, sec, note=""):
    """停顿让录像画面停留可读，并打印分镜进度；同时累计视频时钟。"""
    if note:
        print(f"  … {note}（停 {sec}s）")
    page.wait_for_timeout(int(sec * 1000))
    _CLOCK["t"] += sec


def _scroll_to(page, sel, note="", pause=4):
    """平滑滚动到某元素并停留。"""
    page.evaluate(f"document.getElementById('{sel}')?.scrollIntoView({{behavior:'smooth',block:'center'}})")
    _pause(page, pause, note)


# ── 字幕条：在页面底部注入一个固定字幕层，随镜头更新文字（起旁白解说作用）──
_SUB_JS_INIT = """
(function(){
  if(document.getElementById('__sub')) return;
  var s=document.createElement('div'); s.id='__sub';
  s.style.cssText='position:fixed;left:0;right:0;bottom:0;z-index:99999;'
    +'background:linear-gradient(0deg,rgba(0,0,0,.82),rgba(0,0,0,.55));'
    +'color:#fff;font-size:22px;line-height:1.5;font-weight:600;text-align:center;'
    +'padding:18px 60px;font-family:-apple-system,\"PingFang SC\",sans-serif;'
    +'text-shadow:0 2px 8px rgba(0,0,0,.9);letter-spacing:.5px;';
  document.body.appendChild(s);
})();
"""


def _say(page, text):
    """更新底部字幕条文字（旁白解说），并按当前视频时钟记录旁白时间点。"""
    page.evaluate(_SUB_JS_INIT)
    page.evaluate("(t)=>{document.getElementById('__sub').textContent=t;}", text)
    # 配音略滞后于字幕出现半秒更自然；夹紧到 >=0
    NARRATION.append({"t": round(max(0.0, _CLOCK["t"] - 0.3), 2), "sub": text})


def _title_card(page, lines, sec=4):
    """全屏片头/尾页：盖一层深色板，显示多行标题文字。"""
    html = "".join(
        f'<div style="font-size:{sz}px;font-weight:{w};color:{c};margin:{m}">{t}</div>'
        for (t, sz, w, c, m) in lines)
    page.evaluate("""(html)=>{
      var d=document.getElementById('__title');
      if(!d){d=document.createElement('div');d.id='__title';document.body.appendChild(d);}
      d.style.cssText='position:fixed;inset:0;z-index:100000;display:flex;flex-direction:column;'
        +'align-items:center;justify-content:center;text-align:center;'
        +'background:radial-gradient(circle at 50% 40%,#12224e,#05070f);'
        +'font-family:-apple-system,\"PingFang SC\",sans-serif;';
      d.innerHTML=html;
    }""", html)
    _pause(page, sec)


def _title_hide(page):
    page.evaluate("document.getElementById('__title')?.remove()")


def run():
    OUT_DIR.mkdir(exist_ok=True)
    _warmup()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": VW, "height": VH},
            record_video_dir=str(OUT_DIR),
            record_video_size={"width": VW, "height": VH},
        )
        page = context.new_page()
        print("▶ 开始录制完整版（含片头+字幕）…")
        page.goto(DEMO_HTML.as_uri())
        page.wait_for_load_state("networkidle")

        # ── 片头标题页 ────────────────────────────────────────────
        _title_card(page, [
            ("图灵哨兵", 34, 600, "#4da3ff", "0 0 6px"),
            ("RiskCopilot", 64, 800, "#fff", "8px 0"),
            ("财务舞弊尽调与风险研判的多 Agent 副驾", 26, 500, "#9fb2df", "6px 0 18px"),
            ("GOAI 世界人工智能开源大赛 · 赛道二 无界应用 ｜ AI+金融", 18, 400, "#6c7fb0", "10px"),
        ], sec=4)
        _title_hide(page)

        # ── 镜头1 · 开场定位（问题与场景）──────────────────────────
        _say(page, "RiskCopilot：站在独立第三方审计视角，只用公开信息判断上市公司真伪")
        _pause(page, 3, "镜头1 开场")

        # ── 镜头2 · 经典案例：5-Agent 流水线 + 风险矩阵 ─────────────
        # 入口1 run()：内置康美真实数据，展示多 Agent 协同流水线
        _say(page, "① 经典案例·康美药业：五个职能 Agent 协同，跑通完整研判流水线")
        page.select_option("#company", "kangmei")
        _pause(page, 1)
        page.click("#runBtn")
        _pause(page, 5, "镜头2 康美流水线")
        _say(page, "风险维度矩阵：多引擎证据一眼可读，每格判据可溯源、可复现")
        _scroll_to(page, "matrixCard", "风险矩阵", 5)
        _say(page, "确定性财务信号：读真实年报算比率，逐条判据可审计，无大模型参与")
        _scroll_to(page, "sigTable", "信号表", 3)
        _say(page, "处置追责测算：法条库官方原文坐实，测算判罚与投资者索赔")
        _scroll_to(page, "lawBox", "追责", 3)
        _say(page, "数据溯源·交叉核验：每条结论都能追到公开一手来源，不编造")
        _scroll_to(page, "srcBox", "溯源", 3)

        # ── 镜头3 · 一句话提任务（自然语言 → 自动拆解 → 对比两家）──
        # 入口2 runTask()：最能体现"自主 Agent"——用户一句话，Agent 自理解、拆解、跑多家
        page.evaluate("window.scrollTo(0,0)")
        _say(page, "② 一句话提任务：用户只说自然语言，Agent 自己理解意图、拆解、执行")
        _pause(page, 2)
        page.fill("#taskInput", COMPARE_Q)
        _pause(page, 2, "镜头3 输入任务")
        page.click("#taskBtn")
        _pause(page, 6, "镜头3 自主拆解")
        _say(page, "Agent 复述它理解的任务，自动对比两家公司、给出谁更干净的结论")
        _scroll_to(page, "intentBanner", "意图复述", 5)

        # ── 镜头4 · 快查任意 A 股（实时·不误报）────────────────────
        # 入口3 runLive()：换代码实时拉真实年报当场算，证明动态、有区分度
        page.evaluate("window.scrollTo(0,0)")
        _say(page, "③ 快查任意 A 股：输入代码实时拉真实年报当场算——以贵州茅台为例")
        page.fill("#codeInput", "600519")
        _pause(page, 1)
        page.click("#liveBtn")
        _pause(page, 6, "镜头4 茅台快查")
        _say(page, "茅台矩阵全绿、信号不误报——有区分度，才敢用在真实尽调")
        _scroll_to(page, "matrixCard", "茅台全绿", 4)

        # ── 镜头5 · 深度研判（Planner + 轨迹 + 红旗 + 综述 + 清单）──
        # 入口4 runDeepdive()：赛道二"资料理解"核心——读公告、可解释轨迹、Planner 自主规划
        page.evaluate("window.scrollTo(0,0)")
        _say(page, "④ 深度研判：会读公告、会推理的 Agent 副驾（赛道二「资料理解」核心）")
        page.fill("#codeInput", "600518")
        _pause(page, 1)
        page.click("#deepBtn")
        _pause(page, 6, "镜头5 深度研判")
        _say(page, "Planner 自主规划：不走写死流水线，按风险局面自主决定调哪些能力")
        _scroll_to(page, "planBox", "Planner", 5)
        _say(page, "Agent 执行轨迹：每一步可解释、可审计")
        _scroll_to(page, "traceBox", "轨迹", 4)
        _say(page, "定性红旗：LLM 读公告提炼数字看不见的信号，每条带溯源链接、不编造")
        _scroll_to(page, "redflagBox", "红旗", 5)
        _say(page, "研判综述：融合定量信号 + GNN 结构 + 定性红旗，有推理链、标来源")
        _scroll_to(page, "deepVerdict", "综述", 4)
        _say(page, "可操作核查清单：不止报风险，还给下一步怎么查——拿了能干活")
        _scroll_to(page, "actionBox", "清单", 5)

        # ── 镜头6 · 控辩互搏（三 Agent 对抗，复赛级差异化）─────────
        # 入口5 runDebate()：控方指控/辩方辩护/裁判裁定，名副其实的多 Agent 协同
        page.evaluate("window.scrollTo(0,0)")
        _say(page, "⑤ 多 Agent 控辩互搏：从「单 Agent 调工具」升级为名副其实的「多 Agent 协同」")
        page.fill("#codeInput", "600518")
        _pause(page, 1)
        page.click("#debateBtn")
        _pause(page, 6, "镜头6 控辩互搏")
        _say(page, "三个立场对立的 Agent，就同一套硬证据展开对抗")
        _scroll_to(page, "debateRounds", "三轮次", 4)
        _say(page, "🔴 控方：站最审慎审计视角，尽力提出结构性风险指控")
        _scroll_to(page, "prosBox", "控方", 5)
        _say(page, "🔵 辩方：为每条指控找合理经营解释——辩方越强，系统越不会误报")
        _scroll_to(page, "defBox", "辩方", 5)
        _say(page, "⚖️ 裁判：逐条裁定成立/被削弱/存疑，硬事实不可辩、不得辩没")
        _scroll_to(page, "judgeBox", "裁判", 5)
        _say(page, "对抗后收敛出最终裁定——只做风险提示，不替代机构与司法决策")
        _scroll_to(page, "debateVerdict", "最终裁定", 5)

        # ── 尾页 ──────────────────────────────────────────────────
        # 尾页无字幕条，但有配音第23句；在此记录尾页出现时间点供配音对齐。
        NARRATION.append({"t": round(_CLOCK["t"], 2), "sub": "__OUTRO__"})
        _title_card(page, [
            ("RiskCopilot", 52, 800, "#fff", "8px 0"),
            ("信号库 · 法条库 · 编排全部可复现、可开源（Apache-2.0）", 22, 500, "#9fb2df", "8px 0"),
            ("把审计的「较真」，变成 AI 能规模化的能力", 26, 600, "#00e0c6", "16px 0 0"),
            ("图灵哨兵", 20, 400, "#6c7fb0", "20px 0 0"),
        ], sec=10)   # 尾页停久些，给第23句配音留足时间，避免被 -shortest 截断

        context.close()   # 关闭 context 才落盘视频
        browser.close()

    # ── 落盘旁白时间轴（供 add_voice.py 对齐配音，杜绝错位）──
    NARRATION_JSON.write_text(
        json.dumps(NARRATION, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"✓ 旁白时间轴：{NARRATION_JSON.name}（{len(NARRATION)} 段，末段 @{NARRATION[-1]['t']}s）")

    # ── 转码 webm → mp4 ────────────────────────────────────────
    webms = sorted(OUT_DIR.glob("*.webm"), key=lambda f: f.stat().st_mtime)
    if not webms:
        print("✗ 未生成视频")
        return
    webm = webms[-1]
    mp4 = OUT_DIR / "RiskCopilot-demo.mp4"
    print(f"✓ 原始录像：{webm.name}")
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(webm), "-c:v", "libx264", "-pix_fmt", "yuv420p",
             "-movflags", "+faststart", str(mp4)],
            check=True, capture_output=True)
        print(f"✓ 已转 mp4：{mp4}")
    except Exception as e:
        print(f"⚠ ffmpeg 失败，直接用 webm：{webm}｜{str(e)[:80]}")


if __name__ == "__main__":
    run()
