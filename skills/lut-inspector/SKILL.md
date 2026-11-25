---
name: lut-inspector
description: >-
  Use this skill whenever the user wants to inspect, validate, debug, or fix a .cube 3D LUT file exported from DaVinci Resolve (especially when using third-party DCTL plugins) for in-camera monitoring on Sony VENICE / FX6 / FX3 or other cinema cameras and monitors. Specifically triggers on symptoms like zebra-like banding/stripes, highlight solarization, extreme shadow noise/chroma specks, out-of-range clipping (<0.0 or >1.0), highlight roll-off vs gamut projection checks, 65-point vs 33-point compatibility, or general LUT technical QC.
---

# LUT Inspector (`lut-inspector`) — 影视 LUT 客观工程质检与安全修复规范

本 Skill 用于对 `.cube`（1D / 3D / 1D+3D Shaper）文件进行物理色彩空间下的工程级数学质检、高光滚降评估、可视化伪影定位及机内安全规范化修复（主要面向 **Sony VENICE / FX6 / FX3** 及通用摄影机/监视器）。

> [!CAUTION]
> **【核心职责边界与防误判铁律 — 所有执行模型必须严格遵守】**
> 1. **只做技术质检 (Technical QC)，严禁越界“教用户调色”**：
>    - **绝对禁止**对用户的创意 Look 风格进行审美评价或瞎指挥修改（例如：18% 中灰偏暗/偏亮半档、胶片柔白峰值停在 `0.98~0.995`、低饱和区提饱和、整体暖调/青橙风格等，全都是调色师有意为之的 Look，**不是错误**）。
>    - 若脚本结果 `status == "PASS"` 且无异常告警，**直接给出合格结论与客观数据即可，不要输出“从源头怎么调色/改节点”的说教段落**。只有当确实检出客观数学故障，或用户**明确追问**“为什么会这样/怎么在达芬奇改”时，才结合报告中的 `technical_note` 与 `references/` 说明技术原因。
> 2. **严禁将“宽色域外壁投影”误判为“高光没有滚降”**：
>    - 必须直接采信 `inspect_lut.py` 给出的 `highlight_rolloff` 结论（包含 `rolloff_status`、末端肩部斜率 `slope_shoulder_end`、全白削波 `achromatic_white_clip_pct` 与宽色域外壁投影 `gamut_wall_projection_pct`）。
>    - 在宽色域（如 `S-Gamut3.Cine`）转 `Rec.709` 的 LUT 中，大量输入格点天然落在 Rec.709 之外，色域映射会使其**单通道**触及 `1.0`（其余通道 `< 1.0`），即单通道满幅占比常达 `50%~70%`。这属于**正常的色域边界投影压缩**，绝不等于“高光没有滚降”！真正有无高光滚降看**中性灰轴肩部斜率衰减**与**三通道全白平顶位置**。
> 3. **严禁脱离物理色彩空间手写简化脚本覆写检测结论**：
>    - 严禁自行用 `t * [1.15, 0.65, 0.15]` 在 Log 空间乘系数冒充曝光渐变，也严禁把 `S-Log3 / S-Gamut3.Cine` 下的输入/输出坐标直接套进 sRGB 色相角公式“推断偏色”。`inspect_lut.py` V2.0 已内置场景线性相机曝光扫描与色域矩阵拆分，一切数值请直接引用 `inspect_lut.py` 与 `<stem>_report.json` 的结果。

> [!IMPORTANT]
> **Windows 运行环境要求**：在本机 Windows 环境下，请统一使用 **`py`** 命令调用脚本（不要调用可能触发 Microsoft Store 占位符的 `python`）。所有脚本仅依赖 `numpy`、`matplotlib`、`Pillow` 与标准库，无需 `scipy`。

---

## 内置脚本与知识库

所有相对路径均以本 `SKILL.md` 所在目录（`<skill_dir>`）为基准：

