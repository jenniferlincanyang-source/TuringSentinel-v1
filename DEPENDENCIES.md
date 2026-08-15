# 依赖披露清单 DEPENDENCIES

> 本文件由 `tools/dep_audit.py` 自动复核生成，最后复核：2026-08-07
> 用途：满足 GOAI 大赛第三方依赖/许可证完整披露要求（漏报=披露不实，可致扣分或除名）。

## ✅ 复核结论：账实相符，无未申报依赖

## 已申报依赖清单

### 数据集

| 名称 | 来源 | 许可证 | 用途 | 披露要点 |
|---|---|---|---|---|
| FiGraph | 西安交通大学团队, The Web Conference 2025 | 学术公开数据集(需核对官方使用条款) | A股上市公司关系图,GNN舞弊检测训练/评测 | 仅限参赛/研究用途,不得商用;官方声明违规使用可追责;需在作品中引用原论文 |

### 模型与算法

| 名称 | 来源 | 许可证 | 用途 | 披露要点 |
|---|---|---|---|---|
| GraphSAGE | Hamilton et al. 2017 原论文 | 学术方法(算法思想) | GNN基线模型 | 引用原论文,说明基于其做舞弊检测基线 |

### 代码框架与库

| 名称 | 来源 | 许可证 | 用途 | 披露要点 |
|---|---|---|---|---|
| PyTorch | Meta | BSD-3-Clause | 深度学习框架 | - |
| PyTorch Geometric (torch_geometric) | PyG 团队 | MIT | 图神经网络库 | - |
| scikit-learn | scikit-learn 社区 | BSD-3-Clause | 评估指标(AUC/AP/F1) | - |
| NumPy | NumPy 社区 | BSD-3-Clause | 数值计算 | - |
| pandas | pandas 社区 | BSD-3-Clause | 数据加载与处理 | - |
| AkShare | akshare 开源社区 (akfamily/akshare) | MIT | demo 后端实时拉取A股真实年报、巨潮公告标题+链接、东财个股新闻(公开财经数据) | 数据源为东方财富/巨潮资讯等公开渠道,仅取公开可溯源信息;接口偶发波动时自动降级 |
| FastAPI | FastAPI 社区 (tiangolo) | MIT | demo 后端 Web 框架(/api/analyze、/api/deepdive 接口) | - |
| Uvicorn | encode 团队 | BSD-3-Clause | ASGI 服务器,承载 FastAPI 后端 | - |
| Matplotlib | Matplotlib 社区 | PSF-based (BSD兼容) | 赛道三 virtualcell 探索脚本的结果可视化绘图 | - |
| Playwright | Microsoft (microsoft/playwright-python) | Apache-2.0 | demo 录制脚本(demo/record_demo.py)的浏览器自动化,自动点按 RiskCopilot 页面录屏 | 仅用于本地录制演示视频,不参与线上推理与数据处理;不涉及用户数据采集 |

### 平台与Skill

| 名称 | 来源 | 许可证 | 用途 | 披露要点 |
|---|---|---|---|---|
| DianJin-SKILLS (百技图) | 阿里云 (aliyun/qwen-dianjin) | 待核对仓库LICENSE | 拟复用作通用金融Skill底座(如复用) | 复用哪些Skill、遵守其协议、署名 |

### 商业API与外部服务

| 名称 | 来源 | 许可证 | 用途 | 披露要点 |
|---|---|---|---|---|
| 大模型 API (Claude 兼容接口) | 经 ANTHROPIC_BASE_URL / ANTHROPIC_AUTH_TOKEN 环境变量注入,不硬编码 key | 商业API条款 | 深度研判(/api/deepdive)：资料理解层从公告标题提取定性红旗(A) + 融合定量/GNN/定性生成研判综述(B) | ①仅在'深度研判'环节调用,'快查'路径无需大模型;②只发送公开财报数字+公告标题,不含隐私数据;③无凭据/超时自动降级为规则版,功能不锁定单一厂商;④接口层与模型解耦(llm.py),可替换为 Qwen/DeepSeek 等,规避锁定与成本风险 |

## 附：扫描到的第三方 import（真实使用证据）

- `akshare` → akshare（待核对）
- `fastapi` → fastapi（待核对）
- `numpy` → NumPy（BSD-3-Clause）
- `pandas` → pandas（BSD-3-Clause）
- `playwright` → playwright（待核对）
- `sklearn` → scikit-learn（BSD-3-Clause）
- `torch` → PyTorch（BSD-3-Clause）
- `torch_geometric` → PyTorch Geometric（MIT）
- `uvicorn` → uvicorn（待核对）
