#!/usr/bin/env python3
"""
lut-inspector 核心科学质检与可视化诊断工具。

专用于排查 DaVinci Resolve 第三方 DCTL 导出的 .cube LUT 在 Sony VENICE / FX6 / FX3
及通用摄影机/监视器机内套用时出现的“斑马纹状条纹”与“极暗部噪点/彩噪”问题。
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

plt.rcParams["font.sans-serif"] = [
    "Microsoft YaHei",
    "SimHei",
    "DengXian",
    "Noto Sans CJK SC",
    "DejaVu Sans",
]
plt.rcParams["axes.unicode_minus"] = False


@dataclass(frozen=True)
class CameraProfile:
    key: str
    name: str
    black_point: float
    mid_gray: float
    white_point: float
    shadow_zone: Tuple[float, float]
    sub_black_zone: Tuple[float, float]
    highlight_zone: Tuple[float, float]
    notes: str


PROFILES: Dict[str, CameraProfile] = {
    "slog3": CameraProfile(
        key="slog3",
        name="Sony S-Log3 / S-Gamut3.Cine (VENICE / FX6 / FX3)",
        black_point=95.0 / 1023.0,
        mid_gray=420.0 / 1023.0,
        white_point=598.0 / 1023.0,
        shadow_zone=(0.05, 0.17),
        sub_black_zone=(0.0, 95.0 / 1023.0),
        highlight_zone=(0.55, 0.92),
        notes="10-bit 0% 黑电平在 95/1023 (0.092864)，紧贴 33点晶格第3层 (3/32=0.09375)",
    ),
    "logc3": CameraProfile(
        key="logc3",
        name="ARRI LogC3 (EI800)",
        black_point=0.092864,
        mid_gray=0.391007,
        white_point=0.564,
        shadow_zone=(0.05, 0.16),
        sub_black_zone=(0.0, 0.092864),
        highlight_zone=(0.54, 0.90),
        notes="ARRI LogC3 EI800 标准黑位约 0.0929，18% 灰约 0.391",
    ),
    "logc4": CameraProfile(
        key="logc4",
        name="ARRI LogC4 (ALEXA 35)",
        black_point=0.092864,
        mid_gray=0.278395,
        white_point=0.460,
        shadow_zone=(0.05, 0.15),
        sub_black_zone=(0.0, 0.092864),
        highlight_zone=(0.45, 0.88),
        notes="ARRI LogC4 高动态范围曲线，18% 灰更低 (0.2784)",
    ),
    "vlog": CameraProfile(
        key="vlog",
        name="Panasonic V-Log",
        black_point=128.0 / 1023.0,
        mid_gray=433.0 / 1023.0,
        white_point=0.610,
        shadow_zone=(0.08, 0.20),
        sub_black_zone=(0.0, 128.0 / 1023.0),
        highlight_zone=(0.58, 0.92),
        notes="V-Log 0% 黑位 128/1023 (0.1251)，18% 灰约 0.423",
    ),
    "log3g10": CameraProfile(
        key="log3g10",
        name="RED Log3G10",
        black_point=0.0,
        mid_gray=0.333333,
        white_point=0.560,
        shadow_zone=(0.0, 0.12),
        sub_black_zone=(0.0, 0.02),
        highlight_zone=(0.52, 0.90),
        notes="RED Log3G10 黑位在 0.0 附近，18% 灰在 1/3 (0.3333)",
    ),
    "clog3": CameraProfile(
        key="clog3",
        name="Canon Log 3",
        black_point=128.0 / 1023.0,
        mid_gray=0.3433,
        white_point=0.560,
        shadow_zone=(0.08, 0.19),
        sub_black_zone=(0.0, 128.0 / 1023.0),
        highlight_zone=(0.54, 0.90),
        notes="Canon Log 3 黑位约 128/1023 (0.1251)",
    ),
    "davinci_intermediate": CameraProfile(
        key="davinci_intermediate",
        name="DaVinci Intermediate",
        black_point=0.0,
        mid_gray=0.336043,
        white_point=0.560,
        shadow_zone=(0.0, 0.11),
        sub_black_zone=(0.0, 0.02),
        highlight_zone=(0.52, 0.90),
        notes="DaVinci Intermediate 18% 灰在 0.3360",
    ),
    "acescct": CameraProfile(
        key="acescct",
        name="ACEScct",
        black_point=0.072905,
        mid_gray=0.413588,
        white_point=0.600,
        shadow_zone=(0.04, 0.15),
        sub_black_zone=(0.0, 0.072905),
        highlight_zone=(0.55, 0.92),
        notes="ACEScct 18% 灰 0.4136，趾部线性起点 0.0729",
    ),
    "rec709": CameraProfile(
        key="rec709",
        name="Rec.709 / Gamma 2.4",
        black_point=0.0,
        mid_gray=0.409,
        white_point=0.940,
        shadow_zone=(0.0, 0.12),
        sub_black_zone=(0.0, 0.01),
        highlight_zone=(0.75, 0.99),
        notes="显示域 Rec.709 曲线",
    ),
    "generic_log": CameraProfile(
        key="generic_log",
        name="通用 Log 曲线",
        black_point=0.09,
        mid_gray=0.40,
        white_point=0.60,
        shadow_zone=(0.05, 0.17),
        sub_black_zone=(0.0, 0.09),
        highlight_zone=(0.55, 0.92),
        notes="通用摄影机 Log 预设",
    ),
}


@dataclass
class ParsedCube:
    path: str
    title: str
    lut_1d_size: Optional[int]
    lut_3d_size: Optional[int]
    domain_min: np.ndarray
    domain_max: np.ndarray
    has_bom: bool
    has_non_ascii_text: bool
    has_non_ascii_filename: bool
    file_size_bytes: int
    nan_count: int
    inf_count: int
    negative_zero_count: int
    shaper_1d: Optional[np.ndarray]
    raw_grid_3d: Optional[np.ndarray]  # shape (N, N, N, 3) with index [r, g, b, ch]
    safe_grid_3d: np.ndarray  # shape (N, N, N, 3), NaN/Inf repaired for interpolation
    parse_warnings: List[str]


def _repair_non_finite_grid(grid: np.ndarray) -> np.ndarray:
    """用邻域有效点均值填补 3D 晶格中的 NaN / Inf，仅用于保证后续曲线采样不崩溃。"""
    if np.all(np.isfinite(grid)):
        return grid.copy()
    fixed = grid.copy()
    n = fixed.shape[0]
    for _ in range(3):
        bad_mask = ~np.isfinite(fixed)
        if not np.any(bad_mask):
            break
        coords = np.argwhere(bad_mask)
        for r, g, b, ch in coords:
            r0, r1 = max(0, r - 1), min(n, r + 2)
            g0, g1 = max(0, g - 1), min(n, g + 2)
            b0, b1 = max(0, b - 1), min(n, b + 2)
            patch = fixed[r0:r1, g0:g1, b0:b1, ch]
            valid = patch[np.isfinite(patch)]
            if valid.size > 0:
                fixed[r, g, b, ch] = float(np.mean(valid))
    still_bad = ~np.isfinite(fixed)
    if np.any(still_bad):
        fixed = np.nan_to_num(fixed, nan=0.0, posinf=1.0, neginf=0.0)
    return fixed


def _promote_1d_to_3d(lut_1d: np.ndarray, target_size: int = 33) -> np.ndarray:
    """将纯 1D LUT 映射为等效 33 点 3D LUT，便于统一检测。"""
    n1 = lut_1d.shape[0]
    t_in = np.linspace(0.0, 1.0, target_size, dtype=np.float64)
    x1 = np.linspace(0.0, 1.0, n1, dtype=np.float64)
    ch_curves = [np.interp(t_in, x1, np.nan_to_num(lut_1d[:, c])) for c in range(3)]
    rr, gg, bb = np.meshgrid(ch_curves[0], ch_curves[1], ch_curves[2], indexing="ij")
    return np.stack([rr, gg, bb], axis=-1)


def eval_1d_shaper(shaper: np.ndarray, pts: np.ndarray) -> np.ndarray:
    """对输入 pts (..., 3) 应用 1D Shaper 每通道独立线性插值。"""
    pts_c = np.clip(pts, 0.0, 1.0)
    x1 = np.linspace(0.0, 1.0, shaper.shape[0], dtype=np.float64)
    out = np.empty_like(pts_c, dtype=np.float64)
    for c in range(3):
        out[..., c] = np.interp(pts_c[..., c], x1, shaper[:, c])
    return out


def eval_trilinear(grid: np.ndarray, pts: np.ndarray) -> np.ndarray:
    """
    标准三线性插值 (模拟摄影机/监视器硬件最常见路径)。
    grid 维度为 [i_r, i_g, i_b, 3]，pts 形状为 (..., 3)，值域预期 [0, 1]。
    """
    n = grid.shape[0]
    p = np.clip(pts, 0.0, 1.0)
    s = p * (n - 1)
    i0 = np.clip(np.floor(s).astype(np.int64), 0, n - 2)
    i1 = i0 + 1
    f = s - i0

    rx0, gx0, bx0 = i0[..., 0], i0[..., 1], i0[..., 2]
    rx1, gx1, bx1 = i1[..., 0], i1[..., 1], i1[..., 2]
    fx = f[..., 0:1]
    fy = f[..., 1:2]
    fz = f[..., 2:3]

    c000 = grid[rx0, gx0, bx0]
    c100 = grid[rx1, gx0, bx0]
    c010 = grid[rx0, gx1, bx0]
    c110 = grid[rx1, gx1, bx0]
    c001 = grid[rx0, gx0, bx1]
    c101 = grid[rx1, gx0, bx1]
    c011 = grid[rx0, gx1, bx1]
    c111 = grid[rx1, gx1, bx1]

    c00 = c000 * (1.0 - fx) + c100 * fx
    c10 = c010 * (1.0 - fx) + c110 * fx
    c01 = c001 * (1.0 - fx) + c101 * fx
    c11 = c011 * (1.0 - fx) + c111 * fx

    c0 = c00 * (1.0 - fy) + c10 * fy
    c1 = c01 * (1.0 - fy) + c11 * fy
    return c0 * (1.0 - fz) + c1 * fz


def eval_tetrahedral(grid: np.ndarray, pts: np.ndarray) -> np.ndarray:
    """
    标准 6 分支四面体插值 (模拟 DaVinci Resolve 内部 3D LUT 插值引擎)。
    grid 维度为 [i_r, i_g, i_b, 3]，pts 形状为 (..., 3)。
    """
    n = grid.shape[0]
    p = np.clip(pts, 0.0, 1.0)
    orig_shape = p.shape[:-1]
    p_flat = p.reshape(-1, 3)

    s = p_flat * (n - 1)
    i0 = np.clip(np.floor(s).astype(np.int64), 0, n - 2)
    i1 = i0 + 1
    f = s - i0

    rx0, gx0, bx0 = i0[:, 0], i0[:, 1], i0[:, 2]
    rx1, gx1, bx1 = i1[:, 0], i1[:, 1], i1[:, 2]
    fx, fy, fz = f[:, 0], f[:, 1], f[:, 2]

    c000 = grid[rx0, gx0, bx0]
    c111 = grid[rx1, gx1, bx1]
    out = np.empty_like(c000, dtype=np.float64)

    m1 = (fx >= fy) & (fy >= fz)
    m2 = (~m1) & (fx >= fz) & (fz >= fy)
    m3 = (~m1) & (~m2) & (fz >= fx) & (fx >= fy)
    m4 = (~m1) & (~m2) & (~m3) & (fy >= fx) & (fx >= fz)
    m5 = (~m1) & (~m2) & (~m3) & (~m4) & (fy >= fz) & (fz >= fx)
    m6 = (~m1) & (~m2) & (~m3) & (~m4) & (~m5)

    if np.any(m1):
        c100 = grid[rx1[m1], gx0[m1], bx0[m1]]
        c110 = grid[rx1[m1], gx1[m1], bx0[m1]]
        w0 = (1.0 - fx[m1])[:, None]
        w1 = (fx[m1] - fy[m1])[:, None]
        w2 = (fy[m1] - fz[m1])[:, None]
        w3 = fz[m1][:, None]
        out[m1] = w0 * c000[m1] + w1 * c100 + w2 * c110 + w3 * c111[m1]

    if np.any(m2):
        c100 = grid[rx1[m2], gx0[m2], bx0[m2]]
        c101 = grid[rx1[m2], gx0[m2], bx1[m2]]
        w0 = (1.0 - fx[m2])[:, None]
        w1 = (fx[m2] - fz[m2])[:, None]
        w2 = (fz[m2] - fy[m2])[:, None]
        w3 = fy[m2][:, None]
        out[m2] = w0 * c000[m2] + w1 * c100 + w2 * c101 + w3 * c111[m2]

    if np.any(m3):
        c001 = grid[rx0[m3], gx0[m3], bx1[m3]]
        c101 = grid[rx1[m3], gx0[m3], bx1[m3]]
        w0 = (1.0 - fz[m3])[:, None]
        w1 = (fz[m3] - fx[m3])[:, None]
        w2 = (fx[m3] - fy[m3])[:, None]
        w3 = fy[m3][:, None]
        out[m3] = w0 * c000[m3] + w1 * c001 + w2 * c101 + w3 * c111[m3]

    if np.any(m4):
        c010 = grid[rx0[m4], gx1[m4], bx0[m4]]
        c110 = grid[rx1[m4], gx1[m4], bx0[m4]]
        w0 = (1.0 - fy[m4])[:, None]
        w1 = (fy[m4] - fx[m4])[:, None]
        w2 = (fx[m4] - fz[m4])[:, None]
        w3 = fz[m4][:, None]
        out[m4] = w0 * c000[m4] + w1 * c010 + w2 * c110 + w3 * c111[m4]

    if np.any(m5):
        c010 = grid[rx0[m5], gx1[m5], bx0[m5]]
        c011 = grid[rx0[m5], gx1[m5], bx1[m5]]
        w0 = (1.0 - fy[m5])[:, None]
        w1 = (fy[m5] - fz[m5])[:, None]
        w2 = (fz[m5] - fx[m5])[:, None]
        w3 = fx[m5][:, None]
        out[m5] = w0 * c000[m5] + w1 * c010 + w2 * c011 + w3 * c111[m5]

    if np.any(m6):
        c001 = grid[rx0[m6], gx0[m6], bx1[m6]]
        c011 = grid[rx0[m6], gx1[m6], bx1[m6]]
        w0 = (1.0 - fz[m6])[:, None]
        w1 = (fz[m6] - fy[m6])[:, None]
        w2 = (fy[m6] - fx[m6])[:, None]
        w3 = fx[m6][:, None]
        out[m6] = w0 * c000[m6] + w1 * c001 + w2 * c011 + w3 * c111[m6]

    return out.reshape(orig_shape + (3,))


def bake_1d_and_3d_to_3d(
    shaper: np.ndarray, grid3d: np.ndarray, target_size: int = 33
) -> np.ndarray:
    """将 1D Shaper + 3D LUT 精确烘焙为单一 3D LUT。"""
    axes = np.linspace(0.0, 1.0, target_size, dtype=np.float64)
    rr, gg, bb = np.meshgrid(axes, axes, axes, indexing="ij")
    pts = np.stack([rr, gg, bb], axis=-1)
    shaped = eval_1d_shaper(shaper, pts)
    return eval_tetrahedral(grid3d, shaped)


def parse_cube_file(path: str | Path) -> ParsedCube:
    """解析 Adobe / DaVinci Resolve .cube 文件并保持严谨的 R-fastest 晶格轴序。"""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"找不到 LUT 文件: {p}")

    raw_bytes = p.read_bytes()
    has_bom = raw_bytes.startswith(b"\xef\xbb\xbf")
    file_size = len(raw_bytes)
    text = raw_bytes.decode("utf-8-sig", errors="replace")
    has_non_ascii_text = any(ord(ch) > 127 for ch in text)
    has_non_ascii_filename = any(ord(ch) > 127 for ch in p.name)

    title = ""
    lut_1d_size: Optional[int] = None
    lut_3d_size: Optional[int] = None
    domain_min = np.array([0.0, 0.0, 0.0], dtype=np.float64)
    domain_max = np.array([1.0, 1.0, 1.0], dtype=np.float64)
    parse_warnings: List[str] = []

    triplets: List[List[float]] = []
    nan_count = 0
    inf_count = 0
    neg_zero_count = 0

    for line_no, raw_line in enumerate(text.splitlines(), start=1):
        # 去掉行内注释
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue

        upper = line.upper()
        if upper.startswith("TITLE"):
            m = re.match(r'^TITLE\s+"?(.*?)"?\s*$', line, flags=re.IGNORECASE)
            title = m.group(1) if m else line[5:].strip()
            continue
        if upper.startswith("LUT_1D_SIZE"):
            parts = line.split()
            lut_1d_size = int(parts[1])
            continue
        if upper.startswith("LUT_3D_SIZE"):
            parts = line.split()
            lut_3d_size = int(parts[1])
            continue
        if upper.startswith("DOMAIN_MIN"):
            parts = line.split()
            if len(parts) >= 4:
                domain_min = np.array([float(parts[1]), float(parts[2]), float(parts[3])])
            continue
        if upper.startswith("DOMAIN_MAX"):
            parts = line.split()
            if len(parts) >= 4:
                domain_max = np.array([float(parts[1]), float(parts[2]), float(parts[3])])
            continue
        if upper.startswith(("LUT_1D_INPUT_RANGE", "LUT_3D_INPUT_RANGE")):
            parts = line.split()
            if len(parts) >= 3:
                d0, d1 = float(parts[1]), float(parts[2])
                domain_min = np.array([d0, d0, d0])
                domain_max = np.array([d1, d1, d1])
            continue

        parts = line.split()
        if len(parts) != 3:
            parse_warnings.append(f"第 {line_no} 行无法识别为关键字或 RGB 三元组: {line[:60]}")
            continue

        row: List[float] = []
        for token in parts:
            tl = token.lower()
            if tl.startswith("-0.0") and all(c in "0." for c in tl[1:]):
                neg_zero_count += 1
            if "nan" in tl or "ind" in tl:
                nan_count += 1
                row.append(float("nan"))
            elif "inf" in tl:
                inf_count += 1
                row.append(float("-inf") if tl.startswith("-") else float("inf"))
            else:
                try:
                    row.append(float(token))
                except ValueError:
                    nan_count += 1
                    row.append(float("nan"))
                    parse_warnings.append(f"第 {line_no} 行含非法浮点字面量: {token}")
        triplets.append(row)

    arr = np.asarray(triplets, dtype=np.float64)
    expected_1d = lut_1d_size or 0
    expected_3d = (lut_3d_size ** 3) if lut_3d_size else 0
    expected_total = expected_1d + expected_3d

    if expected_total == 0:
        raise ValueError("未在 .cube 文件中找到有效的 LUT_3D_SIZE 或 LUT_1D_SIZE 声明。")

    if len(arr) != expected_total:
        parse_warnings.append(
            f"数据行数不匹配: 头声明期望 {expected_total} 行，实际解析 {len(arr)} 行。"
        )

    shaper_1d: Optional[np.ndarray] = None
    raw_grid_3d: Optional[np.ndarray] = None

    if lut_1d_size and len(arr) >= expected_1d:
        shaper_1d = arr[:expected_1d].copy()
        rem = arr[expected_1d:]
    else:
        rem = arr

    if lut_3d_size:
        if len(rem) < expected_3d:
            raise ValueError(
                f"3D LUT 数据不足: 期望 {expected_3d} 行 ({lut_3d_size}^3)，实际仅 {len(rem)} 行。"
            )
        block = rem[:expected_3d]
        # Adobe .cube 规范: Red 变化最快 (最内层), Green 其次, Blue 变化最慢 (最外层)
        # reshape((N, N, N, 3)) 的维度是 [b, g, r, ch]，转置 (2, 1, 0, 3) 转为 [r, g, b, ch]
        raw_grid_3d = np.transpose(
            block.reshape((lut_3d_size, lut_3d_size, lut_3d_size, 3)), (2, 1, 0, 3)
        ).copy()

    if raw_grid_3d is not None and shaper_1d is not None:
        safe_1d = np.nan_to_num(shaper_1d, nan=0.0, posinf=1.0, neginf=0.0)
        safe_3d = _repair_non_finite_grid(raw_grid_3d)
        safe_grid_3d = bake_1d_and_3d_to_3d(safe_1d, safe_3d, target_size=lut_3d_size)
        parse_warnings.append("检测到 1D+3D 复合 Shaper 结构，已自动在内部烘焙为等效 3D 晶格用于视觉与曲线质检。")
    elif raw_grid_3d is not None:
        safe_grid_3d = _repair_non_finite_grid(raw_grid_3d)
    elif shaper_1d is not None:
        safe_grid_3d = _promote_1d_to_3d(shaper_1d, target_size=33)
        parse_warnings.append("文件为纯 1D LUT，已提升为等效 33 点 3D LUT 进行统一检测。")
    else:
        raise ValueError("未能构建有效的 LUT 数据矩阵。")

    return ParsedCube(
        path=str(p.resolve()),
        title=title,
        lut_1d_size=lut_1d_size,
        lut_3d_size=lut_3d_size,
        domain_min=domain_min,
        domain_max=domain_max,
        has_bom=has_bom,
        has_non_ascii_text=has_non_ascii_text,
        has_non_ascii_filename=has_non_ascii_filename,
        file_size_bytes=file_size,
        nan_count=nan_count,
        inf_count=inf_count,
        negative_zero_count=neg_zero_count,
        shaper_1d=shaper_1d,
        raw_grid_3d=raw_grid_3d,
        safe_grid_3d=safe_grid_3d,
        parse_warnings=parse_warnings,
    )


def slog3_to_rec709_reference(pts: np.ndarray) -> np.ndarray:
    """
    平滑标准 Sony S-Log3 / S-Gamut3.Cine -> Rec.709 基准转换曲线（带柔和高光色调映射与亮部色域软收缩），
    既严格符合 S-Log3 物理黑位/18%灰锚点，又保证 3D 全空间平滑无折角、无三维折叠。
    """
    p = np.asarray(pts, dtype=np.float64)
    # 1) S-Log3 解码到线性反射率
    thresh = 171.2102946929 / 1023.0
    lin = np.where(
        p >= thresh,
        np.power(10.0, (p * 1023.0 - 420.0) / 261.5) * (0.18 + 0.01) - 0.01,
        (p * 1023.0 - 95.0) * 0.01125 / (171.2102946929 - 95.0),
    )
    # 光滑软零下界（避免 0 处一阶导折角）
    eps_zero = 0.002
    lin_pos = 0.5 * (lin + np.sqrt(lin * lin + eps_zero * eps_zero))

    # 2) 高光每通道与整体能量双重柔压缩（符合电影胶片高光柔和肩部与色域向白点收敛特性）
    #    18% 灰 (0.18) 保持接近 1:1，极亮超曝光区域平滑压制至合理线性域
    lin_ch_soft = lin_pos / (1.0 + 0.16 * lin_pos)
    luma_sgamut = 0.2432 * lin_ch_soft[..., 0] + 0.6915 * lin_ch_soft[..., 1] + 0.0653 * lin_ch_soft[..., 2]
    max_ch = np.max(lin_ch_soft, axis=-1)
    # 越接近极高饱和极亮色角点，越柔顺地向亮部中性灰收敛，防止矩阵产生超色域折返
    sat_keep = 0.68 / (1.0 + 0.42 * max_ch)
    lin_compressed = luma_sgamut[..., None] + sat_keep[..., None] * (lin_ch_soft - luma_sgamut[..., None])

    # 3) S-Gamut3.Cine -> Rec.709 线性矩阵
    m = np.array(
        [
            [1.64677897, -0.40598612, -0.24079285],
            [-0.12445175, 1.27073580, -0.14628405],
            [0.03721461, -0.17081803, 1.13360342],
        ],
        dtype=np.float64,
    )
    rec_lin = lin_compressed @ m.T
    # 光滑软正值保护（消除纯色域边缘硬折角与三维折叠）
    rec_lin = 0.5 * (rec_lin + np.sqrt(rec_lin * rec_lin + 0.0025 * 0.0025))

    # 4) 二次平滑高光滚降，稳稳收敛在 [0, 1] 内
    rec_tone = (rec_lin * (1.0 + 0.12 * rec_lin)) / (1.0 + 0.90 * rec_lin + 0.14 * rec_lin * rec_lin)
    rec_tone = np.clip(rec_tone, 0.0, 1.0)

    # 5) 平滑 Rec.709 OETF 编码
    rec_gamma = np.where(
        rec_tone < 0.018,
        4.5 * rec_tone,
        1.099 * np.power(np.maximum(rec_tone, 0.018), 0.45) - 0.099,
    )
    return np.clip(rec_gamma, 0.0, 1.0)


def luma_709(rgb: np.ndarray) -> np.ndarray:
    """Rec.709 亮度计算 Y = 0.2126 R + 0.7152 G + 0.0722 B。"""
    return 0.2126 * rgb[..., 0] + 0.7152 * rgb[..., 1] + 0.0722 * rgb[..., 2]


def chroma_spread(rgb: np.ndarray) -> np.ndarray:
    """通道极差法色度指标 max(R,G,B) - min(R,G,B)。"""
    return np.max(rgb, axis=-1) - np.min(rgb, axis=-1)


def check_hardware_and_range(
    parsed: ParsedCube, target: str
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """第 1 项检查：机内硬件规范兼容性、NaN/Inf 与 [0.0, 1.0] 值域越界检测。"""
    findings: List[Dict[str, Any]] = []
    grid_eval = parsed.raw_grid_3d if parsed.raw_grid_3d is not None else parsed.safe_grid_3d
    finite_mask = np.isfinite(grid_eval)
    finite_vals = grid_eval[finite_mask] if np.any(finite_mask) else np.array([0.0])

    min_val = float(np.min(finite_vals))
    max_val = float(np.max(finite_vals))
    neg_mask = finite_mask & (grid_eval < -1e-5)
    pos_over_mask = finite_mask & (grid_eval > 1.0 + 1e-5)
    neg_count = int(np.sum(neg_mask))
    pos_over_count = int(np.sum(pos_over_mask))
    total_scalars = int(grid_eval.size)

    neg_coords: List[List[float]] = []
    pos_over_coords: List[List[float]] = []
    n = grid_eval.shape[0]
    if neg_count > 0:
        idx = np.argwhere(np.any(neg_mask, axis=-1))[:5]
        neg_coords = [[round(float(c) / (n - 1), 4) for c in row] for row in idx]
    if pos_over_count > 0:
        idx = np.argwhere(np.any(pos_over_mask, axis=-1))[:5]
        pos_over_coords = [[round(float(c) / (n - 1), 4) for c in row] for row in idx]

    is_sony = target == "sony_cinealta"
    is_cam = target in ("sony_cinealta", "generic_camera")

    # 晶格尺寸与复合 Shaper
    if parsed.lut_1d_size and parsed.lut_3d_size:
        sev = "CRITICAL" if is_cam else "WARNING"
        findings.append(
            {
                "severity": sev,
                "category": "硬件格式兼容性 (1D+3D 复合 Shaper)",
                "symptom_impact": "机内加载失败或暗部/高光曲线完全错位",
                "evidence": f"检测到混合 LUT_1D_SIZE={parsed.lut_1d_size} 与 LUT_3D_SIZE={parsed.lut_3d_size}。",
                "dctl_advice": "Sony VENICE / FX6 / FX3 机内仅支持纯 3D LUT。请使用 sanitize_lut.py 将 1D+3D 烘焙合并为标准 33 点 3D .cube。",
            }
        )
    elif parsed.lut_3d_size is None:
        findings.append(
            {
                "severity": "CRITICAL" if is_cam else "WARNING",
                "category": "硬件格式兼容性 (仅 1D LUT)",
                "symptom_impact": "摄影机无法作为 User 3D LUT 加载",
                "evidence": f"文件仅包含 LUT_1D_SIZE={parsed.lut_1d_size}。",
                "dctl_advice": "请从 DaVinci Resolve 导出 33 Point 3D Cube，或使用 sanitize_lut.py 转换。",
            }
        )
    elif is_sony and parsed.lut_3d_size not in (17, 33):
        findings.append(
            {
                "severity": "CRITICAL",
                "category": "Sony 机内晶格尺寸不兼容",
                "symptom_impact": "VENICE / FX6 / FX3 无法导入或截断加载失败",
                "evidence": f"当前 LUT_3D_SIZE={parsed.lut_3d_size}（Sony 机内标准为 33 点，部分机型兼容 17 点）。",
                "dctl_advice": "在 Resolve 导出时选择 '33 Point Cube'，或运行 sanitize_lut.py --size 33 使用四面体高精度重采样转换。",
            }
        )

    # 非标准 DOMAIN
    if not (
        np.allclose(parsed.domain_min, 0.0, atol=1e-4)
        and np.allclose(parsed.domain_max, 1.0, atol=1e-4)
    ):
        findings.append(
            {
                "severity": "CRITICAL" if is_cam else "WARNING",
                "category": "非标准输入值域 (DOMAIN_MIN / DOMAIN_MAX)",
                "symptom_impact": "机内忽略或错位映射 DOMAIN 导致整体黑灰发闷或高光提早炸掉",
                "evidence": f"DOMAIN_MIN={parsed.domain_min.tolist()}, DOMAIN_MAX={parsed.domain_max.tolist()}。",
                "dctl_advice": "确保 Resolve 导出域为标准 [0.0, 1.0] 规范化域，不要使用带 Shaper 偏移的自定义 DOMAIN。",
            }
        )

    # BOM / 非 ASCII 检查
    if parsed.has_bom or parsed.has_non_ascii_text or parsed.has_non_ascii_filename:
        reasons = []
        if parsed.has_bom:
            reasons.append("含 UTF-8 BOM 头")
        if parsed.has_non_ascii_text:
            reasons.append("文件文本/TITLE 含非 ASCII 字符")
        if parsed.has_non_ascii_filename:
            reasons.append("文件名含非 ASCII 中文/特殊字符")
        findings.append(
            {
                "severity": "WARNING" if is_cam else "INFO",
                "category": "机内文件编码与命名兼容性",
                "symptom_impact": "Sony 读卡菜单可能无法识别文件或显示乱码",
                "evidence": "；".join(reasons) + "。",
                "dctl_advice": "上机前改用纯英文数字文件名（如 Look_SLog3_v1.cube），并以无 BOM ASCII 格式保存（sanitize_lut.py 会自动处理）。",
            }
        )

    # NaN / Inf
    if parsed.nan_count > 0 or parsed.inf_count > 0:
        findings.append(
            {
                "severity": "CRITICAL",
                "category": "非法浮点数值 (NaN / Inf)",
                "symptom_impact": "机内跳变出纯黑/纯白撕裂块噪点，严重者引发加载失败",
                "evidence": f"发现 NaN={parsed.nan_count} 个，Inf={parsed.inf_count} 个。",
                "dctl_advice": "典型成因是 DCTL 中 _powf(x, gamma) 传入负值或除零未加保护！请修改 DCTL 为 _powf(_fmaxf(x, 0.0f), gamma)，并用 sanitize_lut.py --fix-nan 修补。",
            }
        )

    # > 1.0 超白溢出（斑马纹最直接成因之一）
    if pos_over_count > 0:
        findings.append(
            {
                "severity": "CRITICAL" if is_cam else "WARNING",
                "category": "输出超界 > 1.0 (机内定点溢出回绕风险)",
                "symptom_impact": "【斑马纹/条纹元凶】在达芬奇32位视口显示纯白正常，套入 Sony 定点管线后 >1.0 可能溢出回绕为黑色/补色条纹！",
                "evidence": f"最大值 {max_val:.5f}，共 {pos_over_count}/{total_scalars} 个通道值超出 1.0（{pos_over_count/total_scalars*100:.2f}%），示例输入坐标: {pos_over_coords[:3]}。",
                "dctl_advice": "在 DCTL 末尾或 Resolve 最后一个节点加上 [0.0, 1.0] 钳位/高光柔滚降（_saturatef）；或运行 sanitize_lut.py --clamp 应急修复。",
            }
        )

    # < 0.0 负值溢出（极暗部闪点与噪点成因之一）
    if neg_count > 0:
        findings.append(
            {
                "severity": "CRITICAL" if is_cam else "WARNING",
                "category": "输出负值 < 0.0 (暗部无符号下溢闪点)",
                "symptom_impact": "【极暗部噪点/彩点元凶】负值在机内无符号定点硬件中会下溢回绕成 0.99+ 的高饱和彩色/白色随机闪点！",
                "evidence": f"最小值 {min_val:.5f}，共 {neg_count}/{total_scalars} 个通道值低于 0.0（{neg_count/total_scalars*100:.2f}%），示例输入坐标: {neg_coords[:3]}。",
                "dctl_advice": "S-Gamut3.Cine 转 Rec.709 矩阵或减法 Lift 易在暗部产生负值。请在 DCTL 输出前执行 _fmaxf(out, 0.0f)，或使用 sanitize_lut.py --clamp。",
            }
        )

    # Legal Levels 误导出检测 (仅作为明显特征提示)
    black_corner = parsed.safe_grid_3d[0, 0, 0]
    white_corner = parsed.safe_grid_3d[-1, -1, -1]
    if (
        0.055 <= np.mean(black_corner) <= 0.072
        and 0.905 <= np.mean(white_corner) <= 0.935
    ):
        findings.append(
            {
                "severity": "WARNING",
                "category": "疑似 Video / Legal Range (64-940) 压缩导出",
                "symptom_impact": "黑位被强制抬升至 ~0.063（发灰），高光被压在 ~0.919",
                "evidence": f"输入 (0,0,0) 输出={np.round(black_corner,4).tolist()}，(1,1,1) 输出={np.round(white_corner,4).tolist()}。",
                "dctl_advice": "请核查 DaVinci Resolve 中素材与时间线的 Data/Video Levels 设置，Sony 机内 3D LUT 标准应使用 Full Data [0.0, 1.0] 动态范围。",
            }
        )

    metrics = {
        "lut_1d_size": parsed.lut_1d_size,
        "lut_3d_size": parsed.lut_3d_size,
        "effective_grid_size": int(parsed.safe_grid_3d.shape[0]),
        "domain_min": [round(float(x), 6) for x in parsed.domain_min],
        "domain_max": [round(float(x), 6) for x in parsed.domain_max],
        "has_bom": parsed.has_bom,
        "has_non_ascii_text": parsed.has_non_ascii_text,
        "has_non_ascii_filename": parsed.has_non_ascii_filename,
        "nan_count": parsed.nan_count,
        "inf_count": parsed.inf_count,
        "negative_zero_count": parsed.negative_zero_count,
        "min_value": round(min_val, 6),
        "max_value": round(max_val, 6),
        "below_zero_count": neg_count,
        "above_one_count": pos_over_count,
        "below_zero_pct": round(neg_count / max(total_scalars, 1) * 100.0, 3),
        "above_one_pct": round(pos_over_count / max(total_scalars, 1) * 100.0, 3),
        "black_corner_out": [round(float(x), 5) for x in black_corner],
        "white_corner_out": [round(float(x), 5) for x in white_corner],
    }
    return metrics, findings


def check_monotonicity_and_zebra(
    grid: np.ndarray, profile: CameraProfile
) -> Tuple[Dict[str, Any], List[Dict[str, Any]], Dict[str, np.ndarray]]:
    """
    第 2 项检查：单调性反转 (Solarization)、高频锯齿振荡 (Zebra Oscillation) 与硬阶跃断层。
    """
    findings: List[Dict[str, Any]] = []
    n_samples = 512
    t = np.linspace(0.0, 1.0, n_samples, dtype=np.float64)
    dt = t[1] - t[0]

    ramps: Dict[str, Tuple[str, np.ndarray]] = {
        "neutral_gray": ("中性灰斜坡", np.stack([t, t, t], axis=-1)),
        "warm_skin": (
            "暖肤色曝光斜坡",
            np.stack([t, np.clip(0.78 * t, 0, 1), np.clip(0.58 * t, 0, 1)], axis=-1),
        ),
        "red_ramp": ("纯红通道斜坡", np.stack([t, np.zeros_like(t), np.zeros_like(t)], axis=-1)),
        "green_ramp": ("纯绿通道斜坡", np.stack([np.zeros_like(t), t, np.zeros_like(t)], axis=-1)),
        "blue_ramp": ("纯蓝通道斜坡", np.stack([np.zeros_like(t), np.zeros_like(t), t], axis=-1)),
        "sat_orange_ramp": (
            "高饱和橙红曝光斜坡",
            np.stack([np.clip(t * 1.15, 0, 1), np.clip(t * 0.65, 0, 1), np.clip(t * 0.15, 0, 1)], axis=-1),
        ),
        "sat_cyan_ramp": (
            "高饱和青绿曝光斜坡",
            np.stack([np.clip(t * 0.15, 0, 1), np.clip(t * 0.95, 0, 1), np.clip(t * 1.10, 0, 1)], axis=-1),
        ),
        "sat_magenta_ramp": (
            "高饱和洋红曝光斜坡",
            np.stack([np.clip(t * 1.10, 0, 1), np.clip(t * 0.18, 0, 1), np.clip(t * 0.95, 0, 1)], axis=-1),
        ),
    }

    reversal_events: List[Dict[str, Any]] = []
    oscillation_events: List[Dict[str, Any]] = []
    step_events: List[Dict[str, Any]] = []
    curve_cache: Dict[str, np.ndarray] = {"t": t}

    for key, (label, pts) in ramps.items():
        # 使用三线性插值模拟机内硬件真实曲线
        out = eval_trilinear(grid, pts)
        y = luma_709(out)
        dy = np.diff(y)
        slope = dy / dt
        d2y = np.diff(slope)

        if key == "neutral_gray":
            curve_cache["gray_rgb"] = out
            curve_cache["gray_y"] = y
            curve_cache["gray_slope"] = slope
            curve_cache["gray_tet_y"] = luma_709(eval_tetrahedral(grid, pts))

        # 1) 亮度单调性反转检测: dY 明显小于 0
        rev_idx = np.where(dy < -0.0012)[0]
        if rev_idx.size > 0:
            # 将连续区间聚合
            total_drop = float(-np.sum(dy[rev_idx]))
            min_slope = float(np.min(slope[rev_idx]))
            t_start = float(t[rev_idx[0]])
            t_end = float(t[rev_idx[-1] + 1])
            if total_drop > 0.006 or min_slope < -0.6:
                reversal_events.append(
                    {
                        "ramp": label,
                        "key": key,
                        "t_range": [round(t_start, 3), round(t_end, 3)],
                        "total_luma_drop": round(total_drop, 4),
                        "min_slope": round(min_slope, 3),
                        "points": int(rev_idx.size),
                    }
                )

        # 2) 斑马纹振荡波纹检测：兼顾 512 点像素锯齿 与 晶格尺度多峰起伏（局部极值拐点 >= 2 且伴随反相跌落）
        significant = np.abs(dy) > 0.0018
        flips = (dy[:-1] * dy[1:] < 0) & significant[:-1] & significant[1:]
        flip_count = int(np.sum(flips))

        # 晶格尺度 (降采样到 33 点步长间隔) 检查趋势反转次数，精准捕捉 LUT 晶格级 Zebra 波纹
        stride = max(1, n_samples // max(grid.shape[0] - 1, 16))
        y_coarse = y[::stride]
        dy_coarse = np.diff(y_coarse)
        sig_c = np.abs(dy_coarse) > 0.004
        coarse_flips = (dy_coarse[:-1] * dy_coarse[1:] < 0) & sig_c[:-1] & sig_c[1:]
        coarse_flip_count = int(np.sum(coarse_flips))

        if flip_count >= 2 or coarse_flip_count >= 2:
            if coarse_flip_count >= 2:
                idx_c = np.where(coarse_flips)[0]
                t_c = t[::stride]
                t0, t1 = float(t_c[idx_c[0]]), float(t_c[idx_c[-1] + 2 if idx_c[-1] + 2 < len(t_c) else -1])
                max_j = float(np.max(np.abs(dy_coarse[idx_c])))
                total_flips = coarse_flip_count
            else:
                idx_f = np.where(flips)[0]
                t0, t1 = float(t[idx_f[0]]), float(t[idx_f[-1] + 1])
                max_j = float(np.max(np.abs(dy[idx_f])))
                total_flips = flip_count
            oscillation_events.append(
                {
                    "ramp": label,
                    "key": key,
                    "flip_count": total_flips,
                    "t_range": [round(t0, 3), round(t1, 3)],
                    "max_jump": round(max_j, 4),
                }
            )

        # 3) 孤立阶跃/二阶导数异常尖峰（未平滑 if-else 或 Zebra/False Color 硬跳变）
        step_thresh = 0.035
        big_jumps = np.where(np.abs(dy) > step_thresh)[0]
        if big_jumps.size > 0:
            step_events.append(
                {
                    "ramp": label,
                    "t_at": [round(float(t[i]), 3) for i in big_jumps[:4]],
                    "max_step_dy": round(float(np.max(np.abs(dy[big_jumps]))), 4),
                    "max_d2y": round(float(np.max(np.abs(d2y))) if d2y.size else 0.0, 2),
                }
            )

    # 3D 晶格原生轴向反转扫描 (直接查每个晶格沿 R/G/B 递增值是否反转)
    n = grid.shape[0]
    dr_r = np.diff(grid[:, :, :, 0], axis=0)
    dg_g = np.diff(grid[:, :, :, 1], axis=1)
    db_b = np.diff(grid[:, :, :, 2], axis=2)
    axial_rev_r = int(np.sum(dr_r < -0.025))
    axial_rev_g = int(np.sum(dg_g < -0.025))
    axial_rev_b = int(np.sum(db_b < -0.025))
    axial_rev_total = axial_rev_r + axial_rev_g + axial_rev_b
    axial_total_edges = 3 * (n - 1) * n * n

    if reversal_events:
        worst = max(reversal_events, key=lambda x: x["total_luma_drop"])
        findings.append(
            {
                "severity": "CRITICAL" if worst["total_luma_drop"] > 0.015 or len(reversal_events) >= 2 else "WARNING",
                "category": "高光/饱和度单调性反转 (Solarization)",
                "symptom_impact": "【斑马纹/等高线条纹元凶】输入亮度升高时输出亮度突然反向跌落，导致渐变高光或亮色带出现暗色条纹！",
                "evidence": f"在 {len(reversal_events)} 条测试斜坡发现反转，最严重为 [{worst['ramp']}] 输入区间 {worst['t_range']}（累计亮度倒跌 {worst['total_luma_drop']:.4f}，最低斜率 {worst['min_slope']:.2f}）。",
                "dctl_advice": "重点排查减法饱和度 (Subtractive Sat) / 密度 / Tetrahedral Warper DCTL！减小其高亮通道权重，或在进入 DCTL 前加 CST 开启 Tone & Gamut Mapping。",
            }
        )

    if oscillation_events:
        worst_osc = max(oscillation_events, key=lambda x: x["flip_count"])
        findings.append(
            {
                "severity": "CRITICAL",
                "category": "高频锯齿振荡 (Zebra / Banding 波形)",
                "symptom_impact": "【典型斑马纹】输出曲线发生连续正负反复震荡，上机后会在连续影调中渲染出明暗交替的斑马条纹。",
                "evidence": f"在 [{worst_osc['ramp']}] 区间 {worst_osc['t_range']} 检测到 {worst_osc['flip_count']} 次正负交替锯齿翻转（单步跃迁最大 {worst_osc['max_jump']:.4f}）。",
                "dctl_advice": "检查是否忘记关闭 DCTL 的 Zebra / False Color / Gamut Warning 开关，或存在硬阈值分支与过激曲线震荡；可尝试 sanitize_lut.py --de-oscillate 做应急阻尼验证。",
            }
        )

    if step_events:
        worst_step = max(step_events, key=lambda x: x["max_step_dy"])
        findings.append(
            {
                "severity": "WARNING",
                "category": "曲线硬阶跃断层 (硬阈值跳变)",
                "symptom_impact": "画面在特定亮度阈值处突然跳色或裂开明显色阶条带",
                "evidence": f"[{worst_step['ramp']}] 在输入 {worst_step['t_at']} 处单步亮度陡跳 ΔY={worst_step['max_step_dy']:.4f}。",
                "dctl_advice": "排查 DCTL 中未经 smoothstep 平滑的 if-else 亮度分段代码，或存在局部色域硬切开关。",
            }
        )

    if axial_rev_total > 0 and not reversal_events:
        findings.append(
            {
                "severity": "WARNING",
                "category": "3D 晶格局部轴向反转",
                "symptom_impact": "高饱和彩色过渡区易出现色相/明度反相条纹",
                "evidence": f"沿 R/G/B 主轴递增时共 {axial_rev_total}/{axial_total_edges} 条晶格边出现明显负增量 (<-0.025)，分布 R:{axial_rev_r}, G:{axial_rev_g}, B:{axial_rev_b}。",
                "dctl_advice": "减少高饱和通道间的交叉相减力度，避免纯色通道反相跌落。",
            }
        )

    metrics = {
        "reversal_events": reversal_events,
        "oscillation_events": oscillation_events,
        "step_events": step_events,
        "axial_reversals": {
            "R": axial_rev_r,
            "G": axial_rev_g,
            "B": axial_rev_b,
            "total": axial_rev_total,
            "ratio_pct": round(axial_rev_total / max(axial_total_edges, 1) * 100.0, 3),
        },
        "gray_max_slope": round(float(np.max(curve_cache["gray_slope"])), 3),
        "gray_min_slope": round(float(np.min(curve_cache["gray_slope"])), 3),
    }
    return metrics, findings, curve_cache


def check_jacobian_folding(grid: np.ndarray) -> Tuple[Dict[str, Any], List[Dict[str, Any]], np.ndarray]:
    """第 3 项检查：3D 晶格雅可比行列式 det(J) 检测空间自相交与色彩折叠。"""
    findings: List[Dict[str, Any]] = []
    n = grid.shape[0]
    h = 1.0 / (n - 1)

    # 体素前向有限差分 J 矩阵 [r, g, b, out_ch, in_axis]
    dr = grid[1:, :-1, :-1, :] - grid[:-1, :-1, :-1, :]
    dg = grid[:-1, 1:, :-1, :] - grid[:-1, :-1, :-1, :]
    db = grid[:-1, :-1, 1:, :] - grid[:-1, :-1, :-1, :]

    # 解析计算 3x3 行列式 det([dr, dg, db]) 并按步长立方归一化
    cross_gb = np.cross(dg, db, axis=-1)
    det_raw = np.sum(dr * cross_gb, axis=-1)
    det_norm = det_raw / (h ** 3)

    # 容忍极微小数值误差，真正负数代表空间折叠翻转
    folded_mask = det_norm < -0.02
    folded_count = int(np.sum(folded_mask))
    total_voxels = int(det_norm.size)
    min_det = float(np.min(det_norm))
    max_det = float(np.max(det_norm))

    worst_coord: Optional[List[float]] = None
    if folded_count > 0:
        idx = np.unravel_index(int(np.argmin(det_norm)), det_norm.shape)
        worst_coord = [round(float(c) / (n - 1), 4) for c in idx]
        findings.append(
            {
                "severity": "CRITICAL" if folded_count / total_voxels > 0.005 or min_det < -0.5 else "WARNING",
                "category": "3D 色彩空间折叠 (Jacobian det(J) < 0)",
                "symptom_impact": "【斑马纹/撕裂条纹元凶】LUT 立方体体素发生三维翻转穿插，插值路径在体素内混乱折返，产生网状/条纹裂变！",
                "evidence": f"共 {folded_count}/{total_voxels} 个体素 ({folded_count/total_voxels*100:.3f}%) 发生空间折叠，最小归一化 det(J)={min_det:.4f}，最严重坐标≈{worst_coord}。",
                "dctl_advice": "排查高曲率 Hue-vs-Hue / Tetrahedral Color Warper / 极度色相挤压 DCTL，减小色相交界处的扭曲幅度。",
            }
        )

    metrics = {
        "folded_voxels": folded_count,
        "total_voxels": total_voxels,
        "folded_pct": round(folded_count / max(total_voxels, 1) * 100.0, 4),
        "min_normalized_det_j": round(min_det, 4),
        "max_normalized_det_j": round(max_det, 4),
        "worst_folded_coord": worst_coord,
    }
    return metrics, findings, det_norm


def check_extreme_shadows(
    grid: np.ndarray, profile: CameraProfile
) -> Tuple[Dict[str, Any], List[Dict[str, Any]], Dict[str, Any]]:
    """
    第 4 项检查：极暗部与黑电平噪点专项深度分析 + 传感器底噪蒙特卡洛仿真。
    重点捕捉 Sony S-Log3 0% 黑电平 (95/1023 ≈ 0.09286) 附近的斜率尖峰与色度爆炸。
    """
    findings: List[Dict[str, Any]] = []
    n = grid.shape[0]
    step = 1.0 / (n - 1)

    # 1) 高密度暗部中性灰轴分析 [0.0, shadow_upper]
    shadow_hi = max(profile.shadow_zone[1], profile.black_point + 0.08)
    t_shad = np.linspace(0.0, shadow_hi, 256, dtype=np.float64)
    pts_shad = np.stack([t_shad, t_shad, t_shad], axis=-1)
    out_shad = eval_trilinear(grid, pts_shad)
    y_shad = luma_709(out_shad)
    dy_shad = np.diff(y_shad) / (t_shad[1] - t_shad[0])
    dch_shad = np.diff(out_shad, axis=0) / (t_shad[1] - t_shad[0])

    max_shadow_luma_slope = float(np.max(dy_shad))
    max_shadow_ch_slope = float(np.max(dch_shad))
    max_slope_t = float(t_shad[int(np.argmax(dy_shad))])

    # 暗部灰阶偏色 (中性灰输入居然变成明显彩色)
    shadow_axis_chroma = chroma_spread(out_shad)
    max_axis_chroma = float(np.max(shadow_axis_chroma))
    max_axis_chroma_t = float(t_shad[int(np.argmax(shadow_axis_chroma))])

    # 2) 近中性灰轴极暗部晶格“除零色度爆炸”扫描
    # 检查晶格点: 均值 <= shadow_hi + 0.02，且偏离中性灰轴 <= 2 个步长
    axes = np.linspace(0.0, 1.0, n, dtype=np.float64)
    rr, gg, bb = np.meshgrid(axes, axes, axes, indexing="ij")
    in_mean = (rr + gg + bb) / 3.0
    in_chroma = np.maximum(np.maximum(rr, gg), bb) - np.minimum(np.minimum(rr, gg), bb)

    near_axis_shadow_mask = (in_mean <= shadow_hi + 0.025) & (in_chroma > 1e-5) & (in_chroma <= 2.0 * step + 1e-5)
    out_chroma_grid = chroma_spread(grid)

    # 色度放大比 = 输出色度 / 输入色度（剔除纯灰偏色本底后评估局部敏感度）
    chroma_gain_map = np.zeros_like(out_chroma_grid)
    chroma_gain_map[near_axis_shadow_mask] = out_chroma_grid[near_axis_shadow_mask] / np.maximum(
        in_chroma[near_axis_shadow_mask], step * 0.5
    )

    max_near_axis_chroma = 0.0
    max_chroma_gain = 0.0
    worst_chroma_coord: Optional[List[float]] = None
    worst_chroma_out: Optional[List[float]] = None
    explosion_voxel_count = 0

    if np.any(near_axis_shadow_mask):
        masked_gains = chroma_gain_map[near_axis_shadow_mask]
        masked_ch = out_chroma_grid[near_axis_shadow_mask]
        max_near_axis_chroma = float(np.max(masked_ch))
        max_chroma_gain = float(np.max(masked_gains))
        explosion_voxel_count = int(np.sum((masked_gains > 3.2) & (masked_ch > 0.075)))

        flat_idx = int(np.argmax(chroma_gain_map))
        idx = np.unravel_index(flat_idx, chroma_gain_map.shape)
        worst_chroma_coord = [round(float(axes[c]), 4) for c in idx]
        worst_chroma_out = [round(float(x), 4) for x in grid[idx]]

    # 按亮度带绘制近轴色度爆炸曲线 (供给图表子图3)
    band_luma = []
    band_max_chroma = []
    for i in range(n):
        if axes[i] > 0.25:
            break
        slice_mask = (
            (np.abs(in_mean - axes[i]) <= step * 0.6)
            & (in_chroma > 1e-5)
            & (in_chroma <= 1.5 * step + 1e-5)
        )
        band_luma.append(float(axes[i]))
        if np.any(slice_mask):
            band_max_chroma.append(float(np.max(out_chroma_grid[slice_mask])))
        else:
            band_max_chroma.append(float(chroma_spread(grid[i : i + 1, i, i])[0]))

    # 3) 极暗部零点跳变 (0,0,0) -> (1步长)
    zero_step_jump = float(
        np.max(
            [
                np.linalg.norm(grid[1, 0, 0] - grid[0, 0, 0]),
                np.linalg.norm(grid[0, 1, 0] - grid[0, 0, 0]),
                np.linalg.norm(grid[0, 0, 1] - grid[0, 0, 0]),
                np.linalg.norm(grid[1, 1, 1] - grid[0, 0, 0]),
            ]
        )
    )

    # 4) 蒙特卡洛传感器底噪仿真 (黑电平附近生成 64x64 微弱高斯噪声块，sigma=0.0035 ≈ 10-bit ±3.5 码值)
    rng = np.random.default_rng(42)
    noise_sigma = 0.0035
    patch_h, patch_w = 64, 64
    base_level = max(profile.black_point, 0.04)
    # 生成一个稍微带渐变微弱纹理和随机热噪的测试块（集中在黑位+0.015）
    noise_block = rng.normal(loc=0.0, scale=noise_sigma, size=(patch_h, patch_w, 3))
    test_center = np.array([base_level + 0.012, base_level + 0.012, base_level + 0.012])
    noisy_in = np.clip(test_center[None, None, :] + noise_block, 0.0, 1.0)

    # 基准参考平滑曲线输出
    ref_out = slog3_to_rec709_reference(noisy_in)
    ref_luma_std = float(np.std(luma_709(ref_out)))
    ref_chroma_std = float(
        np.sqrt(
            (
                np.var(ref_out[..., 0] - ref_out[..., 1])
                + np.var(ref_out[..., 1] - ref_out[..., 2])
                + np.var(ref_out[..., 2] - ref_out[..., 0])
            )
            / 3.0
        )
    )

    # 被检 LUT 模拟机内三线性输出（含模拟硬件越界回绕，以真实还原上机情况）
    lut_raw_out = eval_trilinear(grid, noisy_in)
    # 若有越界，记录真实硬件无符号回绕版与截断版
    has_wrap_risk = bool(np.any((lut_raw_out < -1e-4) | (lut_raw_out > 1.0 + 1e-4)))
    lut_hw_sim = np.mod(lut_raw_out, 1.0) if has_wrap_risk else np.clip(lut_raw_out, 0.0, 1.0)
    lut_luma_std = float(np.std(luma_709(lut_hw_sim)))
    lut_chroma_std = float(
        np.sqrt(
            (
                np.var(lut_hw_sim[..., 0] - lut_hw_sim[..., 1])
                + np.var(lut_hw_sim[..., 1] - lut_hw_sim[..., 2])
                + np.var(lut_hw_sim[..., 2] - lut_hw_sim[..., 0])
            )
            / 3.0
        )
    )

    luma_noise_amp_vs_ref = lut_luma_std / max(ref_luma_std, 1e-6)
    chroma_noise_amp_vs_ref = lut_chroma_std / max(ref_chroma_std, 1e-6)
    chroma_gain_over_input = lut_chroma_std / noise_sigma

    # 判决归因
    if explosion_voxel_count > 0 or max_chroma_gain > 3.5 or chroma_noise_amp_vs_ref > 2.6:
        findings.append(
            {
                "severity": "CRITICAL",
                "category": "极暗部近轴色度爆炸 (Shadow Chroma Explosion)",
                "symptom_impact": "【极暗部彩噪/杂色斑元凶】在黑电平附近微小传感器热噪触发极高色度增益，画面暗部出现蠕动红/绿/蓝彩噪颗粒！",
                "evidence": (
                    f"在输入暗部近灰轴检测到 {explosion_voxel_count} 个色度爆炸体素，"
                    f"最大近轴色度增益={max_chroma_gain:.2f}x（输出色差达 {max_near_axis_chroma:.3f}，坐标 {worst_chroma_coord} -> 输出 {worst_chroma_out}）；"
                    f"黑位底噪仿真显示色度噪点相对标准曲线放大 {chroma_noise_amp_vs_ref:.2f} 倍！"
                ),
                "dctl_advice": "典型成因是 DCTL 在暗部计算饱和度/球面坐标时分母接近 0（如 1/(L+1e-4)）。请在 DCTL 内对暗部分母加安全下限（或 smoothstep 渐弱暗部饱和度），并在 Resolve 暗部做 Shadow Desat；应急上机可用 sanitize_lut.py --tame-shadow-chroma。",
            }
        )

    if max_shadow_luma_slope > 7.0 or luma_noise_amp_vs_ref > 2.4:
        findings.append(
            {
                "severity": "CRITICAL" if max_shadow_luma_slope > 10.5 or luma_noise_amp_vs_ref > 3.5 else "WARNING",
                "category": "极暗部亮度增益斜率尖峰 (Shadow Slope Spike)",
                "symptom_impact": "【极暗部颗粒粗噪点元凶】黑电平附近的微弱传感底噪被陡峭斜率等比例放大，产生明显暗部亮度颗粒感。",
                "evidence": f"极暗部 (0.0~{shadow_hi:.2f}) 最大亮度斜率={max_shadow_luma_slope:.2f} (在输入 {max_slope_t:.3f} 处)，单通道最大斜率={max_shadow_ch_slope:.2f}，黑位亮度底噪相对标准曲线放大 {luma_noise_amp_vs_ref:.2f} 倍。",
                "dctl_advice": "检查是否重复叠加了两次 S-Log3->709 转换或暗部 Contrast/Pivot 过激；建议放松阴影 Toe 对比度并核对 S-Log3 黑位 (95/1023) 对应斜率。",
            }
        )

    if max_axis_chroma > 0.045:
        findings.append(
            {
                "severity": "WARNING",
                "category": "极暗部中性灰偏色 (Shadow Gray Tint)",
                "symptom_impact": "纯黑与暗灰背景出现明显偏色或底噪染色",
                "evidence": f"中性灰轴在极暗部输入 {max_axis_chroma_t:.3f} 处最大通道偏离={max_axis_chroma:.4f}。",
                "dctl_advice": "核查 DCTL 暗部 Split-Tone 或 Lift 色轮偏移是否过重。",
            }
        )

    if zero_step_jump > 0.25:
        findings.append(
            {
                "severity": "WARNING",
                "category": "黑点原点 (0,0,0) 邻域突变",
                "symptom_impact": "最深黑位与相邻晶格跳变过大，触发机内死黑噪点断层",
                "evidence": f"(0,0,0) 至一阶邻格最大欧氏跳变 ΔRGB={zero_step_jump:.4f}。",
                "dctl_advice": "检查 DCTL 在 RGB=0 处是否存在分母除零或分支跳变。",
            }
        )

    metrics = {
        "max_shadow_luma_slope": round(max_shadow_luma_slope, 3),
        "max_shadow_channel_slope": round(max_shadow_ch_slope, 3),
        "max_slope_input_t": round(max_slope_t, 4),
        "max_shadow_axis_chroma": round(max_axis_chroma, 4),
        "max_near_axis_chroma": round(max_near_axis_chroma, 4),
        "max_shadow_chroma_gain": round(max_chroma_gain, 3),
        "chroma_explosion_voxels": explosion_voxel_count,
        "worst_chroma_coord": worst_chroma_coord,
        "worst_chroma_out": worst_chroma_out,
        "zero_step_jump": round(zero_step_jump, 4),
        "monte_carlo_noise_sim": {
            "test_center_input": round(float(test_center[0]), 4),
            "input_sigma": noise_sigma,
            "ref_luma_std": round(ref_luma_std, 5),
            "lut_luma_std": round(lut_luma_std, 5),
            "luma_noise_amp_vs_ref": round(luma_noise_amp_vs_ref, 2),
            "ref_chroma_std": round(ref_chroma_std, 5),
            "lut_chroma_std": round(lut_chroma_std, 5),
            "chroma_noise_amp_vs_ref": round(chroma_noise_amp_vs_ref, 2),
            "chroma_gain_over_input": round(chroma_gain_over_input, 2),
            "hw_wrap_triggered": has_wrap_risk,
        },
    }
    sim_artifacts = {
        "t_shad": t_shad,
        "out_shad": out_shad,
        "dy_shad": dy_shad,
        "band_luma": np.array(band_luma),
        "band_max_chroma": np.array(band_max_chroma),
        "ref_patch": np.clip(ref_out, 0.0, 1.0),
        "lut_patch": np.clip(lut_hw_sim, 0.0, 1.0),
        "luma_amp": luma_noise_amp_vs_ref,
        "chroma_amp": chroma_noise_amp_vs_ref,
    }
    return metrics, findings, sim_artifacts


def check_interpolation_divergence(
    grid: np.ndarray,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]], np.ndarray]:
    """第 5 项检查：达芬奇 Tetrahedral 四面体 vs 摄影机 Trilinear 三线性插值分歧。"""
    findings: List[Dict[str, Any]] = []
    n = grid.shape[0]
    centers = (np.arange(n - 1, dtype=np.float64) + 0.5) / (n - 1)
    rr, gg, bb = np.meshgrid(centers, centers, centers, indexing="ij")
    pts = np.stack([rr, gg, bb], axis=-1)

    out_tri = eval_trilinear(grid, pts)
    out_tet = eval_tetrahedral(grid, pts)
    diff = np.linalg.norm(out_tet - out_tri, axis=-1)

    max_diff = float(np.max(diff))
    p99_diff = float(np.percentile(diff, 99))
    mean_diff = float(np.mean(diff))
    worst_idx = np.unravel_index(int(np.argmax(diff)), diff.shape)
    worst_coord = [round(float(centers[c]), 4) for c in worst_idx]

    if max_diff > 0.045 or p99_diff > 0.015:
        findings.append(
            {
                "severity": "WARNING" if max_diff < 0.09 else "CRITICAL",
                "category": "四面体 vs 三线性插值严重分歧",
                "symptom_impact": "【所见非所得条纹】在 Resolve 默认四面体插值下看似正常，导入三线性插值的机内监看后却裂开色阶或条带！",
                "evidence": f"体素中心最大差异 ΔRGB={max_diff:.4f} (位于 {worst_coord})，P99 差异={p99_diff:.4f}。",
                "dctl_advice": "在 DaVinci Resolve 的 Project Settings 中将 '3D Lookup Table Interpolation' 临时切为 Trilinear 即可在监看器上复现该差异，适当缓和该色彩区域的局部急转斜率。",
            }
        )

    # 沿中性灰和纯红测试一条插值差异曲线供图表绘制
    t = np.linspace(0.0, 1.0, 256)
    ramp_pts = np.stack([t, np.clip(0.55 * t, 0, 1), np.clip(0.25 * t, 0, 1)], axis=-1)
    ramp_diff = np.linalg.norm(
        eval_tetrahedral(grid, ramp_pts) - eval_trilinear(grid, ramp_pts), axis=-1
    )

    metrics = {
        "max_divergence_delta_rgb": round(max_diff, 5),
        "p99_divergence_delta_rgb": round(p99_diff, 5),
        "mean_divergence_delta_rgb": round(mean_diff, 5),
        "worst_coord": worst_coord,
    }
    return metrics, findings, ramp_diff


def check_discontinuities_and_clipping(
    grid: np.ndarray,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """第 6 项检查：邻格跃迁断层与高光硬削波偏色。"""
    findings: List[Dict[str, Any]] = []
    n = grid.shape[0]
    dr = np.linalg.norm(grid[1:, :, :] - grid[:-1, :, :], axis=-1)
    dg = np.linalg.norm(grid[:, 1:, :] - grid[:, :-1, :], axis=-1)
    db = np.linalg.norm(grid[:, :, 1:] - grid[:, :, :-1], axis=-1)
    max_jump = float(max(np.max(dr), np.max(dg), np.max(db)))
    p999_jump = float(np.percentile(np.concatenate([dr.ravel(), dg.ravel(), db.ravel()]), 99.9))

    # 寻找最大跳变所在边
    if np.max(dr) >= np.max(dg) and np.max(dr) >= np.max(db):
        idx = np.unravel_index(int(np.argmax(dr)), dr.shape)
        jump_coord = [round(float(c) / (n - 1), 4) for c in idx]
        jump_axis = "R"
    elif np.max(dg) >= np.max(db):
        idx = np.unravel_index(int(np.argmax(dg)), dg.shape)
        jump_coord = [round(float(c) / (n - 1), 4) for c in idx]
        jump_axis = "G"
    else:
        idx = np.unravel_index(int(np.argmax(db)), db.shape)
        jump_coord = [round(float(c) / (n - 1), 4) for c in idx]
        jump_axis = "B"

    # 高光硬削波检测（单通道提前顶到 >=0.999 而其他通道继续上升导致偏色）
    high_slice = grid[int(n * 0.65) :, int(n * 0.65) :, int(n * 0.65) :]
    clipped_ratio = float(np.mean(np.any(high_slice >= 0.999, axis=-1)))

    if max_jump > 0.36:
        findings.append(
            {
                "severity": "WARNING" if max_jump < 0.50 else "CRITICAL",
                "category": "晶格邻域剧烈断层 (Grid Discontinuity)",
                "symptom_impact": "图像在该颜色附近产生像素撕裂、噪点块或突兀色阶线",
                "evidence": f"沿 {jump_axis} 轴在输入 {jump_coord} 附近单格跳变 ΔRGB={max_jump:.4f} (P99.9={p999_jump:.4f})。",
                "dctl_advice": "检查是否因 DCTL 分支突变、除零或之前存在 NaN 修补留下的硬台阶。",
            }
        )

    metrics = {
        "max_adjacent_delta_rgb": round(max_jump, 4),
        "p999_adjacent_delta_rgb": round(p999_jump, 4),
        "worst_jump_axis": jump_axis,
        "worst_jump_coord": jump_coord,
        "highlight_clipped_cube_ratio_pct": round(clipped_ratio * 100.0, 2),
    }
    return metrics, findings


def summarize_and_score(all_findings: List[Dict[str, Any]]) -> Tuple[int, str]:
    """根据所有发现汇总健康得分 (0-100) 与 PASS / WARN / FAIL 评级。"""
    score = 100
    crit_count = 0
    warn_count = 0
    for f in all_findings:
        sev = f["severity"]
        if sev == "CRITICAL":
            crit_count += 1
            score -= 22
        elif sev == "WARNING":
            warn_count += 1
            score -= 8
        else:
            score -= 2
    score = max(0, min(100, score))
    if crit_count > 0 or score < 60:
        status = "FAIL"
    elif warn_count > 0 or score < 88:
        status = "WARN"
    else:
        status = "PASS"
    return score, status


def _build_stress_test_image(profile: CameraProfile) -> Tuple[np.ndarray, Dict[str, slice]]:
    """
    构建专业“合成全动态范围压力测试图”(360x720 RGB, 0~1)，覆盖四大高危区间：
      1) S-Log3 黑电平附近极暗部带微弱传感器底噪阶梯条 (诱发极暗部噪点/彩噪)
      2) 全范围平滑中性灰梯度 (检测反转/单调性/偏色)
      3) 高光暖肤色渐变带 (检测高光斑马纹/Solarization)
      4) 全色相高饱和渐变矩阵 (检测色域折叠/斑马纹/插值裂变)
    """
    h, w = 360, 720
    img = np.zeros((h, w, 3), dtype=np.float64)
    x = np.linspace(0.0, 1.0, w, dtype=np.float64)

    # 条带 A (0..90): 极暗部 S-Log3 黑位周围 [0.04, 0.22] 水平渐变 + 真实微弱传感器底噪
    rng = np.random.default_rng(1024)
    s_a = slice(0, 90)
    ramp_dark = profile.sub_black_zone[0] + 0.04 + x * 0.18
    dark_base = np.broadcast_to(ramp_dark[None, :, None], (90, w, 3)).copy()
    # 上半段带微弱底噪 (sigma=0.004)，下半段纯净暗阶渐变便于对比
    dark_base[:55] += rng.normal(0.0, 0.004, size=(55, w, 3))
    img[s_a] = np.clip(dark_base, 0.0, 1.0)

    # 条带 B (90..150): 全范围中性灰梯度 0.0 -> 1.0
    s_b = slice(90, 150)
    img[s_b] = np.broadcast_to(x[None, :, None], (60, w, 3))

    # 条带 C (150..220): 高光暖肤色梯度（涵盖阴影至过曝）
    s_c = slice(150, 220)
    skin_base = np.stack(
        [
            np.clip(0.10 + x * 0.95, 0, 1),
            np.clip(0.09 + x * 0.74, 0, 1),
            np.clip(0.08 + x * 0.56, 0, 1),
        ],
        axis=-1,
    )
    img[s_c] = np.broadcast_to(skin_base[None, :, :], (70, w, 3))

    # 条带 D (220..360): 全色相 × 亮度 2D 高饱和渐变场 (y 从亮高饱和到暗高饱和)
    s_d = slice(220, 360)
    hue = x[None, :] * 6.0  # 0..6
    val = np.linspace(0.95, 0.25, 140)[:, None]
    sat = np.linspace(0.95, 0.45, 140)[:, None]
    c_val = val * sat
    x_val = c_val * (1.0 - np.abs((hue % 2.0) - 1.0))
    m_val = val - c_val
    hi = np.floor(hue).astype(np.int64) % 6
    r = np.zeros_like(hue)
    g = np.zeros_like(hue)
    b = np.zeros_like(hue)
    for k, (rk, gk, bk) in enumerate(
        [
            (c_val, x_val, np.zeros_like(hue)),
            (x_val, c_val, np.zeros_like(hue)),
            (np.zeros_like(hue), c_val, x_val),
            (np.zeros_like(hue), x_val, c_val),
            (x_val, np.zeros_like(hue), c_val),
            (c_val, np.zeros_like(hue), x_val),
        ]
    ):
        mask = hi == k
        r = np.where(mask, rk, r)
        g = np.where(mask, gk, g)
        b = np.where(mask, bk, b)
    img[s_d] = np.clip(np.stack([r + m_val, g + m_val, b + m_val], axis=-1), 0.0, 1.0)

    zones = {"shadow": s_a, "gray": s_b, "skin": s_c, "chroma": s_d}
    return img, zones


def generate_visual_reports(
    parsed: ParsedCube,
    report: Dict[str, Any],
    profile: CameraProfile,
    curve_cache: Dict[str, np.ndarray],
    det_norm: np.ndarray,
    sim_artifacts: Dict[str, Any],
    ramp_diff: np.ndarray,
    out_dir: Path,
    stem: str,
) -> Tuple[Path, Path]:
    """生成两张高分辨率专业中文诊断图：6 宫格科学仪表盘 PNG 与 合成压力测试热力图 PNG。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    diag_path = out_dir / f"{stem}_diagnostics.png"
    stress_path = out_dir / f"{stem}_stress_test.png"

    grid = parsed.safe_grid_3d
    t = curve_cache["t"]
    gray_rgb = curve_cache["gray_rgb"]
    gray_y = curve_cache["gray_y"]
    gray_slope = curve_cache["gray_slope"]

    # ==================== 图 1: 6 宫格科学质检仪表盘 ====================
    fig = plt.figure(figsize=(16, 10.5), dpi=180, facecolor="#111318")
    gs = fig.add_gridspec(2, 3, hspace=0.33, wspace=0.25, left=0.055, right=0.97, top=0.90, bottom=0.07)

    status = report["summary"]["status"]
    score = report["summary"]["score"]
    status_color = "#22c55e" if status == "PASS" else ("#f59e0b" if status == "WARN" else "#ef4444")
    fig.suptitle(
        f"LUT 专业质检科学诊断面板 — {Path(parsed.path).name}  |  [{status}] 得分: {score}/100  |  目标曲线: {profile.key.upper()}",
        color="#f8fafc",
        fontsize=14,
        fontweight="bold",
        y=0.96,
    )

    def _style_ax(ax: plt.Axes, title: str, xlabel: str, ylabel: str) -> None:
        ax.set_facecolor("#181c24")
        ax.set_title(title, color="#e2e8f0", fontsize=11, fontweight="bold", pad=8)
        ax.set_xlabel(xlabel, color="#94a3b8", fontsize=9)
        ax.set_ylabel(ylabel, color="#94a3b8", fontsize=9)
        ax.tick_params(colors="#94a3b8", labelsize=8)
        ax.grid(True, color="#2d3544", linestyle="--", linewidth=0.6, alpha=0.7)
        for spine in ax.spines.values():
            spine.set_color("#334155")

    # (1) 左上: 灰阶传递曲线 + 锚点 + 越界标记
    ax1 = fig.add_subplot(gs[0, 0])
    _style_ax(ax1, "1. 灰阶 RGB / 亮度曲线与安全边界", "输入归一化值 (0.0 - 1.0)", "输出值")
    ax1.plot(t, gray_rgb[:, 0], color="#ef4444", lw=1.3, alpha=0.85, label="Red")
    ax1.plot(t, gray_rgb[:, 1], color="#22c55e", lw=1.3, alpha=0.85, label="Green")
    ax1.plot(t, gray_rgb[:, 2], color="#3b82f6", lw=1.3, alpha=0.85, label="Blue")
    ax1.plot(t, gray_y, color="#f8fafc", lw=2.0, linestyle="-", label="Luma Y")
    ax1.axhline(0.0, color="#38bdf8", lw=1.0, ls=":", alpha=0.9)
    ax1.axhline(1.0, color="#fbbf24", lw=1.0, ls=":", alpha=0.9)
    ax1.axvline(profile.black_point, color="#a855f7", lw=1.0, ls="--", alpha=0.8, label=f"黑位 {profile.black_point:.3f}")
    ax1.axvline(profile.mid_gray, color="#94a3b8", lw=0.9, ls="--", alpha=0.6, label=f"18%灰 {profile.mid_gray:.3f}")
    over_m = np.any(gray_rgb > 1.0 + 1e-4, axis=-1)
    under_m = np.any(gray_rgb < -1e-4, axis=-1)
    if np.any(over_m):
        ax1.scatter(t[over_m], np.max(gray_rgb[over_m], axis=-1), color="#fbbf24", s=14, zorder=5, label="超白 >1.0")
    if np.any(under_m):
        ax1.scatter(t[under_m], np.min(gray_rgb[under_m], axis=-1), color="#38bdf8", s=14, zorder=5, label="负值 <0.0")
    ax1.legend(loc="upper left", fontsize=7.5, facecolor="#0f172a", edgecolor="#334155", labelcolor="#e2e8f0", ncol=2)

    # (2) 中上: 一阶导数局部斜率曲线 (斑马纹/反转捕捉)
    ax2 = fig.add_subplot(gs[0, 1])
    _style_ax(ax2, "2. 灰阶一阶增益斜率 (dY/dx) 与反转警戒区", "输入归一化值", "局部斜率 dY/dx")
    t_mid = 0.5 * (t[:-1] + t[1:])
    ax2.plot(t_mid, gray_slope, color="#38bdf8", lw=1.6, label="灰阶亮度斜率")
    ax2.axhline(0.0, color="#ef4444", lw=1.2, ls="--", label="单调反转零线 (<0)")
    ax2.axhspan(min(-1.0, float(np.min(gray_slope)) - 0.2), 0.0, color="#ef4444", alpha=0.14)
    ax2.axvline(profile.black_point, color="#a855f7", lw=1.0, ls="--", alpha=0.7)
    ax2.set_ylim(min(-0.6, float(np.min(gray_slope)) - 0.3), max(4.5, min(18.0, float(np.max(gray_slope)) + 0.8)))
    ax2.legend(loc="upper right", fontsize=8, facecolor="#0f172a", edgecolor="#334155", labelcolor="#e2e8f0")

    # (3) 右上: 极暗部放大镜 [0.0, 0.22] + 近轴色度爆炸曲线
    ax3 = fig.add_subplot(gs[0, 2])
    _style_ax(ax3, "3. 极暗部放大镜 [0.0-0.22] 与近轴色度爆炸", "极暗部输入值", "输出通道 / 色差")
    t_shad = sim_artifacts["t_shad"]
    out_shad = sim_artifacts["out_shad"]
    ax3.plot(t_shad, out_shad[:, 0], color="#ef4444", lw=1.2, alpha=0.85, label="灰轴 R")
    ax3.plot(t_shad, out_shad[:, 1], color="#22c55e", lw=1.2, alpha=0.85, label="灰轴 G")
    ax3.plot(t_shad, out_shad[:, 2], color="#3b82f6", lw=1.2, alpha=0.85, label="灰轴 B")
    ax3.plot(
        sim_artifacts["band_luma"],
        sim_artifacts["band_max_chroma"],
        color="#f97316",
        lw=2.2,
        marker="o",
        markersize=3.5,
        label="近灰邻域最大色度差",
    )
    ax3.axvline(profile.black_point, color="#a855f7", lw=1.2, ls="--", label=f"黑电平 {profile.black_point:.3f}")
    ax3.axhline(0.0, color="#38bdf8", lw=0.9, ls=":")
    ax3.set_xlim(0.0, 0.22)
    ax3.legend(loc="upper left", fontsize=7.5, facecolor="#0f172a", edgecolor="#334155", labelcolor="#e2e8f0")

    # (4) 左下: 黑电平传感器底噪仿真对比卡
    ax4 = fig.add_subplot(gs[1, 0])
    _style_ax(ax4, "4. 黑电平传感器底噪放大仿真 (64x64 对比)", "", "")
    ref_p = sim_artifacts["ref_patch"]
    lut_p = sim_artifacts["lut_patch"]
    # 为了让暗部视觉清晰同时保持相对比例，对比块做轻微统一的显示提亮 gamma 0.72
    combined_patch = np.concatenate([ref_p, np.ones((ref_p.shape[0], 3, 3)) * 0.4, lut_p], axis=1)
    disp_patch = np.power(np.clip(combined_patch, 0.0, 1.0), 0.72)
    ax4.imshow(disp_patch, aspect="auto")
    ax4.set_xticks([32, 99])
    ax4.set_xticklabels(["标准平滑曲线", "本 LUT 机内效果"], color="#e2e8f0", fontsize=9.5, fontweight="bold")
    ax4.set_yticks([])
    ax4.text(
        0.5,
        0.06,
        f"相对底噪放大: 亮度 {sim_artifacts['luma_amp']:.2f}x  |  色度 {sim_artifacts['chroma_amp']:.2f}x",
        transform=ax4.transAxes,
        ha="center",
        va="bottom",
        color="#fde047" if max(sim_artifacts["luma_amp"], sim_artifacts["chroma_amp"]) > 2.2 else "#86efac",
        fontsize=9,
        fontweight="bold",
        bbox=dict(boxstyle="round,pad=0.3", facecolor="#0f172a", edgecolor="#334155", alpha=0.88),
    )

    # (5) 中下: 3D 晶格缺陷分布投影 (亮度 vs 饱和度)
    ax5 = fig.add_subplot(gs[1, 1])
    _style_ax(ax5, "5. 3D 晶格缺陷体素分布投影 (亮度 vs 输入饱和度)", "晶格平均输入亮度", "输入通道饱和差 (max-min)")
    n = grid.shape[0]
    axes_g = np.linspace(0.0, 1.0, n)
    rr, gg, bb = np.meshgrid(axes_g, axes_g, axes_g, indexing="ij")
    mean_g = (rr + gg + bb) / 3.0
    sat_g = np.maximum(np.maximum(rr, gg), bb) - np.minimum(np.minimum(rr, gg), bb)
    raw_eval = parsed.raw_grid_3d if parsed.raw_grid_3d is not None else grid

    over_vox = np.any(raw_eval > 1.0 + 1e-5, axis=-1)
    under_vox = np.any(raw_eval < -1e-5, axis=-1)
    fold_vox = np.zeros_like(over_vox, dtype=bool)
    fold_vox[:-1, :-1, :-1] = det_norm < -0.02

    # 背景采样散点
    ax5.scatter(mean_g[::2, ::2, ::2].ravel(), sat_g[::2, ::2, ::2].ravel(), color="#334155", s=4, alpha=0.35, label="正常晶格采样")
    if np.any(over_vox):
        ax5.scatter(mean_g[over_vox], sat_g[over_vox], color="#fbbf24", s=16, alpha=0.85, label=f"超白 >1.0 ({int(np.sum(over_vox))}点)")
    if np.any(under_vox):
        ax5.scatter(mean_g[under_vox], sat_g[under_vox], color="#38bdf8", s=16, alpha=0.85, label=f"负值 <0.0 ({int(np.sum(under_vox))}点)")
    if np.any(fold_vox):
        ax5.scatter(mean_g[fold_vox], sat_g[fold_vox], color="#ec4899", s=20, alpha=0.9, marker="x", label=f"雅可比折叠 ({int(np.sum(fold_vox))}格)")
    ax5.axvline(profile.black_point, color="#a855f7", lw=1.0, ls="--", alpha=0.7)
    ax5.legend(loc="upper right", fontsize=7.5, facecolor="#0f172a", edgecolor="#334155", labelcolor="#e2e8f0")

    # (6) 右下: 四面体 vs 三线性分歧 & 综合摘要面板
    ax6 = fig.add_subplot(gs[1, 2])
    _style_ax(ax6, "6. 插值分歧曲线 (四面体 vs 机内三线性) 与摘要", "测试斜坡输入值", "ΔRGB 欧氏差")
    t_256 = np.linspace(0.0, 1.0, len(ramp_diff))
    ax6.plot(t_256, ramp_diff, color="#c084fc", lw=1.8, label="四面体 vs 三线性 ΔRGB")
    ax6.axhline(0.045, color="#f59e0b", lw=1.0, ls="--", label="可见条带风险线 (0.045)")
    interp_m = report["metrics"]["interpolation_divergence"]
    hw_m = report["metrics"]["hardware_and_range"]
    shad_m = report["metrics"]["extreme_shadows"]
    mono_m = report["metrics"]["monotonicity_and_zebra"]
    summary_text = (
        f"综合评级: {status} ({score}/100)\n"
        f"尺寸: {hw_m['lut_3d_size'] or '1D'}  |  值域: [{hw_m['min_value']:.3f}, {hw_m['max_value']:.3f}]\n"
        f"反转事件: {len(mono_m['reversal_events'])}  |  振荡波纹: {len(mono_m['oscillation_events'])}\n"
        f"暗部斜率: {shad_m['max_shadow_luma_slope']:.2f}  |  近轴色度增益: {shad_m['max_shadow_chroma_gain']:.2f}x\n"
        f"插值最大分歧: {interp_m['max_divergence_delta_rgb']:.4f}"
    )
    ax6.text(
        0.96,
        0.95,
        summary_text,
        transform=ax6.transAxes,
        va="top",
        ha="right",
        color="#f8fafc",
        fontsize=8.0,
        bbox=dict(boxstyle="round,pad=0.4", facecolor="#0f172a", edgecolor=status_color, linewidth=1.6, alpha=0.92),
    )
    ax6.set_ylim(0.0, max(0.06, float(np.max(ramp_diff)) * 1.65))
    ax6.legend(loc="center right", fontsize=7.5, facecolor="#0f172a", edgecolor="#334155", labelcolor="#e2e8f0")

    fig.savefig(diag_path, dpi=180, facecolor=fig.get_facecolor())
    plt.close(fig)

    # ==================== 图 2: 合成全动态压力测试与伪影热力标记图 ====================
    test_img, zones = _build_stress_test_image(profile)
    raw_out = eval_trilinear(grid, test_img)
    hw_wrap_sim = np.mod(raw_out, 1.0)
    has_oob = np.any((raw_out < -1e-4) | (raw_out > 1.0 + 1e-4), axis=-1)

    # 检测图像行方向上的局部伪影像素（排除第 1 条带上半部人工注入随机热噪的 55 行，避免随机噪声误触发水平斑马纹标签）
    y_img = luma_709(np.clip(raw_out, 0.0, 1.0))
    dy_x = np.diff(y_img, axis=1, prepend=y_img[:, :1])
    rev_pix = dy_x < -0.0025
    osc_pix = np.zeros_like(rev_pix)
    osc_pix[:, 1:-1] = (dy_x[:, 1:-1] * dy_x[:, 2:] < 0) & (np.abs(dy_x[:, 1:-1]) > 0.003) & (np.abs(dy_x[:, 2:]) > 0.003)
    rev_pix[:55, :] = False
    osc_pix[:55, :] = False

    # 暗部彩噪像素：原输入在暗部区间且极低饱和度，输出却出现明显色差
    in_is_dark_neutral = (np.mean(test_img, axis=-1) <= profile.shadow_zone[1] + 0.02) & (chroma_spread(test_img) < 0.03)
    shadow_chroma_pix = in_is_dark_neutral & (chroma_spread(hw_wrap_sim) > 0.075)

    # 构建热力叠加底图 (先转灰度背景，再用高对比颜色标出各类伪影)
    gray_bg = np.repeat(np.clip(y_img, 0.0, 1.0)[..., None] * 0.45, 3, axis=-1)
    overlay = gray_bg.copy()
    # 青色: <0.0 负值下溢风险
    under_pix = np.any(raw_out < -1e-4, axis=-1)
    overlay[under_pix] = np.array([0.05, 0.88, 0.98])
    # 亮黄: >1.0 超白回绕斑马纹风险
    over_pix = np.any(raw_out > 1.0 + 1e-4, axis=-1)
    overlay[over_pix] = np.array([1.0, 0.86, 0.10])
    # 洋红: 单调性反转 / 高频锯齿条纹
    zebra_pix = rev_pix | osc_pix
    overlay[zebra_pix] = np.array([0.96, 0.22, 0.72])
    # 橙红: 极暗部彩噪爆炸像素
    overlay[shadow_chroma_pix] = np.array([1.0, 0.34, 0.12])

    fig2, (ax_l, ax_r) = plt.subplots(1, 2, figsize=(15.5, 7.6), dpi=180, facecolor="#111318")
    fig2.subplots_adjust(left=0.115, right=0.98, top=0.85, bottom=0.10, wspace=0.07)
    fig2.suptitle(
        f"合成全动态压力测试卡与上机伪影定位 — {Path(parsed.path).name}",
        color="#f8fafc",
        fontsize=13.5,
        fontweight="bold",
        y=0.95,
    )

    ax_l.imshow(np.clip(hw_wrap_sim, 0.0, 1.0), aspect="auto")
    ax_l.set_title(
        "左图：模拟 Sony 机内三线性插值 + 定点溢出回绕效果"
        + (" (检测到越界回绕!)" if np.any(has_oob) else " (值域未越界)"),
        color="#fde047" if np.any(has_oob) else "#e2e8f0",
        fontsize=10.5,
        fontweight="bold",
        pad=8,
    )
    ax_l.set_xticks([])
    ax_l.set_yticks([45, 120, 185, 290])
    ax_l.set_yticklabels(["S-Log3 暗部+底噪", "全动态中性灰", "暖肤色曝光", "全色相高饱和"], color="#cbd5e1", fontsize=9)

    ax_r.imshow(np.clip(overlay, 0.0, 1.0), aspect="auto")
    ax_r.set_title("右图：自动伪影热力定位（高光条纹/越界/暗部彩噪）", color="#e2e8f0", fontsize=10.5, fontweight="bold", pad=8)
    ax_r.set_xticks([])
    ax_r.set_yticks([])

    # 底部图例说明
    legend_handles = [
        plt.Line2D([0], [0], marker="s", color="w", label="亮黄: >1.0 超白斑马纹回绕风险", markerfacecolor="#ffdb1a", markersize=10, linestyle="None"),
        plt.Line2D([0], [0], marker="s", color="w", label="洋红: 单调反转 / 锯齿斑马纹", markerfacecolor="#f538b8", markersize=10, linestyle="None"),
        plt.Line2D([0], [0], marker="s", color="w", label="青色: <0.0 负值暗部闪点", markerfacecolor="#0de0fa", markersize=10, linestyle="None"),
        plt.Line2D([0], [0], marker="s", color="w", label="橙红: 极暗部色度爆炸 / 彩噪", markerfacecolor="#ff571f", markersize=10, linestyle="None"),
    ]
    fig2.legend(
        handles=legend_handles,
        loc="lower center",
        ncol=4,
        fontsize=9,
        facecolor="#1e293b",
        edgecolor="#475569",
        labelcolor="#f8fafc",
        framealpha=0.95,
    )

    fig2.savefig(stress_path, dpi=180, facecolor=fig2.get_facecolor())
    plt.close(fig2)
    return diag_path, stress_path


