#!/usr/bin/env python3
"""
生成用于自检与验证的基准 .cube 测试集：
1. clean_slog3_to_rec709_33.cube: 无瑕平滑的 Sony S-Log3 -> Rec.709 33点机内监看 LUT；
2. broken_dctl_sony_zebra_shadow_33.cube: 精准复刻 DCTL 典型缺陷的 33点 LUT
   （高光超白 >1.0 + 单调性反转/锯齿斑马纹 + S-Log3 黑电平近轴色度除零彩噪 + <0.0 负值闪点）。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import numpy as np

from inspect_lut import slog3_to_rec709_reference
from sanitize_lut import write_sony_safe_cube


def build_clean_slog3_lut(n: int = 33) -> np.ndarray:
    """构建无瑕疵 Sony S-Log3 / S-Gamut3.Cine -> Rec.709 33 点 LUT，索引为 [i_r, i_g, i_b, 3]。"""
    axes = np.linspace(0.0, 1.0, n, dtype=np.float64)
    rr, gg, bb = np.meshgrid(axes, axes, axes, indexing="ij")
    pts = np.stack([rr, gg, bb], axis=-1)
    return slog3_to_rec709_reference(pts)


def build_broken_dctl_lut(n: int = 33) -> np.ndarray:
    """
    在基准 S-Log3 -> Rec.709 曲线上注入两类常见第三方 DCTL 致命故障：
      1) 高光区间（0.62 ~ 0.88）出现过激减法饱和度反转、高频振荡锯齿（斑马纹）及 > 1.0 超白溢出；
      2) S-Log3 黑电平 0.092864 周围近灰轴晶格出现除零式色度爆炸（极暗部彩噪）及 < 0.0 负值下溢。
    """
    base = build_clean_slog3_lut(n)
    broken = base.copy()

    axes = np.linspace(0.0, 1.0, n, dtype=np.float64)
    rr, gg, bb = np.meshgrid(axes, axes, axes, indexing="ij")
    in_pts = np.stack([rr, gg, bb], axis=-1)
    in_mean = np.mean(in_pts, axis=-1)
    in_chroma = np.max(in_pts, axis=-1) - np.min(in_pts, axis=-1)

    # 故障 1: 高光斑马纹波纹 + 单调性反转 + >1.0 定点回绕风险
    hi_band = (in_mean >= 0.62) & (in_mean <= 0.88)
    # 沿亮度施加交替振荡（模拟未平滑分段或过度减法饱和度波纹）
    ripple = 0.055 * np.sin((in_mean - 0.62) * 58.0)
    # 针对高光及暖色/饱和通道额外施加强烈亮度倒跌与超 1.0 增益
    sat_boost = np.where(rr > gg, 0.048 * (rr - gg), 0.0)
    broken[hi_band, 0] += ripple[hi_band] + 0.045 + sat_boost[hi_band]
    broken[hi_band, 1] += ripple[hi_band] * 0.90 + 0.032
    broken[hi_band, 2] += ripple[hi_band] * 0.85 + 0.020

    # 在高白角点区域也刻意产生超白 (1.035)
    top_hi = in_mean >= 0.88
    broken[top_hi] += 0.042

    # 故障 2: 极暗部 S-Log3 黑位 (0.09286) 附近除零色度爆炸 + 微小负值 (< 0.0)
    shadow_band = (in_mean >= 0.04) & (in_mean <= 0.16) & (in_chroma > 1e-5) & (in_chroma <= 0.07)
    # 模拟 1 / (L + eps) 在偏离灰轴微小扰动时的红/绿/蓝剧烈分裂
    # 用输入三通道相对均值的偏差方向乘以异常巨大放大倍率，直击近轴晶格
    dev = in_pts - in_mean[..., None]
    denom = np.maximum(np.abs(in_mean - 0.092), 0.008)
    explosion = (dev / denom[..., None]) * 0.095
    broken[shadow_band] += explosion[shadow_band]

    # 在极深超黑区间 (< 0.092) 注入负值，模拟无符号定点下溢闪点
    sub_black = in_mean < 0.090
    broken[sub_black] -= 0.015

    return broken


def write_raw_unclamped_cube(grid: np.ndarray, out_path: Path, title: str) -> None:
    """保留越界负值与 >1.0 超白值写入 .cube，用于复现真实故障文件。"""
    n = grid.shape[0]
    rows = np.transpose(grid, (2, 1, 0, 3)).reshape((-1, 3))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="ascii", newline="\n") as f:
        f.write(f'TITLE "{title}"\n')
        f.write(f"LUT_3D_SIZE {n}\n")
        f.write("DOMAIN_MIN 0.000000 0.000000 0.000000\n")
        f.write("DOMAIN_MAX 1.000000 1.000000 1.000000\n")
        for r_v, g_v, b_v in rows:
            f.write(f"{r_v:.6f} {g_v:.6f} {b_v:.6f}\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="生成用于验证 lut-inspector 的标准样本与缺陷样本 .cube")
    parser.add_argument("--out-dir", default="examples", help="测试样本输出目录 (默认: examples)")
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    clean_grid = build_clean_slog3_lut(33)
    clean_path = out_dir / "clean_slog3_to_rec709_33.cube"
    write_sony_safe_cube(clean_grid, clean_path, "Clean_Sony_SLog3_to_Rec709_33")

    broken_grid = build_broken_dctl_lut(33)
    broken_path = out_dir / "broken_dctl_sony_zebra_shadow_33.cube"
    write_raw_unclamped_cube(broken_grid, broken_path, "Broken_DCTL_Zebra_Shadow_33")

    print(f"[generate_test_luts] 已生成健康样本: {clean_path.resolve()}")
    print(f"[generate_test_luts] 已生成缺陷样本: {broken_path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
