# DaVinci Resolve 第三方 DCTL 常见 LUT 伪影代码级与节点级排查手册

当你从 DaVinci Resolve 导出含第三方 DCTL 插件的 `.cube` 文件，并在摄影机或监视器上遇到 **斑马纹状条纹** 或 **极暗部异常噪点** 时，请按以下分类对照排查。

---

## 1. 斑马纹 / 条带状伪影（Zebra / Banding / Solarization）

### 根因 A：输出未做 `[0.0, 1.0]` 钳位导致硬件定点溢出回绕
- **代码常见写法（缺陷）**：
  ```c
  // 直接返回线性计算或锐化增益结果，高光可达 1.05 ~ 1.30
  return make_float3(r, g, b);
  ```
- **现象**：Resolve 32-bit 浮点管线将 `1.04` 显示为纯白，完全看不出问题；导出 `.cube` 到摄影机/监视器定点硬件后，`> 1.0` 溢出回绕为黑色/补色，在高光边界形成类似斑马纹的黑色轮廓带。
- **修复**：
  - 在 Resolve 节点树最末级加一个安全钳位节点或 DCTL 末尾加 `_saturatef(val)` / `clamp(val, 0.0f, 1.0f)`；
  - 若需保留高光细节而不是硬裁切，先在最后加温和的高光压缩（Highlight Roll-off / Soft Clip）后再 Clamp。

### 根因 B：过激“减法饱和度（Subtractive Saturation）”或高光色相扭曲导致的单调性反转
- **代码/调色常见手法（缺陷）**：
  - 许多网红胶片模拟 DCTL（如自制 Print Film Density / Tetrahedral Color Warper）在高饱和度通道使用过大的减法权重（如 `R_out = R - k * (G + B)` 且未限制下界，或在亮部强压某通道亮度）。
  - 导致输入亮度升高时，输出 Rec.709 亮度 $Y$ 反而掉头向下（$\partial Y/\partial x < 0$）。
- **现象**：大光比天空、霓虹灯、车灯、高光肤色边缘出现一圈圈反相明暗条纹或“等高线”。
- **修复**：
  - 在 `inspect_lut.py` 的报告中查看**发生反转的具体通道与输入区间**（例如 `R 通道 0.65~0.85`）；
  - 回到 Resolve 中降低该 DCTL 的 **Density / Subtractive Sat / Tetrahedral Warp** 滑块，或在进入该 DCTL 前先用 `CST` 开启 **Tone Mapping（Luminance Mapping）+ Gamut Mapping（Saturation Compression）**，避免超广色域数值直接撞击 DCTL。

### 根因 C：DCTL 内的 `if-else` 分段硬切换或遗留指示开关
- **代码常见写法（缺陷）**：
  ```c
  if (luma > 0.75f) {
      out = highlight_curve(in); // 在 0.75 处与下方曲线不连续！
  }
  ```
  或者开发者内置了曝光警告/色域警告条纹（如 `sin(luma * 100)` 或硬区间掩码）。
- **修复**：检查 DCTL 参数面板确保无调试/Overlay 开关；将分段逻辑替换为 `smoothstep` 平滑过渡，或更换为连续可微的高光曲线。

### 根因 D：3D 晶格空间折叠（Jacobian < 0）与四面体/三线性插值分歧
- **原理**：DaVinci Resolve 内部默认开启 **Tetrahedral（四面体）插值**，而许多摄影机/监视器默认 **Trilinear（三线性）插值**。当 DCTL 对色相角或饱和度施加了大曲率局部变形（如强行将相邻色相拉到同一条线甚至交叉折叠）时，33 点晶格采样后，三线性插值与四面体插值在体素内部结果大相径庭，产生沿 33 点网格规律排布的马赛克/斑马状阶梯条纹。
- **修复**：尝试在 DaVinci Resolve 的 `Project Settings -> Color Management -> 3D Lookup Table Interpolation` 临时切换为 **Trilinear** 预览，马上就能在监视器上提前抓出问题；并在 DCTL 中减小局部色相极度挤压的幅度。

---

## 2. 极暗部噪点 / 彩噪爆炸（Shadow Noise & Chroma Explosion）