def render_markdown_report(report: Dict[str, Any], diag_png: Optional[Path], stress_png: Optional[Path]) -> str:
    """生成结构清晰、中文专业详尽的 Markdown 诊断报告。"""
    s = report["summary"]
    hw = report["metrics"]["hardware_and_range"]
    mono = report["metrics"]["monotonicity_and_zebra"]
    jac = report["metrics"]["jacobian_folding"]
    shad = report["metrics"]["extreme_shadows"]
    interp = report["metrics"]["interpolation_divergence"]
    disc = report["metrics"]["discontinuities_and_clipping"]
    sim = shad["monte_carlo_noise_sim"]

    lines = [
        f"# LUT 专业科学质检报告：`{Path(report['file']).name}`",
        "",
        f"- **综合评级**：`{s['status']}`（健康得分：**{s['score']} / 100**）",
        f"- **目标机内环境**：`{report['target']}` | **Log 曲线预设**：`{report['profile_name']}`",
        f"- **LUT 规格**：`3D={hw['lut_3d_size']}`，`1D={hw['lut_1d_size']}`，值域 `[{hw['min_value']}, {hw['max_value']}]`，TITLE=`{report['title'] or '(空)'}`",
        "",
        "---",
        "",
        "## 一、核心痛点诊断发现（按严重程度排序）",
        "",
    ]

    if not report["findings"]:
        lines.append("> 未检出影响 Sony 机内监看或暗部画质的异常缺陷，曲线连续、平滑且严格处于 `[0.0, 1.0]` 安全范围。")
    else:
        for idx, f in enumerate(report["findings"], 1):
            badge = "🔴 严重" if f["severity"] == "CRITICAL" else ("🟠 警告" if f["severity"] == "WARNING" else "🔵 提示")
            lines.extend(
                [
                    f"### {idx}. {badge} {f['category']}",
                    f"- **上机直接表现**：{f['symptom_impact']}",
                    f"- **定量检测证据**：{f['evidence']}",
                    f"- **达芬奇 / DCTL 修复建议**：{f['dctl_advice']}",
                    "",
                ]
            )

    lines.extend(
        [
            "---",
            "",
            "## 二、关键数学质检指标一览",
            "",
            "| 检查维度 | 关键指标 | 实测值 | 安全参考基准 |",
            "| :--- | :--- | :--- | :--- |",
            f"| **值域安全** | 最小值 / 最大值 | `{hw['min_value']} ~ {hw['max_value']}` | 严格 `[0.0, 1.0]` |",
            f"| **越界比例** | `< 0.0` 比例 / `> 1.0` 比例 | `{hw['below_zero_pct']}%` / `{hw['above_one_pct']}%` | `0.0%` / `0.0%` |",
            f"| **斑马纹风险** | 单调反转斜坡数 / 振荡斜坡数 | `{len(mono['reversal_events'])}` / `{len(mono['oscillation_events'])}` | `0` / `0` |",
            f"| **三维折叠** | 雅可比 `det(J)<0` 体素比例 | `{jac['folded_pct']}%` (`min={jac['min_normalized_det_j']}`) | `0.0%` (`> 0`) |",
            f"| **极暗部斜率** | 暗部最大亮度斜率 `dY/dx` | `{shad['max_shadow_luma_slope']}` (at `{shad['max_slope_input_t']}`) | `< 6.5` |",
            f"| **暗部色度爆炸** | 近灰轴最大色度增益 | `{shad['max_shadow_chroma_gain']}x` (爆炸格 `{shad['chroma_explosion_voxels']}`) | `< 3.0x` (`0` 格) |",
            f"| **底噪仿真倍率** | 黑位底噪放大（亮度 / 色度） | `{sim['luma_noise_amp_vs_ref']}x` / `{sim['chroma_noise_amp_vs_ref']}x` | `< 2.2x` / `< 2.4x` |",
            f"| **插值分歧** | 四面体 vs 三线性最大 / P99 ΔRGB | `{interp['max_divergence_delta_rgb']}` / `{interp['p99_divergence_delta_rgb']}` | `< 0.045` / `< 0.015` |",
            f"| **连续性** | 晶格最大邻域跃迁 `ΔRGB` | `{disc['max_adjacent_delta_rgb']}` | `< 0.35` |",
            "",
        ]
    )

    if diag_png and stress_png:
        lines.extend(
            [
                "## 三、可视化诊断图表",
                f"- 科学仪表盘图表：`{diag_png}`",
                f"- 合成压力测试与伪影定位图：`{stress_png}`",
                "",
            ]
        )

    return "\n".join(lines)


