# -*- coding: utf-8 -*-
"""
给 RiskCopilot demo 视频加中文配音（macOS say TTS + ffmpeg 合成，全命令行，无需剪映）。

原理：
  1. 每段旁白用 say 生成语音片段（Tingting 婷婷·中国大陆女声）；
  2. 按每段在视频里的"起始秒"把语音放到时间轴对应位置（用 adelay 延迟）；
  3. 所有语音轨叠加成一条完整音轨，再合进原视频。

用法：python3 add_voice.py
产出：recording/RiskCopilot-demo-voiced.mp4
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
REC = HERE / "recording"
VIDEO_IN = REC / "RiskCopilot-demo.mp4"
VIDEO_OUT = REC / "RiskCopilot-demo-voiced.mp4"
NARRATION_JSON = REC / "narration.json"   # 由 record_demo.py 落盘的真实时间轴
TMP = REC / "_voice_tmp"
VOICE = "Tingting"     # 中国大陆标准女声
RATE = 180             # 语速（字/分，适中偏稳）

# 口语化配音文案，顺序与 record_demo.py 里各镜头 _say(...) 一一对应（共 23 段）。
# 起始秒不再写死——改为读 narration.json 里录制时记录的真实时间点，杜绝错位。
VOICE_LINES = [
    "RiskCopilot，站在独立第三方审计视角，只用公开信息判断上市公司真伪。",
    "第一，经典案例康美药业。五个职能Agent协同，跑通完整研判流水线。",
    "风险维度矩阵，多引擎证据一眼可读，每一格判据都可溯源、可复现。",
    "确定性财务信号读真实年报，逐条可审计，无大模型参与。",
    "处置追责测算，法条库官方原文坐实，测算判罚与投资者索赔。",
    "数据溯源交叉核验，每条结论都追到公开一手来源，不编造。",
    "第二，一句话提任务。用户只说自然语言，Agent自己理解意图、拆解、执行。",
    "Agent复述它理解的任务，自动对比两家公司，给出谁更干净的结论。",
    "第三，快查任意A股。输入代码，实时拉取真实年报当场计算。以贵州茅台为例。",
    "茅台矩阵全绿，信号不误报。有区分度，才敢用在真实尽调。",
    "第四，深度研判。会读公告、会推理的Agent副驾，直击赛道二资料理解。",
    "Planner自主规划，不走写死的流水线，按风险局面自主决定调用哪些能力。",
    "Agent执行轨迹，每一步可解释、可审计。",
    "定性红旗，读公告提炼数字看不见的信号，每条带溯源链接、不编造。",
    "研判综述融合定量信号、图谱结构和定性红旗，有推理链、标来源。",
    "可操作核查清单，不止报风险，还给下一步怎么查，拿了能干活。",
    "第五，多Agent控辩互搏。从单Agent调工具，升级为名副其实的多Agent协同。",
    "三个立场对立的Agent，就同一套硬证据展开对抗。",
    "控方站最审慎的审计视角，尽力提出结构性风险指控。",
    "辩方为每条指控找合理经营解释。辩方越强，系统越不会误报。",
    "裁判逐条裁定成立、削弱或存疑。硬事实不可辩，也不得辩没。",
    "对抗后收敛出最终裁定。只做风险提示，不替代机构与司法决策。",
    "信号库、法条库、编排全部可复现、可开源。把审计的较真，变成AI能规模化的能力。",
]


def _load_script():
    """把口语文案绑定到 narration.json 记录的真实时间点，返回 [(start, text)]。"""
    if not NARRATION_JSON.exists():
        raise SystemExit(f"✗ 找不到 {NARRATION_JSON.name}（先跑 record_demo.py 生成时间轴）")
    times = [seg["t"] for seg in json.loads(NARRATION_JSON.read_text(encoding="utf-8"))]
    if len(times) != len(VOICE_LINES):
        raise SystemExit(
            f"✗ 时间点数({len(times)})与配音文案数({len(VOICE_LINES)})不符——"
            f"改了 record_demo.py 的 _say 序列后，请同步 VOICE_LINES。")
    return list(zip(times, VOICE_LINES))


SCRIPT = None   # 运行时由 _load_script() 填充


def _run(cmd):
    return subprocess.run(cmd, check=True, capture_output=True)


def build():
    if not VIDEO_IN.exists():
        print(f"✗ 找不到视频：{VIDEO_IN}（先跑 record_demo.py）")
        return
    script = _load_script()
    print(f"✓ 已按录制时间轴对齐 {len(script)} 段配音（末段 @{script[-1][0]}s）")
    TMP.mkdir(parents=True, exist_ok=True)

    # 1) 每段旁白 → aiff（say）→ wav（ffmpeg 统一采样率）
    segs = []
    for i, (start, text) in enumerate(script):
        aiff = TMP / f"seg{i:02d}.aiff"
        wav = TMP / f"seg{i:02d}.wav"
        _run(["say", "-v", VOICE, "-r", str(RATE), "-o", str(aiff), text])
        _run(["ffmpeg", "-y", "-i", str(aiff), "-ar", "44100", "-ac", "2", str(wav)])
        segs.append((start, wav))
        print(f"  ✓ 段{i:02d} @{start}s：{text[:20]}…")

    # 2) 用 ffmpeg 把每段按起始秒延迟后叠加成一条音轨
    inputs = []
    for _, wav in segs:
        inputs += ["-i", str(wav)]
    filt = []
    for idx, (start, _) in enumerate(segs):
        ms = int(start * 1000)
        filt.append(f"[{idx}:a]adelay={ms}|{ms}[a{idx}]")
    mix_in = "".join(f"[a{idx}]" for idx in range(len(segs)))
    filt.append(f"{mix_in}amix=inputs={len(segs)}:dropout_transition=0:normalize=0[aout]")
    voice_track = TMP / "voice_track.m4a"
    _run(["ffmpeg", "-y", *inputs, "-filter_complex", ";".join(filt),
          "-map", "[aout]", "-c:a", "aac", "-b:a", "192k", str(voice_track)])
    print("  ✓ 配音轨合成完成")

    # 3) 配音轨合进原视频（视频流不重编码，直接复制）
    _run(["ffmpeg", "-y", "-i", str(VIDEO_IN), "-i", str(voice_track),
          "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac",
          "-shortest", str(VIDEO_OUT)])
    print(f"✓ 带配音视频已生成：{VIDEO_OUT}")


if __name__ == "__main__":
    build()
