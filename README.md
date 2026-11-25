<div align="center">

<img src="assets/banner.svg" alt="LUT Inspector Banner" width="100%" />

<br />

[![Python](https://img.shields.io/badge/Python-3.10%2B-3776ab?style=for-the-badge&logo=python&logoColor=white)](requirements.txt)
[![DaVinci Resolve DCTL](https://img.shields.io/badge/DaVinci%20Resolve-DCTL%20.cube%20QA-2563eb?style=for-the-badge)](#为什么需要-lut-inspector)
[![Sony Cinema Line](https://img.shields.io/badge/Sony%20CineAlta-VENICE%20%7C%20FX6%20%7C%20FX3-0f172a?style=for-the-badge)](#sony-venice--fx6--fx3-导出避坑-5-秒自查)
[![33-pt Safe Cube](https://img.shields.io/badge/3D%20LUT-33--pt%20ASCII%20Safe-059669?style=for-the-badge)](#命令行独立运行)
[![License: MIT](https://img.shields.io/badge/License-MIT-d97706?style=for-the-badge)](LICENSE)

<p align="center">
  <strong>专为 DaVinci Resolve + 第三方 DCTL 与 Sony VENICE / FX6 / FX3 机内监看打造的 3D LUT 科学质检与安全修复 Skill</strong><br />
  提前拦截机内 <strong>“斑马纹溢出回绕”</strong> 与 <strong>S-Log3 极暗部彩噪爆炸</strong> · 6 宫格科学诊断图 + 压力热力图 · 一键 33 点安全重采样
</p>

[痛点解析](#为什么需要-lut-inspector) ·
[原理可视化](#底层原理可视化) ·
[图表与实测对比](#实测诊断图表详解) ·
[架构与工作流](#架构与闭环工作流) ·
[快速开始](#安装与快速开始) ·
[Log 曲线预设](#支持的-log-曲线与目标环境) ·
[导出避坑自查](#sony-venice--fx6--fx3-导出避坑-5-秒自查)

</div>

---

## 为什么需要 LUT Inspector？

很多调色师和 DIT 都遇过同一个现场噩梦：**LUT 在 DaVinci Resolve 监视器里完美无瑕，一导进 Sony VENICE / FX6 / FX3 机内监看，高光就出现类似斑马纹的黑白/补色条纹，极暗部更是炸满蠕动彩噪。**

根源在于 **Resolve 32-bit 浮点视口** 与 **摄影机机内定点 FPGA 管线** 存在巨大硬件差异：

| 现场常见伪影 | DaVinci Resolve 32-bit 浮点视口 | Sony 机内定点 FPGA / ASIC 硬件 | `lut-inspector` 提前拦截手段 |
| :--- | :--- | :--- | :--- |
| **高光类似“斑马纹”条纹** | `> 1.0` 超白值被视口自然裁切为纯白，肉眼完全察觉不到异常 | 浮点 `1.05` 在无符号定点管线发生 **溢出回绕 (Wrap-around)** 跌成 `0.05` 纯黑条带 | 全晶格扫描 `> 1.0` 越界比例与坐标，并在压力测试卡左图直观复现溢出回绕带 |
| **渐变等高线 / 反相条带** | 局部极高对比或锐度在静态画面中不易察觉 | **单调性反转 (`dY/dx < 0`)** 与 **高频锯齿振荡** 导致平滑渐变裂出明暗交替条纹 | 沿中性灰与饱和色斜坡计算一阶导数与正负翻转次数，精准标出反转输入区间 |
| **网状撕裂 / 阶梯断层** | 默认 **Tetrahedral（四面体）插值** 平滑过渡掩盖局部折叠 | 机内常用 **Trilinear（三线性）插值**，且晶格 **雅可比 `det(J) < 0`** 会使插值路径折返 | 计算全体素归一化雅可比行列式 + 逐体素中心四面体 vs 三线性 `ΔRGB` 分歧曲线 |
| **极暗部颗粒 / 蠕动彩噪** | 16/32-bit 下微弱黑位偏差不易放大 | S-Log3 0% 黑位 (`95/1023 ≈ 0.0929`) 紧贴 33 点第 3 层，DCTL 除零 (`1/(L+ε)`) 与 `< 0.0` 下溢引发彩噪爆发 | 紧盯黑电平邻域测算近灰轴色度增益，并运行 **64×64 传感器底噪蒙特卡洛仿真** |
| **摄影机拒载 / 曲线错位** | Resolve 内不校验文件头规范 | 65 点体积、1D+3D Shaper、非 `[0,1]` Domain、UTF-8 BOM 或中文 `TITLE` 均会导致读卡失败 | 强制检查硬件规格头信息；`sanitize_lut.py` 自动烘焙、33 点四面体重采样并输出纯 ASCII 无 BOM |

---

## 底层原理可视化

<div align="center">
  <img src="assets/slog3_anchor_and_artifacts.svg" alt="斑马纹溢出回绕与 S-Log3 黑位共振机制" width="100%" />
</div>

### Sony S-Log3 10-bit 关键锚点与 33 点晶格映射关系

在 10-bit Data Range (`0 ~ 1023`) 映射至 `[0.0, 1.0]` 时，Sony S-Log3 的黑电平与 33 点 3D LUT 晶格节点存在物理级贴合：

| 物理意义 | 场景反射率 | 10-bit 码值 (`0–1023`) | 归一化输入 (`0.0–1.0`) | 对应 33 点晶格层 (`i / 32`) | 常见风险说明 |
| :--- | :---: | :---: | :---: | :---: | :--- |
| **Sub-Black（超黑/热噪区）** | `< 0%` | `< 95` | `< 0.092864` | 第 `0, 1, 2` 层 (`0.000, 0.03125, 0.06250`) | 色域矩阵或负 Lift 产生 `< 0.0` 负值，机内下溢成高亮闪点 |
| **0% Black（物理黑电平）** | `0%` | **`95`** | **`0.092864`** | **紧贴第 `3` 层 (`3/32 = 0.09375`)** | 传感器暗电流热噪以 `0.0929` 为中心抖动，极易撞击 DCTL 除零奇异点 |
| **暗部阴影关键带** | `~2% – 5%` | `140 – 220` | `0.137 – 0.215` | 第 `4 ~ 7` 层 | 过陡 TOE 对比度或重复挂两层 Log→709 会令亮度噪点放大数十倍 |
| **18% 中灰 (Mid Gray)** | `18%` | **`420`** | **`0.410557`** | 第 `13` 层 (`0.40625`) | 决定整体曝光归位基准 |
| **90% 白 (Diffuse White)** | `90%` | **`598`** | **`0.584555`** | 第 `19` 层 (`0.59375`) | 之上至 `1.0` 为高光滚降区，DCTL 最易超 `1.0` 或发生单调反转 |

---

## 实测诊断图表详解

仓库内置 [`generate_test_luts.py`](skills/lut-inspector/scripts/generate_test_luts.py) 与完整自检报告示例（位于 [`examples/sample_report/`](examples/sample_report/)），下面展示 **健康基准 LUT**、**典型缺陷 DCTL LUT** 与 **应急修复版 LUT** 的真实运行对照。

### 三组实测样本量化指标对比

| 检验维度 | 健康基准 `clean_slog3_to_rec709_33.cube` | 故障 DCTL `broken_dctl_sony_zebra_shadow_33.cube` | 应急修复版 `..._sony_safe_33.cube` | 安全参考基准 |
| :--- | :---: | :---: | :---: | :---: |
| **综合评级与健康分** | `PASS` (**100 / 100**) | `FAIL` (**0 / 100** · 9 严重) | `FAIL` (**0 / 100** · 5 严重 1 警告)* | `PASS` |
| **值域范围 (`min ~ max`)** | `7.4e-05 ~ 0.946347` | `-0.366533 ~ 1.051691` | **`0.0 ~ 1.0`** | 严格 `[0.0, 1.0]` |
| **越界比例 (`<0.0` / `>1.0`)** | `0.0%` / `0.0%` | `0.37%` / `2.482%` | **`0.0%` / `0.0%`** | `0.0%` / `0.0%` |
| **斑马纹风险 (反转 / 振荡)** | `0` / `0` | `4` / `3` | `4` / `2` | `0` / `0` |
| **三维晶格折叠 `det(J)<0`** | `0.0%` | `5.5206%` (`min=-198.1027`) | `5.0659%` (`min=-1.0671`) | `0.0%` |
| **近灰轴色度爆炸 (最大增益 / 格数)** | `1.115x` (`0` 格) | `12.22x` (`42` 格) | **`3.457x` (`2` 格)** | `< 3.0x` (`0` 格) |
| **64×64 黑位底噪放大 (亮度 / 色度)** | `1.06x` / `1.05x` | `215.69x` / `266.45x` | **`4.09x` / `4.05x`** | `< 2.2x` / `< 2.4x` |
| **四面体 vs 三线性最大分歧 `ΔRGB`** | `0.02431` | `0.20863` | `0.08504` | `< 0.045` |
| **晶格最大邻域跃迁 `ΔRGB`** | `0.1871` | `0.5593` | **`0.2267`** | `< 0.35` |

> \* 注：故障示例刻意注入了大幅高光正弦波纹与大曲率通道反转；`sanitize_lut.py` 一键清零了全部 `<0.0 / >1.0` 越界闪点并将极暗部底噪由 **266.45x** 压降 65 倍至 **4.05x**，而剧烈的结构性 Solarization 仍需按报告提示回到 DaVinci Resolve 中调低对应 DCTL 参数以从源头根治。

---

### 1. 6 宫格科学诊断面板 (`*_diagnostics.png`)

<table>
  <tr>
    <th width="50%">🔴 故障 DCTL LUT（FAIL · 0/100）</th>
    <th width="50%">🟢 健康基准 LUT（PASS · 100/100）</th>
  </tr>
  <tr>
    <td><img src="examples/sample_report/broken_dctl_sony_zebra_shadow_33_diagnostics.png" alt="故障 DCTL 6宫格科学诊断面板" width="100%" /></td>
    <td><img src="examples/sample_report/clean_slog3_to_rec709_33_diagnostics.png" alt="健康基准 6宫格科学诊断面板" width="100%" /></td>
  </tr>
</table>

<details>
<summary><strong>📖 点击展开：6 宫格各子图快速导读说明</strong></summary>

1. **灰阶 RGB / 亮度曲线与安全边界**：叠加黑位 (`0.093`) 与 18% 灰 (`0.411`) 竖线；**黄点**标出 `>1.0` 超白，**蓝点**标出 `<0.0` 负值。
2. **灰阶一阶增益斜率 (`dY/dx`) 与反转警戒区**：曲线一旦跌入下方红色 `< 0` 区域即发生 **单调性反转**，剧烈尖峰震荡则对应高频斑马条纹。
3. **极暗部放大镜 `[0.0–0.22]` 与近轴色度爆炸**：紧盯 S-Log3 黑位 `0.093` 附近的橙色色差曲线；若在此处猛烈冲高，代表存在 DCTL 除零彩噪缺陷。
4. **黑电平传感器底噪放大仿真 (`64×64` 对比)**：左侧为标准平滑曲线在底噪下的黑场，右侧为当前 LUT 模拟机内效果，直观暴露“彩噪马赛克”。
5. **3D 晶格缺陷体素分布投影（亮度 vs 输入饱和度）**：在亮度–饱和度二维投影上标出超白 (`>1.0`)、负值 (`<0.0`) 与雅可比折叠 (`×` 号) 的集中区域。
6. **插值分歧曲线（四面体 vs 机内三线性）与摘要**：当紫色曲线冲破 `0.045` 虚线时，说明 Resolve 中看似正常的过渡在机内三线性硬件上会裂开色带。

</details>

---

### 2. 合成全动态压力测试卡 & 伪影热力定位图 (`*_stress_test.png`)

左图直接模拟 **Sony 机内三线性插值 + 定点溢出回绕** 的实际成像，右图自动按颜色高亮定位伪影区域（图例：**亮黄** `>1.0` 超白回绕 · **洋红** 单调反转/锯齿斑马纹 · **青色** `<0.0` 负值闪点 · **橙红** 极暗部色度爆炸彩噪）：

<details open>
<summary><strong>🔴 故障 DCTL 修复前：暗部满屏彩噪闪点 + 高光/肤色溢出色带</strong></summary>
<img src="examples/sample_report/broken_dctl_sony_zebra_shadow_33_stress_test.png" alt="故障 DCTL 压力测试卡" width="100%" />
</details>

<details>
<summary><strong>🛠️ 经 sanitize_lut.py 应急修复后：暗部彩噪块与溢出黄青条带已清零，仅留源头结构性反转提示</strong></summary>
<img src="examples/sample_report/broken_dctl_sony_zebra_shadow_33_sony_safe_33_stress_test.png" alt="应急修复版压力测试卡" width="100%" />
</details>

<details>
<summary><strong>🟢 健康基准 LUT：S-Log3 暗部纯净、中性灰与暖肤色过渡自然连续</strong></summary>
<img src="examples/sample_report/clean_slog3_to_rec709_33_stress_test.png" alt="健康基准压力测试卡" width="100%" />
</details>

---

## 架构与闭环工作流

<div align="center">
  <img src="assets/architecture_workflow.svg" alt="LUT Inspector 全流程架构图" width="100%" />
</div>

```mermaid
flowchart LR
    A["DaVinci Resolve + DCTL<br/>导出 .cube (1D/3D/65pt)"] --> B["inspect_lut.py<br/>科学质检与底噪仿真"]
    B --> C{"综合评级 PASS ?"}
    C -- "PASS（零越界/低底噪）" --> F["安全导入 Sony VENICE / FX6 / FX3"]
    C -- "FAIL / WARN" --> D["查看双图 + Markdown 根因报告<br/>回 Resolve 源头调整 DCTL 节点"]
    C -- "现场急需安全监看副本" --> E["sanitize_lut.py<br/>33pt 重采样 + 钳位 + 暗部彩噪镇定"]
    E -->|"强制二次复检"| B
    E -->|"生成不覆盖原文件的副本"| F
```

---

## 安装与快速开始

### 环境依赖

仅需 **Python 3.10+** 与两个常见科学计算库（无需 `scipy`）：

```powershell
git clone https://github.com/VINK77aaaa/lut-inspector-skill.git
cd lut-inspector-skill
pip install -r requirements.txt
```

> [!TIP]
> **Windows 用户提示**：若系统中 `python` 指向 Microsoft Store 占位符，请统一使用 `py` 命令运行脚本。

---

### 方式一：作为 Antigravity / AI Agent Skill 使用

本仓库根目录已配置 [`.agents/skills.json`](.agents/skills.json)，且核心技能包位于 [`skills/lut-inspector/`](skills/lut-inspector/)。

- **当前工作区直接使用**：在工作区中打开 Antigravity 对话，直接发送 `.cube` 文件路径即可自动触发 [`SKILL.md`](skills/lut-inspector/SKILL.md) 标准 SOP。
- **全局安装到任意项目可用**：将 [`skills/lut-inspector/`](skills/lut-inspector/) 复制到全局技能目录 `~/.gemini/config/skills/lut-inspector/`（macOS/Linux）或 `%USERPROFILE%\.gemini\config\skills\lut-inspector\`（Windows）。

示例自然语言指令：
- *“帮我用 lut-inspector 检查这个准备放进索尼 FX6 的 LUT：`D:/LUTs/MyFilmLook.cube`”*
- *“导出的 S-Log3 LUT 在威尼斯机内高光像有斑马纹，暗部噪点也炸了，帮我诊断原因并出一个安全修复版。”*

---

### 方式二：命令行独立运行

#### 1. 深度质检并生成双图与双报告
```powershell
py skills/lut-inspector/scripts/inspect_lut.py "examples/broken_dctl_sony_zebra_shadow_33.cube" --profile slog3 --target sony_cinealta --out-dir "./report_out"
```
常用参数说明：
- `--profile`：输入素材对应的 Log 曲线预设（默认 `slog3`，详见下方支持列表）
- `--target`：目标运行环境（`sony_cinealta` 默认 / `generic_camera` / `post`）
- `--out-dir`：输出 JSON、Markdown、6 宫格诊断图与压力测试图的目录
- `--no-plots`：跳过生成 PNG 图表（适合纯 CI 快速体检）
- `--strict-exit`：若评级为 `FAIL` 则返回退出码 `2`

#### 2. 生成 Sony 机内安全修复版副本（绝不覆盖原文件）
```powershell
py skills/lut-inspector/scripts/sanitize_lut.py "examples/broken_dctl_sony_zebra_shadow_33.cube" --size 33 --profile slog3 --clamp --fix-nan --tame-shadow-chroma --de-oscillate
```
核心修复开关：
- `--size`：输出 3D 晶格尺寸（`17` / `33` / `65` / `keep`，Sony 机内推荐默认 `33`；会自动烘焙合并 1D+3D Shaper）
- `--clamp` / `--fix-nan`：默认开启，执行 `[0.0, 1.0]` 严格钳位与 `NaN`/`Inf` 邻域插补
- `--tame-shadow-chroma`：针对 S-Log3 黑位附近近灰轴色度爆炸做保亮度限幅（有效消除暗部蠕动彩噪）
- `--de-oscillate`：开启保边去振荡阻尼，缓解高光高频锯齿斑马纹
- 输出默认保存为同目录 `*_sony_safe_33.cube`（纯 ASCII、无 BOM、LF 换行、标准 R-fastest 排序）。**修复后请务必再次运行 `inspect_lut.py` 完成复检闭环。**

#### 3. 生成本地回归测试样本
```powershell
py skills/lut-inspector/scripts/generate_test_luts.py --out-dir "./examples"
```

---

## 支持的 Log 曲线与目标环境

[`inspect_lut.py`](skills/lut-inspector/scripts/inspect_lut.py) 内置 **10 种摄影机/色彩空间曲线预设** 与 **3 种硬件目标环境**：

| `--profile` 参数值 | 适用色彩空间 / 摄影机曲线 | 0% 黑电平参考 | 18% 中灰参考 |
| :--- | :--- | :---: | :---: |
| `slog3` *(默认)* | Sony S-Log3 / S-Gamut3.Cine (VENICE / FX6 / FX3) | `0.092864` (`95/1023`) | `0.410557` (`420/1023`) |
| `logc3` | ARRI LogC3 (EI800) | `0.092864` | `0.391007` |
| `logc4` | ARRI LogC4 (ALEXA 35) | `0.092864` | `0.278395` |
| `vlog` | Panasonic V-Log | `0.125122` (`128/1023`) | `0.423265` (`433/1023`) |
| `log3g10` | RED Log3G10 | `0.000000` | `0.333333` |
| `clog3` | Canon Log 3 | `0.125122` (`128/1023`) | `0.343300` |
| `davinci_intermediate` | DaVinci Intermediate | `0.000000` | `0.336043` |
| `acescct` | ACEScct | `0.072905` | `0.413588` |
| `rec709` | Rec.709 / Gamma 2.4 | `0.000000` | `0.409000` |
| `generic_log` | 通用摄影机 Log 预设 | `0.090000` | `0.400000` |

目标环境 (`--target`)：`sony_cinealta`（VENICE / FX6 / FX3 严格 33 点纯 3D 规范）· `generic_camera`（Atomos / SmallHD 等通用监视器）· `post`（仅后期 DaVinci 内部使用）。

---

## Sony VENICE / FX6 / FX3 导出避坑 5 秒自查

1. **尺寸**：必须导出 **`33 Point Cube`**（不要选 65 点或 1D+3D Shaper 复合格式）。
2. **节点禁区**：导出前务必关闭胶片颗粒（Grain）、光晕（Halation）、柔光（Glow）、暗角（Vignette）和任何空间滤镜。
3. **DCTL 开关**：确认关闭插件里的 `False Color / Zebra / Gamut Warning / Split View`。
4. **Data Levels**：遵循 S-Log3 完整 Full/Data 范围（`95/1023` 黑位对应 `0.092864`），避免套用错位的 Video Levels 压缩。
5. **编码命名**：文件名与内部 `TITLE` 使用纯英文、数字和下划线，避免中文字符或 UTF-8 BOM。

> 📚 深入技术原理与 DaVinci 节点修复指南，请参阅：
> - [Sony 机内规格与 S-Log3 黑位锚点技术指南](skills/lut-inspector/references/sony_cinealta_lut_guide.md)
> - [DaVinci Resolve 第三方 DCTL 常见伪影代码级与节点级排查手册](skills/lut-inspector/references/dctl_artifact_root_causes.md)

---

## 项目目录结构

```text
lut-inspector-skill/
├── .agents/
│   └── skills.json                         # Agent Skill 自动发现配置
├── assets/
│   ├── banner.svg                          # 项目主视觉 Banner
│   ├── slog3_anchor_and_artifacts.svg      # 溢出回绕与 S-Log3 黑位共振机制图
│   └── architecture_workflow.svg           # 全流程科学质检与闭环修复架构图
├── skills/lut-inspector/                   # 核心 Skill 包
│   ├── SKILL.md                            # Agent 自动化执行标准 SOP
│   ├── scripts/
│   │   ├── inspect_lut.py                  # 科学质检、底噪仿真与高清双图生成
│   │   ├── sanitize_lut.py                 # 机内安全修复、33 点重采样与彩噪镇定
│   │   └── generate_test_luts.py           # 标准正例与缺陷复现测试 LUT 生成器
│   └── references/
│       ├── sony_cinealta_lut_guide.md      # Sony VENICE / FX6 / FX3 技术规范
│       └── dctl_artifact_root_causes.md    # DCTL 伪影代码级与节点级排查手册
├── examples/
│   ├── clean_slog3_to_rec709_33.cube       # 健康 S-Log3 → Rec.709 基准样本
│   ├── broken_dctl_sony_zebra_shadow_33.cube              # 故障 DCTL 复现样本
│   ├── broken_dctl_sony_zebra_shadow_33_sony_safe_33.cube # 应急修复版样本
│   └── sample_report/                      # 完整 JSON / Markdown / 双图示例报告
├── requirements.txt                        # Python 依赖 (numpy, matplotlib)
├── LICENSE                                 # MIT License
└── README.md                               # 本项目说明文档
```

## License

本项目基于 [MIT License](LICENSE) 开源。