def inspect_lut_file(
    lut_path: str | Path,
    profile_key: str = "slog3",
    target: str = "sony_cinealta",
    out_dir: Optional[str | Path] = None,
    generate_plots: bool = True,
) -> Dict[str, Any]:
    """执行完整的 LUT 科学质检流水线，返回结构化报告并按需输出图表与报告文件。"""
    if profile_key not in PROFILES:
        raise ValueError(f"未知 profile '{profile_key}'，可选: {list(PROFILES.keys())}")
    profile = PROFILES[profile_key]
    parsed = parse_cube_file(lut_path)
    grid = parsed.safe_grid_3d

    hw_metrics, hw_findings = check_hardware_and_range(parsed, target)
    mono_metrics, mono_findings, curve_cache = check_monotonicity_and_zebra(grid, profile)
    jac_metrics, jac_findings, det_norm = check_jacobian_folding(grid)
    shad_metrics, shad_findings, sim_artifacts = check_extreme_shadows(grid, profile)
    interp_metrics, interp_findings, ramp_diff = check_interpolation_divergence(grid)
    disc_metrics, disc_findings = check_discontinuities_and_clipping(grid)

    all_findings = (
        hw_findings + mono_findings + jac_findings + shad_findings + interp_findings + disc_findings
    )
    # 按严重程度排序
    sev_order = {"CRITICAL": 0, "WARNING": 1, "INFO": 2}
    all_findings.sort(key=lambda x: sev_order.get(x["severity"], 9))

    score, status = summarize_and_score(all_findings)

    report: Dict[str, Any] = {
        "file": parsed.path,
        "title": parsed.title,
        "target": target,
        "profile": profile.key,
        "profile_name": profile.name,
        "summary": {
            "status": status,
            "score": score,
            "critical_count": sum(1 for f in all_findings if f["severity"] == "CRITICAL"),
            "warning_count": sum(1 for f in all_findings if f["severity"] == "WARNING"),
            "info_count": sum(1 for f in all_findings if f["severity"] == "INFO"),
            "parse_warnings": parsed.parse_warnings,
        },
        "findings": all_findings,
        "metrics": {
            "hardware_and_range": hw_metrics,
            "monotonicity_and_zebra": mono_metrics,
            "jacobian_folding": jac_metrics,
            "extreme_shadows": shad_metrics,
            "interpolation_divergence": interp_metrics,
            "discontinuities_and_clipping": disc_metrics,
        },
        "artifacts": {},
    }

    if out_dir is not None:
        out_path = Path(out_dir)
        out_path.mkdir(parents=True, exist_ok=True)
        stem = Path(parsed.path).stem
        diag_png: Optional[Path] = None
        stress_png: Optional[Path] = None
        if generate_plots:
            diag_png, stress_png = generate_visual_reports(
                parsed, report, profile, curve_cache, det_norm, sim_artifacts, ramp_diff, out_path, stem
            )
            report["artifacts"]["diagnostics_png"] = str(diag_png.resolve())
            report["artifacts"]["stress_test_png"] = str(stress_png.resolve())

        json_file = out_path / f"{stem}_report.json"
        md_file = out_path / f"{stem}_report.md"
        md_text = render_markdown_report(report, diag_png, stress_png)
        json_file.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        md_file.write_text(md_text, encoding="utf-8")
        report["artifacts"]["json_report"] = str(json_file.resolve())
        report["artifacts"]["markdown_report"] = str(md_file.resolve())

    return report


