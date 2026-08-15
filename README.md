# 图灵哨兵 · RiskCopilot

> 公开市场价值发现与财务舞弊研判的多 Agent 副驾
> GOAI 世界人工智能开源大赛 · 赛道二（无界应用 · AI+金融） ｜ 队名：图灵哨兵

站在独立第三方审计视角，只用公开信息，**正着发现被漏看的真价值，反着识破被粉饰的假价值**。
机器发现错配，人判断机会还是陷阱。

同项目另一条主线（多重视角价值核验、零依赖标准库实现）在
[TuringSentinel-v2](https://github.com/jenniferlincanyang-source/TuringSentinel-v2)。
两个仓库是**并行的不同叙事线，不是新旧替代关系**。

---

## 30 秒跑起来

```bash
git clone https://github.com/jenniferlincanyang-source/TuringSentinel-v1.git
cd TuringSentinel-v1
python3 -m pip install -r requirements.txt
python3 verify_all.py          # 先跑这个：18 项断言，零网络零 LLM，约 5 秒
```

想看交互界面，macOS 下双击 `▶︎ 双击启动Demo.command`（自检环境 → 起后端 → 自动开页面）；
其他平台：

```bash
cd demo && python3 backend.py    # 后端跑 127.0.0.1:8199
# 浏览器打开 demo/RiskCopilot-demo.html
```

### LLM 凭据（可选）

深度研判 / 控辩互搏 / 总控编排三个功能要调大模型。凭据**只从环境变量读，代码里不硬编码**：

```bash
export ANTHROPIC_BASE_URL="<兼容 /v1/messages 的接口地址>"
export ANTHROPIC_AUTH_TOKEN="<你的密钥>"
```

**不配也能跑**——`llm.py` 返回 None，这三个功能自动降级为确定性规则版，页面不崩、结论仍在，
只是没有 LLM 生成的推理叙述。快查 / 信号引擎 / 追责测算 / 价值发现错配扫描
**本来就不需要 LLM**，是纯确定性的。

---

## 先跑 `verify_all.py`

它不产生任何判断，只调用被验对象并断言其输出——是尺子，不是选手。

- **离线段（18 项）**：信号引擎在康美真实年报上的判定、法条库四要素完整性、价值发现规则的
  两次运行一致性、全仓密钥扫描、合规声明、材料完整性。这段在任何机器上结果都该一致。
- **在线段（`--online`，需后端已启动）**：真实取数链路 + 不误报测试 + 价值发现接口。

### 两个可以当场验的判定

| 标的 | 系统判定 | 为什么这条值得看 |
|---|---|---|
| 康美药业 600518 | 2017 年 **极高（10 分）**，6 条判据命中 | 存贷双高：账上货币资金 341.5 亿却背有息负债 221.8 亿；货币资金/净资产 106.6%，已超净资产本身 |
| 贵州茅台 600519 | 4 个年度全部 **无（0 分）**，0 条判据命中 | **不误报**才是能用的前提——把所有公司都判高危的系统没有价值 |

7 条信号在康美最差那年是：触发 2 条、未触发 3 条、**数据缺失 2 条记 na 不记命中**。
缺数据就说缺数据，不硬凑分。

---

## 五个交互入口

| 入口 | 干什么 | 要 LLM 吗 |
|---|---|---|
| 经典案例 | 康美/茅台/格力跑 5-Agent 流水线 + 风险矩阵 | 否 |
| 一句话提任务 | 自然语言 → 意图理解 → 自动拆解 → 按风险分级编排 | 是（可降级） |
| 快查任意 A 股 | 输 6 位代码，实时拉真实年报当场算 | 否 |
| 深度研判 / 控辩互搏 | 读公告提定性红旗 + 控/辩/裁三方对抗收敛 | 是（可降级） |
| 价值发现 | 链热单体冷错配扫描 → 归属核验 → 兜底排雷 | 部分 |

**建议先试"快查"**：随便输一个代码（如 `600519` 茅台），看它敢不敢在正常公司身上保持全绿。

---

## 目录

```
demo/                       Agent 与后端
├── backend.py              FastAPI 后端，12 个接口，仅绑 127.0.0.1:8199
├── RiskCopilot-demo.html   前端，单文件无构建
├── agent_intent.py         意图理解：自然语言 → 结构化意图
├── agent_orchestrator.py   总控编排：按风险分级自主升级（清白止步 / 高危深挖）
├── agent_deepdive.py       深度研判：ReAct 闭环（规划 → 读公告 → LLM 研判）
├── agent_debate.py         控辩互搏：控方 / 辩方 / 裁判三 Agent 对抗收敛
├── agent_attribution.py    归属核验：治"题材联想"陷阱，复用控辩骨架
├── value_engine.py         价值发现引擎：链热单体冷错配扫描（纯确定性）
├── value_scan.py           价值发现四步闭环，可单独跑
├── materials.py            资料层：公告标题+链接、个股新闻（只取可溯源的）
├── llm.py                  LLM 薄封装，环境变量读凭据，失败一律降级 None
├── track_frontier.py       前沿追踪 Agent：LLM 判断新论文是支持还是挑战本方案
└── record_demo.py          自动录屏脚本（Playwright）

signals/                    确定性信号库（唯一真相源，backend 直接 import）
├── run.py                  python3 run.py 一键复现
├── engine.py               信号引擎（注册 / 评估 / 汇总）
├── signals_registry.py     7 条信号定义，@register 加新的照葫芦画瓢
└── data/kangmei.json       康美年报真实数字，逐页核对、标注页码

law/                        法条库与追责测算
├── penalty_calc.py         python3 penalty_calc.py 一键复现
└── data/                   证券责任法条库 + 康美案情

docs/                       方案与方法论（01 方案设计 / 02 Agent 架构 / 03 舞弊方法论
                            / 04 信号哲学 / 05 文献综述 / 06 研究计划）
verify_all.py               一键自检
DEPENDENCIES.md             第三方依赖与许可证完整披露
```

三个引擎都能单独跑，都不需要网络和 LLM：

```bash
cd signals && python3 run.py                    # 7 条信号跑康美真实年报
cd law     && python3 penalty_calc.py           # 追责测算，每条法条援引回查校验
cd demo    && python3 value_scan.py --no-attr    # 价值发现四步闭环骨架
```

---

## 这个仓库不含什么（以及为什么）

| 未包含 | 原因 | 想要怎么办 |
|---|---|---|
| `demo/gnn_scores.json` | FiGraph 衍生数据（3815 家公司舞弊概率）。FiGraph 条款为**仅限参赛/研究用途、不得商用**，而本仓库是 Apache-2.0（允许商用）——**不能用本仓库的协议去转授一份我并不持有的权利** | 向 FiGraph 原作者申请数据集，自行用 GraphSAGE 导出。缺这个文件 backend 有 try/except 兜底，只是 GNN 一栏留空，其余功能不受影响 |
| `docs/papers/*.pdf` | 学术论文原文，其中 NBER working paper 有再分发限制 | 见 `docs/05-文献综述.md` 的文献表，按 arXiv / NBER 编号自取 |
| 法规原文 docx | 体积 + 再分发考虑。法条援引已标法名 + 条号 + 生效日 | 在国家法律法规数据库按条号自行核对 |
| Demo 视频 / PPT | 19MB + 4MB 二进制，不适合进 git | 见大赛提交材料 |

---

## 合规边界（诚实交付，请一并看）

- **数据**：只用公开信息——上市公司已披露年报、巨潮公告标题+链接、证监会已公开的行政处罚
  决定书。不接任何私有、内部或个人数据。
- **结论口径**：只输出"风险存疑"提示与"值得人工深看的候选"，**不下"该公司造假"的定性结论**
  （那是监管与司法权限），**不预测涨跌、不给买入建议、不给估值数字**。
- **硬信号是 ground truth**：LLM 不得推翻确定性信号命中的事实，只能补充定性证据与解释。
  控辩互搏里裁判裁定的是"这些事实是否足以构成指控"，不是"事实是否存在"。
- **服务安全**：`backend.py` 仅绑定 127.0.0.1、**无身份认证**，只适合本地演示。
  真实部署必须加认证、审计日志、TLS、字段级鉴权。**不要把它直接暴露到公网**——
  深度研判会消耗你环境变量里的 LLM 凭据。
- **已知短板（不粉饰）**：
  - 价值发现的取数层还是 `demo/data/value_sample*.json` 样例（**判据逻辑本身确定性、
    可复现**，接真实板块成员 + 行情 + 估值是后续 TODO）。
  - 价值发现的"有效性"尚未被决策日志验证，不敢说"已验证有效"。
  - 反舞弊底座是硬的：康美真实年报验证、茅台不误报。

---

## 许可

代码与自写文档采用 [Apache-2.0](LICENSE)。

第三方依赖（AkShare / FastAPI / Uvicorn / pandas 等）与数据集（FiGraph）的许可与使用条款，
见 [DEPENDENCIES.md](DEPENDENCIES.md)。若复用 FiGraph 或 GraphSAGE，请引用其原论文。
