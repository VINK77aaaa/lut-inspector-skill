#!/usr/bin/env python3
"""
lut-inspector 机内安全修复与格式规范化工具。

支持：
1. 修复 NaN / Inf 晶格坏点；
2. 将 1D Shaper + 3D LUT 烘焙合并为纯 3D LUT；
3. 使用四面体高精度插值将 65 点等重采样为 Sony VENICE / FX6 / FX3 兼容的 33 点；
4. 严格钳位 [0.0, 1.0] 消除定点溢出斑马纹与暗部下溢闪点；
5. 可选极暗部近轴色度爆炸镇定 (--tame-shadow-chroma) 与高频锯齿阻尼 (--de-oscillate)；
6. 导出纯 ASCII、无 BOM、LF 换行、标准 R-fastest 排序的 .cube 副本（绝不覆盖原文件）。
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Optional, Tuple

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import numpy as np

from inspect_lut import (
    PROFILES,
    _repair_non_finite_grid,
    bake_1d_and_3d_to_3d,
    chroma_spread,
    eval_tetrahedral,
    luma_709,
    parse_cube_file,
)


def write_sony_safe_cube(grid: np.ndarray, out_path: Path, title: str) -> None:
    """
    将 shape=(N, N, N, 3)（索引顺序 [i_r, i_g, i_b, ch]）的 3D LUT
    按 Adobe/Sony 标准 R-fastest 顺序写为纯 ASCII 无 BOM .cube 文件。
    """
    n = grid.shape[0]
    safe_title = re.sub(r"[^A-Za-z0-9_.\- ]+", "_", title).strip() or "Sony_Safe_LUT"
    # 消除 -0.000000
    clean = np.where(np.abs(grid) < 1e-7, 0.0, grid)
    # 逆转置: [i_r, i_g, i_b, ch] -> [i_b, i_g, i_r, ch]，展平即 Red 最快变化
    rows = np.transpose(clean, (2, 1, 0, 3)).reshape((-1, 3))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="ascii", newline="\n") as f:
        f.write(f'TITLE "{safe_title}"\n')
        f.write(f"LUT_3D_SIZE {n}\n")
        f.write("DOMAIN_MIN 0.000000 0.000000 0.000000\n")
        f.write("DOMAIN_MAX 1.000000 1.000000 1.000000\n")
        for r_v, g_v, b_v in rows:
            f.write(f"{r_v:.6f} {g_v:.6f} {b_v:.6f}\n")


def resample_3d_grid(grid: np.ndarray, target_size: int) -> np.ndarray:
    """使用高精度四面体插值将 3D 晶格重采样至 target_size。"""
    if grid.shape[0] == target_size:
        return grid.copy()
    axes = np.linspace(0.0, 1.0, target_size, dtype=np.float64)
    rr, gg, bb = np.meshgrid(axes, axes, axes, indexing="ij")
    pts = np.stack([rr, gg, bb], axis=-1)
    return eval_tetrahedral(grid, pts)


def tame_shadow_chroma(grid: np.ndarray, black_point: float, shadow_hi: float) -> Tuple[np.ndarray, int]:
    """
    极暗部近中性灰轴色度爆炸镇定：
    仅针对暗部区域内紧邻中性灰轴（输入色差 <= 2 步长）却出现剧烈彩色偏移的异常晶格点，
    按当前点亮度平滑抑制其异常高出的色度分量，避免伤及正常高饱和彩色或中高调影调。
    """
    out = grid.copy()
    n = out.shape[0]
    step = 1.0 / (n - 1)
    axes = np.linspace(0.0, 1.0, n, dtype=np.float64)
    rr, gg, bb = np.meshgrid(axes, axes, axes, indexing="ij")
    in_mean = (rr + gg + bb) / 3.0
    in_ch = np.maximum(np.maximum(rr, gg), bb) - np.minimum(np.minimum(rr, gg), bb)

    hi_bound = max(shadow_hi + 0.025, black_point + 0.07)
    near_axis_dark = (in_mean <= hi_bound) & (in_ch <= 2.2 * step + 1e-5)

    out_ch = chroma_spread(out)
    # 估计中性灰轴在该亮度下的参考输出值（使用同亮度纯灰采样插值）
    gray_axis_rgb = np.stack([out[i, i, i] for i in range(n)], axis=0)
    gray_axis_luma = luma_709(gray_axis_rgb)

    adjusted_count = 0
    # 对暗部近灰轴点逐一做保亮度色度限幅
    coords = np.argwhere(near_axis_dark)
    for r, g, b in coords:
        c_in = float(in_ch[r, g, b])
        c_out = float(out_ch[r, g, b])
        # 允许的暗部近轴合理最大色度：既尊重原灰轴微偏色，又封死除零引发的异常爆炸
        allowed_chroma = max(0.016, 1.45 * c_in)
        if c_out > allowed_chroma:
            rgb = out[r, g, b]
            y_val = float(luma_709(rgb))
            # 保持该点原亮度 y_val，将饱和偏移按比例压回安全范围
            neutral = np.array([y_val, y_val, y_val], dtype=np.float64)
            scale = allowed_chroma / max(c_out, 1e-6)
            # 柔和过渡权重，越暗越严格
            dark_weight = np.clip((hi_bound - float(in_mean[r, g, b])) / max(hi_bound * 0.75, 1e-4), 0.35, 0.96)
            target_rgb = neutral + scale * (rgb - neutral)
            out[r, g, b] = rgb * (1.0 - dark_weight) + target_rgb * dark_weight
            adjusted_count += 1

    return out, adjusted_count


def de_oscillate_grid(grid: np.ndarray, iterations: int = 2) -> Tuple[np.ndarray, int]:
    """
    保边阻尼去振荡：
    仅针对沿 R/G/B 轴出现正负交替锯齿翻转的局部尖峰内点做轻度平滑，不糊化正常单调渐变。
    """
    out = grid.copy()
    n = out.shape[0]
    smoothed_points = 0
    for _ in range(iterations):
        nxt = out.copy()
        for axis in range(3):
            # 沿单轴提取前驱与后继差分
            diff_fwd = np.diff(out, axis=axis)
            # 在内点处检查左右差分反号（锯齿尖峰）
            s_left = [slice(None)] * 4
            s_right = [slice(None)] * 4
            s_mid = [slice(None)] * 4
            s_prev = [slice(None)] * 4
            s_next = [slice(None)] * 4
            s_left[axis] = slice(0, n - 2)
            s_right[axis] = slice(1, n - 1)
            s_mid[axis] = slice(1, n - 1)
            s_prev[axis] = slice(0, n - 2)
            s_next[axis] = slice(2, n)

            d1 = diff_fwd[tuple(s_left)]
            d2 = diff_fwd[tuple(s_right)]
            # 仅当一阶差分反号且振荡幅值明显时判定为局部锯齿点
            osc_mask = (d1 * d2 < -2e-5) & (np.abs(d1) > 0.004) & (np.abs(d2) > 0.004)
            if np.any(osc_mask):
                mid_vals = out[tuple(s_mid)]
                avg_neighbors = 0.5 * (out[tuple(s_prev)] + out[tuple(s_next)])
                updated = np.where(osc_mask, 0.35 * mid_vals + 0.65 * avg_neighbors, mid_vals)
                nxt[tuple(s_mid)] = updated
                smoothed_points += int(np.sum(osc_mask))
        out = nxt
    return out, smoothed_points


def sanitize_lut_file(
    input_path: str | Path,
    output_path: Optional[str | Path] = None,
    size_opt: str = "33",
    profile_key: str = "slog3",
    do_clamp: bool = True,
    do_fix_nan: bool = True,
    do_tame_shadow: bool = False,
    do_de_oscillate: bool = False,
    custom_title: Optional[str] = None,
) -> Path:
    """执行 LUT 安全修复并导出新副本。"""
    parsed = parse_cube_file(input_path)
    profile = PROFILES[profile_key]

    # 获取起点 3D 晶格
    if parsed.raw_grid_3d is not None and parsed.shaper_1d is not None:
        s1 = np.nan_to_num(parsed.shaper_1d, nan=0.0, posinf=1.0, neginf=0.0) if do_fix_nan else parsed.shaper_1d
        s3 = _repair_non_finite_grid(parsed.raw_grid_3d) if do_fix_nan else parsed.raw_grid_3d.copy()
        work_size = parsed.lut_3d_size or 33
        grid = bake_1d_and_3d_to_3d(s1, s3, target_size=work_size)
    elif parsed.raw_grid_3d is not None:
        grid = _repair_non_finite_grid(parsed.raw_grid_3d) if do_fix_nan else parsed.raw_grid_3d.copy()
    else:
        grid = parsed.safe_grid_3d.copy()

    orig_min = float(np.nanmin(grid)) if np.any(np.isfinite(grid)) else 0.0
    orig_max = float(np.nanmax(grid)) if np.any(np.isfinite(grid)) else 1.0

    # 重采样到目标晶格尺寸
    if size_opt == "keep":
        target_size = int(grid.shape[0])
    else:
        target_size = int(size_opt)

    if grid.shape[0] != target_size:
        grid = resample_3d_grid(grid, target_size)

    osc_fixed_count = 0
    if do_de_oscillate:
        grid, osc_fixed_count = de_oscillate_grid(grid, iterations=3)

    tamed_count = 0
    if do_tame_shadow:
        grid, tamed_count = tame_shadow_chroma(
            grid, black_point=profile.black_point, shadow_hi=profile.shadow_zone[1]
        )

    if do_clamp:
        grid = np.clip(grid, 0.0, 1.0)

    in_p = Path(input_path)
    if output_path is None:
        safe_stem = re.sub(r"[^A-Za-z0-9_.\-]+", "_", in_p.stem).strip("_") or "LUT"
        out_p = in_p.with_name(f"{safe_stem}_sony_safe_{target_size}.cube")
    else:
        out_p = Path(output_path)

    title = custom_title or f"{in_p.stem}_SonySafe_{target_size}"
    write_sony_safe_cube(grid, out_p, title)

    print("=" * 68)
    print(f"[sanitize_lut] 修复导出完成 -> {out_p}")
    print(f"  晶格尺寸: {parsed.lut_3d_size or '1D'} -> {target_size}x{target_size}x{target_size} (纯3D)")
    print(f"  修复前值域: [{orig_min:.5f}, {orig_max:.5f}]  (NaN={parsed.nan_count}, Inf={parsed.inf_count})")
    print(f"  修复后值域: [{float(np.min(grid)):.5f}, {float(np.max(grid)):.5f}]")
    print(f"  暗部近轴色度镇定晶格数: {tamed_count}  |  去振荡调整点数: {osc_fixed_count}")
    print("=" * 68)
    return out_p


def main() -> int:
    parser = argparse.ArgumentParser(
        description="lut-inspector: 生成 Sony VENICE / FX6 / FX3 机内安全修复版 .cube 副本"
    )
    parser.add_argument("input_cube", help="待修复的源 .cube 文件路径")
    parser.add_argument("-o", "--output", default=None, help="输出 .cube 文件路径（默认生成 _sony_safe_33.cube 副本）")
    parser.add_argument(
        "--size",
        default="33",
        choices=["17", "33", "65", "keep"],
        help="输出 3D LUT 晶格尺寸（Sony 机内推荐 33，默认: 33）",
    )
    parser.add_argument(
        "--profile",
        default="slog3",
        choices=list(PROFILES.keys()),
        help="Log 曲线参考基准 (默认: slog3)",
    )
    parser.add_argument(
        "--clamp",
        dest="clamp",
        action="store_true",
        default=True,
        help="开启 [0.0, 1.0] 严格钳位 (默认开启)",
    )
    parser.add_argument(
        "--no-clamp",
        dest="clamp",
        action="store_false",
        help="关闭 [0.0, 1.0] 严格钳位",
    )
    parser.add_argument(
        "--fix-nan",
        dest="fix_nan",
        action="store_true",
        default=True,
        help="开启 NaN/Inf 自动插补 (默认开启)",
    )
    parser.add_argument(
        "--no-fix-nan",
        dest="fix_nan",
        action="store_false",
        help="关闭 NaN/Inf 自动插补",
    )
    parser.add_argument(
        "--tame-shadow-chroma",
        action="store_true",
        help="启用极暗部近灰轴色度爆炸镇定（缓解暗部彩噪）",
    )
    parser.add_argument(
        "--de-oscillate",
        action="store_true",
        help="启用保边去振荡阻尼（缓解高光锯齿斑马纹）",
    )
    parser.add_argument("--title", default=None, help="自定义输出 LUT 内部 ASCII TITLE")
    args = parser.parse_args()

    sanitize_lut_file(
        input_path=args.input_cube,
        output_path=args.output,
        size_opt=args.size,
        profile_key=args.profile,
        do_clamp=args.clamp,
        do_fix_nan=args.fix_nan,
        do_tame_shadow=args.tame_shadow_chroma,
        do_de_oscillate=args.de_oscillate,
        custom_title=args.title,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