def main() -> int:
    parser = argparse.ArgumentParser(
        description="lut-inspector: 专业 .cube 3D LUT 科学质检工具 (Sony VENICE/FX6/FX3 & DCTL 排查)"
    )
    parser.add_argument("lut_path", help="待检测的 .cube 文件路径")
    parser.add_argument(
        "--profile",
        default="slog3",
        choices=list(PROFILES.keys()),
        help="输入素材对应的 Log 曲线基准 (默认: slog3)",
    )
    parser.add_argument(
        "--target",
        default="sony_cinealta",
        choices=["sony_cinealta", "generic_camera", "post"],
        help="目标运行环境 (默认: sony_cinealta)",
    )
    parser.add_argument(
        "--out-dir",
        default=None,
        help="报告与高清 PNG 图表输出目录 (默认在 LUT 同级创建 lut_inspection_report 目录)",
    )
    parser.add_argument("--json-out", default=None, help="额外指定的 JSON 报告路径")
    parser.add_argument("--md-out", default=None, help="额外指定的 Markdown 报告路径")
    parser.add_argument("--no-plots", action="store_true", help="跳过生成 PNG 可视化图表")
    parser.add_argument(
        "--strict-exit",
        action="store_true",
        help="若评级为 FAIL 则返回退出码 2 (默认返回 0 以便自动化流程继续)",
    )
    args = parser.parse_args()

    out_dir = Path(args.out_dir) if args.out_dir else (Path(args.lut_path).resolve().parent / "lut_inspection_report")
    report = inspect_lut_file(
        lut_path=args.lut_path,
        profile_key=args.profile,
        target=args.target,
        out_dir=out_dir,
        generate_plots=not args.no_plots,
    )

    if args.json_out:
        Path(args.json_out).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.md_out:
        diag_p = Path(report["artifacts"]["diagnostics_png"]) if "diagnostics_png" in report["artifacts"] else None
        stress_p = Path(report["artifacts"]["stress_test_png"]) if "stress_test_png" in report["artifacts"] else None
        Path(args.md_out).write_text(render_markdown_report(report, diag_p, stress_p), encoding="utf-8")

    # 终端简洁清晰打印结论
    s = report["summary"]
    hw = report["metrics"]["hardware_and_range"]
    shad = report["metrics"]["extreme_shadows"]
    sim = shad["monte_carlo_noise_sim"]
    print("=" * 72)
    print(f"[lut-inspector] 检测完成: {Path(report['file']).name}")
    print(f"综合评级: {s['status']}  |  健康分: {s['score']}/100  |  严重:{s['critical_count']}  警告:{s['warning_count']}")
    print(
        f"尺寸: 3D={hw['lut_3d_size']} (1D={hw['lut_1d_size']})  |  "
        f"值域: [{hw['min_value']}, {hw['max_value']}]  |  "
        f"底噪放大: 亮度 {sim['luma_noise_amp_vs_ref']}x / 色度 {sim['chroma_noise_amp_vs_ref']}x"
    )
    if report["findings"]:
        print("-" * 72)
        for i, f in enumerate(report["findings"], 1):
            print(f" {i}. [{f['severity']}] {f['category']}")
            print(f"    -> {f['evidence']}")
    print("-" * 72)
    for k, v in report["artifacts"].items():
        print(f"输出 {k}: {v}")
    print("=" * 72)

    if args.strict_exit and s["status"] == "FAIL":
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
