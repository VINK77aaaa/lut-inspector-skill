---
name: lut-inspector
description: >-
  Use this skill whenever the user wants to inspect, validate, debug, or fix a .cube 3D LUT file exported from DaVinci Resolve (especially when using third-party DCTL plugins) for in-camera monitoring on Sony VENICE / FX6 / FX3 or other cinema cameras and monitors. Specifically triggers on symptoms like zebra-like banding/stripes, highlight solarization, extreme shadow noise/chroma specks, out-of-range clipping (<0.0 or >1.0), 65-point vs 33-point compatibility, or general LUT quality checks.
---

# LUT Inspector (`lut-inspector`) — 专业影视 LUT 质检与机内安全修复指南

本 Skill 用于对 `.cube`（1D / 3D / 1D+3D Shaper）文件进行数学级质检、可视化伪影定位、DCTL/DaVinci 节点根因排查，并支持生成符合 **Sony VENICE / FX6 / FX3**（及通用机内监视器）规范的安全修复版 `.cube` 副本。

> [!IMPORTANT]
> **Windows 运行环境要求**：在本机 Windows 环境下，请统一使用 **`py`** 命令调用脚本（不要直接调用可能触发 Microsoft Store 占位符的 `python`）。所有脚本仅依赖 `numpy`、`matplotlib` 与标准库，无需 `scipy`。

---

## 核心能力与内置脚本

所有相对路径均以本 `SKILL.md` 所在目录（`<skill_dir>`）为基准：

1. **科学质检与可视化脚本**：[scripts/inspect_lut.py](./scripts/inspect_lut.py)
   - 检测 **斑马纹成因**：`> 1.0` 超白定点溢出回绕、高光/高饱和单调性反转（Solarization）、一阶差分锯齿振荡、3D 雅可比折叠（`det(J) < 0`）、DaVinci 四面体 vs 机内三线性插值分歧。
   - 检测 **极暗部噪点成因**：`< 0.0` 负值下溢闪点、Sony S-Log3 0% 黑电平（`95/1023 ≈ 0.09286`）附近近灰轴色度爆炸（除零奇异点）、局部增益斜率尖峰，并运行 **64×64 传感器底噪蒙特卡洛仿真**量化噪声放大倍数。
   - 自动产出：JSON 报告、Markdown 报告、6 宫格科学诊断图（`*_diagnostics.png`）、合成压力测试与伪影定位热力图（`*_stress_test.png`）。
2. **机内安全修复脚本**：[scripts/sanitize_lut.py](./scripts/sanitize_lut.py)
   - 修补 `NaN`/`Inf`、将 1D+3D 烘焙合并为纯 3D LUT、高精度四面体重采样为 33 点、严格 `[0.0, 1.0]` 钳位、可选 `--tame-shadow-chroma`（镇定极暗部彩噪）与 `--de-oscillate`（保边阻尼去斑马纹），导出纯 ASCII 无 BOM 副本（绝不覆盖原文件）。
3. **测试集生成器**：[scripts/generate_test_luts.py](./scripts/generate_test_luts.py)
   - 快速生成标准健康样本与复刻 DCTL 典型缺陷（斑马纹 + 暗部彩噪）的 33 点 `.cube` 样本，用于回归验证。
4. **按需查阅的参考知识库**：
   - Sony 机内规格与 S-Log3 黑位锚点原理：[references/sony_cinealta_lut_guide.md](./references/sony_cinealta_lut_guide.md)
   - DaVinci Resolve 第三方 DCTL 常见伪影与代码/节点排查手册：[references/dctl_artifact_root_causes.md](./references/dctl_artifact_root_causes.md)

---

## 标准执行工作流 (SOP)

当用户提供一个或多个 `.cube` 文件并要求检查、排查斑马纹/暗部噪点或准备上机监看时，按以下顺序执行：

### 步骤 1：运行科学质检脚本

根据用户使用的摄影机与 Log 空间选择 `--profile`（常用 Sony 机型默认 `slog3`）和 `--target`（默认为 `sony_cinealta`）：

```powershell
py "<skill_dir>/scripts/inspect_lut.py" "<path_to_lut.cube>" --profile slog3 --target sony_cinealta --out-dir "<output_dir>"
```

常用 `--profile` 选项：`slog3`（默认）、`logc3`、`logc4`、`vlog`、`log3g10`、`clog3`、`davinci_intermediate`、`acescct`、`rec709`、`generic_log`。  
常用 `--target` 选项：`sony_cinealta`（VENICE / FX6 / FX3，默认）、`generic_camera`（Atomos/SmallHD/其他机内）、`post`（仅后期达芬奇内部使用）。

### 步骤 2：检视可视化诊断图与报告

1. 用 `read_file` 读取生成的 `<stem>_report.json`（或 `<stem>_report.md`）获取全部定量发现。
2. **务必用 `read_file` 直接查看生成的两张高分辨率图片**：
   - `<output_dir>/<stem>_diagnostics.png`（检查灰阶曲线、斜率负值区、极暗部放大镜色度尖峰、黑位底噪块直观对比）
   - `<output_dir>/<stem>_stress_test.png`（直观确认条纹与暗部彩噪定位在哪个亮度/色相区域）
3. 如用户遇到具体 DCTL 故障或 Sony 机内报错，结合 `references/dctl_artifact_root_causes.md` 和 `references/sony_cinealta_lut_guide.md` 解释根本原因与达芬奇节点树修改建议（而非只丢出一串冷冰冰的数字）。

### 步骤 3：按需生成“机内安全修复版”并复检（闭环验证）

当检测到越界（`< 0.0` 或 `> 1.0`）、`NaN`/`Inf`、65 点/1D+3D 不兼容、暗部色度爆炸或高频锯齿震荡，且用户需要安全版本立即上机监看时：

1. 根据检测发现的症状组合修复开关（绝不修改原文件，输出为新副本）：
   ```powershell
   py "<skill_dir>/scripts/sanitize_lut.py" "<path_to_lut.cube>" --size 33 --profile slog3 --clamp --fix-nan [--tame-shadow-chroma] [--de-oscillate]
   ```
   - 若报告存在 **极暗部近轴色度爆炸**：务必加上 `--tame-shadow-chroma`。
   - 若报告存在 **高频锯齿振荡 / 斑马纹波纹**：可加上 `--de-oscillate`（同时提醒用户：强烈的高光单调性大反转建议仍应回到 DaVinci Resolve 中调低对应 DCTL 强度以从源头消除）。
2. **对生成的新 `.cube` 副本再次运行 `inspect_lut.py`**，确认越界点清零、尺寸为 `33`、暗部底噪放大回归正常且无新引入的问题后，再交付给用户。

---

## 向用户呈现结果原则

- 用通俗准确的影视调色语言总结核心结论：**综合评级与得分**、**为什么会出现斑马纹**、**为什么极暗部会出现噪点/彩噪**、**在 DaVinci Resolve 节点树/DCTL 中如何从源头改好**。
- 如果在向用户汇报时需要展示诊断图，可将 PNG 复制到当前对话 Artifact 目录并在回复中清晰引用（注意遵守工作区与链接规范）。