### 根因 E：输出出现 `< 0.0` 微小负值（硬件下溢闪点）
- **代码常见写法（缺陷）**：
  - 色彩矩阵转换（如 `S-Gamut3.Cine -> Rec.709`）本身必然对暗部/高饱和边界像素产生负系数（例如 `-0.008`）；
  - Lift（暗部抬升/下压）或 Black Offset 在 S-Log3 黑位以下减成负数。
- **现象**：无符号硬件中 `-0.005` 回绕成 `0.995`，在极暗部随机跳出极亮的红/绿/蓝/白噪点颗粒。
- **修复**：转换矩阵与 DCTL 末尾必须严格 `_fmaxf(val, 0.0f)`，同时用 `sanitize_lut.py --clamp` 修复已有 LUT。

### 根因 F：暗部球面/圆柱/HSV 饱和度公式的“除零奇异点”（最隐蔽、最致命）
- **代码常见写法（缺陷）**：
  ```c
  // 常见于“自制彩色密度”、“分离色调”、“HSV/HSL 饱和度调整” DCTL
  float max_c = _fmaxf(r, _fmaxf(g, b));
  float min_c = _fminf(r, _fminf(g, b));
  float sat = (max_c - min_c) / _fmaxf(max_c, 1e-5f); // 当通道接近 0 或包含噪声波动时，分母极小！
  // 或者用 YCbCr / Opponent Space 按比例放大色度：
  float chroma_gain = target_sat / (luma + 1e-4f);
  ```
- **现象**：当 S-Log3 黑电平 `0.092864`（码值 95）被解码到线性光 `0.0` 附近时，传感器哪怕只有 ±0.002 的微弱热噪（比如 `R=0.002, G=0.000, B=0.001`），上述分母趋近于 0 的公式会瞬间算出 `sat = 1.0` 或巨大的色度偏移！
  反映在 33 点 `.cube` 中，就是 **黑位附近（第 2~4 层晶格）偏离灰轴仅 1 个步长的邻接点被染上了剧烈的红/绿/蓝/洋红偏色**，导致机内极暗部出现大面积蠕动的彩色噪点（Chroma Noise）。
- **修复**：
  1. 在 DCTL 内部计算饱和度或比值前，必须在分母增加符合实际黑电平尺度的**暗部保护门限（Shadow Rolloff / Soft Floor）**，例如在归一化对数域黑位处设置 `smoothstep(0.09, 0.18, luma)` 逐渐衰减速彩增强，或线性域使用 `luma + 0.02f` 而非 `1e-5f`；
  2. 在 Resolve 中于 DCTL 之前或之后，对极暗部（Luma Qualifier `0 ~ 12%`）适度降低饱和度（Shadow Desaturation）；
  3. 对已导出的应急 LUT，可使用 `sanitize_lut.py --tame-shadow-chroma` 精准平滑近轴极暗部晶格异常色度刺点。

### 根因 G：`_powf(x, gamma)` 传入负值产生 `NaN`（Not a Number）
- **代码常见写法（缺陷）**：
  ```c
  // S-Gamut3.Cine 转 Rec.709 矩阵后部分通道为负值，直接套 Gamma 幂函数：
  r = _powf(r, 1.0f / 2.4f); // 当 r < 0 时 GPU/CUDA/Metal 直接返回 NaN！
  ```
- **现象**：`.cube` 文件里直接出现 `nan`、`1.#IND` 或某一个角点跳变回 `0.000000`，在阴影高饱和区形成黑斑或撕裂噪点。
- **修复**：所有幂运算前必须写为 `_powf(_fmaxf(x, 0.0f), gamma)`，或使用保留符号的对称幂 `copysignf(_powf(_fabsf(x), gamma), x)`。

### 根因 H：极暗部局部增益斜率过大（Shadow Slope Spike）
- **原理**：S-Log3 编码在暗部本身斜率极平缓（线性光微小变化对应 Log 较窄范围）。如果在 S-Log3 尚未正确解码（或被连续叠加了两层 S-Curve 对比度 DCTL）的情况下硬拉阴影与对比度，会导致输出相对于输入的一阶导数 $\partial Y / \partial x > 8 \sim 15$，将底噪等比例拉大 10 倍。
- **修复**：检查是否重复叠加了 Log-to-709 转换（双重 S-Curve），并适当收紧暗部对比度斜率或抬高 Black Point 软趾部（Toe）。