1. **核心工程质检与可视化脚本**：[scripts/inspect_lut.py](./scripts/inspect_lut.py)
   - **机内硬件规范与越界检查**：检查晶格尺寸（Sony 标准 33 点）、1D+3D 混合 Shaper、`DOMAIN`、`NaN`/`Inf`、`< 0.0` 下溢闪点与 `> 1.0` 超白定点回绕。
   - **高光滚降与锚点分析 (`check_highlight_rolloff`)**：精准区分中性灰肩部滚降衰减、三通道死白硬削波与宽色域外壁投影压缩。
   - **斑马纹与单调性检查**：基于场景线性相机曝光扫描（`-7.0 EV ~ +6.5 EV`）检查亮度反转（Solarization）与晶格级锯齿振荡，以及核心有效色域内部三维雅可比折叠（`det(J) < 0`）和插值分歧。
   - **极暗部噪点专项检查**：紧盯黑电平（如 Sony S-Log3 `95/1023 ≈ 0.092864`）附近斜率尖峰、近灰轴除零色度爆炸，并运行 64×64 传感器底噪蒙特卡洛仿真。
2. **机内安全修复脚本**：[scripts/sanitize_lut.py](./scripts/sanitize_lut.py)
   - 修补 `NaN`/`Inf`、1D+3D 烘焙合并、四面体重采样转 33 点、严格 `[0.0, 1.0]` 钳位、可选 `--tame-shadow-chroma` 与 `--de-oscillate`，导出纯 ASCII 无 BOM 副本（绝不覆盖原文件）。
3. **测试样本生成器**：[scripts/generate_test_luts.py](./scripts/generate_test_luts.py)
4. **按需查阅参考手册**（仅在需要深入排查 DCTL/机内故障时阅读）：
   - Sony 机内规格与 S-Log3 黑位锚点原理：[references/sony_cinealta_lut_guide.md](./references/sony_cinealta_lut_guide.md)
   - DCTL 常见伪影技术成因手册：[references/dctl_artifact_root_causes.md](./references/dctl_artifact_root_causes.md)

---

## 标准执行流程 (SOP)

### 步骤 1：运行科学质检脚本

根据素材 Log 空间选择 `--profile`（Sony 默认 `slog3`）和 `--target`（默认 `sony_cinealta`）：

```powershell
py "<skill_dir>/scripts/inspect_lut.py" "<path_to_lut.cube>" --profile slog3 --target sony_cinealta --out-dir "<output_dir>"
```

常用 `--profile`：`slog3`（默认）、`logc3`、`logc4`、`vlog`、`log3g10`、`clog3`、`davinci_intermediate`、`acescct`、`rec709`、`generic_log`。  
常用 `--target`：`sony_cinealta`（VENICE / FX6 / FX3，默认）、`generic_camera`（监视器/其他机型）、`post`（仅后期达芬奇内部）。

### 步骤 2：阅读报告并检视 PNG 诊断图

1. 使用文件读取工具读取 `<output_dir>/<stem>_report.json`（或 `<stem>_report.md`）。
2. **亲自用视觉读取工具检视生成的两张图片**，核对曲线趋势与热力图位置：
   - `<output_dir>/<stem>_diagnostics.png`
   - `<output_dir>/<stem>_stress_test.png`

### 步骤 3：按规范输出简洁客观的质检答复

按以下结构答复用户（简洁清晰，不添油加醋）：
1. **核心质检结论**：`status`（`PASS` / `WARN` / `FAIL`）与健康分，一句话说明是否可直接上机监看。
2. **高光滚降与影调锚点特征**：直接汇报 `rolloff_status`（如平滑肩部滚降 / 硬削波）、末端斜率、死白削波占比与色域外壁投影占比，**客观陈述** 18% 灰及白点输出值而不评价好坏。
3. **极暗部与斑马纹隐患核查**：汇报值域范围、`min/max` 越界点数、物理曝光反转数、核心区三维折叠数与黑电平底噪放大倍率。
4. **仅在发现问题或用户需要安全修复版时**：列出异常项的技术证据，并询问或按需执行 `sanitize_lut.py` 生成 `_sony_safe_33.cube` 副本后再次复检交付。
