"""PaddleOCR-VL-1.6 单图演示。

运行：python -m ppi_loop.vision.demo --image /path/to/image.jpg
或：  python -m ppi_loop.vision.demo --url https://example.com/image.jpg
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ..image_utils import download_one
from .pipeline import prepare_visual_assets


def _input_path(image: str | None, url: str | None) -> Path:
    # 把--image/--url参数统一成本地图片路径
    # 给了url就先下载；给本地路径则展开并校验文件存在
    # 两者都没给或路径无效时抛出对应异常
    if url:
        downloaded = download_one(url)
        if not downloaded:
            raise RuntimeError(f"图片下载失败: {url}")
        return Path(downloaded).resolve()
    if not image:
        raise ValueError("必须提供 --image 或 --url")
    path = Path(image).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"本地图片不存在: {path}")
    return path


def run_demo(local_path: Path) -> dict:
    # 对单张图片跑一遍完整视觉资产流程，并逐段打印结果
    # 依次展示版面块、Markdown、3×3分块OCR、合并文本、ROI和诊断
    # 返回prepare_visual_assets的原始dict，方便后续处理
    print(f"[vision demo] 原图: {local_path}")
    result = prepare_visual_assets(str(local_path))

    blocks = result.get("ocr_blocks", [])
    print(f"\n--- PaddleOCR-VL版面块：{len(blocks)}条 ---")
    for index, block in enumerate(blocks, 1):
        score = block.get("score")
        score_text = f" score={float(score):.3f}" if score is not None else ""
        print(
            f"[{index:03d}] label={block.get('label', 'text')}"
            f" box={block.get('box', [])}{score_text}\n{block.get('text', '')}"
        )

    print("\n--- PaddleOCR-VL完整Markdown ---")
    print(result.get("ocr_markdown", "") or "(空)")

    tile_results = result.get("ocr_tile_results", [])
    print(f"\n--- 3×3二次纯OCR：{len(tile_results)}块 ---")
    for tile in tile_results:
        status = "成功" if tile.get("success") else "失败"
        print(
            f"[{tile.get('tile_id')}] {status} box={tile.get('tile_box')}\n"
            f"{tile.get('text', '') or tile.get('error', '(空)')}"
        )

    print("\n--- 合并后OCR文本（VLM实际接收） ---")
    print(result.get("ocr_merged_text", "") or "(空)")

    print("\n--- ROI ---")
    for region in result.get("visual_regions", []):
        print(
            f"{region.get('region_id')}: box={region.get('box')} "
            f"type={region.get('region_type')} label={region.get('label')} "
            f"path={region.get('local_path')}"
        )

    print("\n--- 诊断 ---")
    print(json.dumps(result.get("vision_diagnostics", {}), ensure_ascii=False, indent=2))
    return result


def main() -> None:
    # 命令行入口：解析--image/--url参数，转成路径后跑run_demo
    # 两个参数互斥且必填一个，否则argparse直接报错
    parser = argparse.ArgumentParser(description="PaddleOCR-VL-1.6单图演示")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--image")
    group.add_argument("--url")
    args = parser.parse_args()
    run_demo(_input_path(args.image, args.url))


if __name__ == "__main__":
    main()
