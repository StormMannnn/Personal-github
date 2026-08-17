"""PaddleOCR-VL-1.6 本地解析与原图 ROI 编排。"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

from .. import config
from .ocr import (
    extract_ocr_text,
    find_largest_image_block,
    generate_overlapping_tiles,
    merge_ocr_text,
    run_paddleocr_vl,
    run_paddleocr_vl_ocr_only,
    select_vl_regions,
    translate_blocks_to_original,
)
from .runtime import get_vision_runtime, vision_slot


def _visual_cache_tag() -> str:
    # 根据当前视觉相关配置生成缓存标签，配置一变缓存即失效
    # 输入无参数，输出形如"版本号_sha256前10位"的短字符串
    # 用哈希摘要把一堆配置参数压缩成短串，方便拼进缓存文件名
    payload = json.dumps({
        "pipeline": config.VISION_PIPELINE_VERSION,
        "vl_model": config.VISION_PADDLEOCR_VL_MODEL,
        "layout_model": config.VISION_LAYOUT_MODEL,
        "max_rois": config.VISION_MAX_ROIS,
        "large_image_ratio": config.VISION_LARGE_IMAGE_RATIO,
        "tile_rows": config.VISION_TILE_ROWS,
        "tile_cols": config.VISION_TILE_COLS,
        "tile_overlap": config.VISION_TILE_OVERLAP_RATIO,
        "secondary_ocr": config.VISION_SECONDARY_OCR_ENABLED,
        "max_product_tile_rois": config.VISION_MAX_PRODUCT_TILE_ROIS,
        "merged_text_format": "plain_v1",
        "roi_short_side": config.VISION_ROI_MIN_SHORT_SIDE,
        "roi_max_long_side": config.VISION_ROI_MAX_LONG_SIDE,
        "roi_padding": config.VISION_ROI_PADDING_RATIO,
        "roi_strategy": "layout_relation_v2",
    }, sort_keys=True).encode("utf-8")
    return f"{config.VISION_PIPELINE_VERSION}_{hashlib.sha256(payload).hexdigest()[:10]}"


def _read_image(path: str) -> Any:
    # 用cv2读取图片并返回BGR格式的numpy数组
    # 先经np.fromfile读字节再imdecode解码，是为了兼容中文/空格路径
    # 解码失败说明文件损坏或格式不支持，直接抛异常
    import cv2
    import numpy as np

    image = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"无法解码图片: {path}")
    return image


def _write_jpeg(path: Path, image_bgr: Any, quality: int = 92) -> None:
    # 把BGR图像编码成JPEG写入指定路径，quality控制压缩质量
    # 先自动建好父目录，编码失败会抛异常而不是静默返回
    # 用encoded.tofile写字节，避免中文路径下cv2.imwrite写不进去
    import cv2

    path.parent.mkdir(parents=True, exist_ok=True)
    ok, encoded = cv2.imencode(".jpg", image_bgr, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise ValueError(f"JPEG编码失败: {path}")
    encoded.tofile(str(path))


def _crop_region(source: Any, box: tuple[int, int, int, int]) -> Any:
    # 按box从原图裁剪ROI，并做向外扩边与几何缩放
    # 扩边量由VISION_ROI_PADDING_RATIO控制，最少扩8像素且夹在图像边界内
    # 短边过小或长边过大时按配置比例缩放，用LANCZOS4插值保证清晰度
    """从原图裁剪并做几何放大；不生成或推断新的图像细节。"""
    import cv2

    height, width = source.shape[:2]
    x1, y1, x2, y2 = box
    pad_x = max(8, round((x2 - x1) * config.VISION_ROI_PADDING_RATIO))
    pad_y = max(8, round((y2 - y1) * config.VISION_ROI_PADDING_RATIO))
    x1, y1 = max(0, x1 - pad_x), max(0, y1 - pad_y)
    x2, y2 = min(width, x2 + pad_x), min(height, y2 + pad_y)
    crop = source[y1:y2, x1:x2]
    if crop.size == 0:
        raise ValueError(f"ROI为空: {box}")
    short_side = min(crop.shape[:2])
    long_side = max(crop.shape[:2])
    if short_side < config.VISION_ROI_MIN_SHORT_SIDE or long_side > config.VISION_ROI_MAX_LONG_SIDE:
        scale = min(
            config.VISION_ROI_MIN_SHORT_SIDE / max(short_side, 1),
            config.VISION_ROI_MAX_LONG_SIDE / max(long_side, 1),
        )
        if long_side <= config.VISION_ROI_MAX_LONG_SIDE:
            scale = max(1.0, scale)
        crop = cv2.resize(
            crop,
            (round(crop.shape[1] * scale), round(crop.shape[0] * scale)),
            interpolation=cv2.INTER_LANCZOS4,
        )
    return crop


def _crop_exact(source: Any, box: tuple[int, int, int, int]) -> Any:
    # 按原图坐标精确裁剪3×3分块，不做任何扩边或缩放
    # 坐标先夹到图像范围内防止越界，裁剪为空时抛异常提示分块无效
    # 供二次OCR使用，保证输入与tile_box坐标严格对应
    """按原图坐标精确裁剪3×3分块，不扩边、不放大。"""
    height, width = source.shape[:2]
    x1, y1, x2, y2 = box
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(width, x2), min(height, y2)
    crop = source[y1:y2, x1:x2]
    if crop.size == 0:
        raise ValueError(f"分块为空: {box}")
    return crop


def prepare_visual_assets(local_path: str) -> dict:
    # n1节点调用的视觉资产入口：产出OCR文本、分块结果和ROI裁剪图
    # 先尝试读磁盘缓存，命中就直接返回；未命中才跑PaddleOCR-VL
    # 大图块触发3×3二次OCR，否则按普通ROI裁剪；所有降级错误收进vision_diagnostics
    """解析原图，必要时对大image块做3×3二次纯OCR。"""
    started = time.time()
    result = {
        "ocr_blocks": [],
        "ocr_markdown": "",
        "ocr_merged_text": "",
        "ocr_tile_results": [],
        "visual_regions": [],
        "vision_diagnostics": {
            "enabled": config.VISION_ENABLED,
            "backend": "paddleocr_vl",
            "errors": [],
        },
    }
    if not config.VISION_ENABLED:
        result["vision_diagnostics"]["duration_seconds"] = 0.0
        return result

    # ==== 缓存复用：命中则跳过 PaddleOCR-VL ====
    base_name = Path(local_path).stem.removesuffix("_original")
    cache_tag = _visual_cache_tag()
    cache_path = Path(config.IMG_CACHE_OCR_DIR) / f"{base_name}_ocr_{cache_tag}.json"
    if cache_path.is_file():
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            cached_regions = cached.get("visual_regions") or []
            if all(
                (not vr.get("local_path")) or Path(vr["local_path"]).is_file()
                for vr in cached_regions
            ):
                result["ocr_blocks"] = cached.get("blocks", [])
                result["ocr_markdown"] = cached.get("markdown", "")
                result["ocr_merged_text"] = cached.get("merged_text", "")
                result["ocr_tile_results"] = cached.get("tile_results", [])
                result["visual_regions"] = cached_regions
                diag = cached.get("diagnostics", {})
                diag["cache_hit"] = True
                result["vision_diagnostics"] = diag
                result["vision_diagnostics"]["duration_seconds"] = round(time.time() - started, 3)
                print(
                    f"[vision] 命中OCR缓存 blocks={len(result['ocr_blocks'])} "
                    f"roi={len(cached_regions)} → 跳过PaddleOCR-VL"
                )
                return result
        except Exception as exc:
            print(f"[vision] OCR缓存读取失败，重新解析: {exc}")

    original = None
    try:
        with vision_slot():
            original = _read_image(local_path)
            height, width = original.shape[:2]
            result["vision_diagnostics"].update({
                "original_width": width,
                "original_height": height,
            })
            runtime = get_vision_runtime()
            try:
                blocks, markdown = run_paddleocr_vl(runtime, local_path)
            except Exception as exc:
                blocks, markdown = [], ""
                result["vision_diagnostics"]["errors"].append(
                    f"PaddleOCR-VL解析失败: {exc}"
                )

            result["ocr_blocks"] = blocks
            result["ocr_markdown"] = markdown
            result["vision_diagnostics"].update(runtime.diagnostics())
            result["vision_diagnostics"].update({
                "ocr_block_count": len(blocks),
                "markdown_chars": len(markdown),
            })
            roi_dir = Path(config.IMG_CACHE_VL_ROI_DIR)
            roi_dir.mkdir(parents=True, exist_ok=True)
            visual_regions = []

            largest = find_largest_image_block(blocks, width, height)
            large_ratio = float(largest["area_ratio"]) if largest else 0.0
            secondary_triggered = bool(
                config.VISION_SECONDARY_OCR_ENABLED
                and largest
                and large_ratio > config.VISION_LARGE_IMAGE_RATIO
            )
            result["vision_diagnostics"].update({
                "large_image_detected": largest is not None,
                "large_image_box": list(largest["box"]) if largest else [],
                "large_image_area_ratio": round(large_ratio, 6),
                "secondary_ocr_triggered": secondary_triggered,
            })

            tile_results: list[dict] = []
            tile_ocr_started = time.time()
            if secondary_triggered and largest:
                tiles = generate_overlapping_tiles(
                    tuple(largest["box"]), width, height,
                    config.VISION_TILE_ROWS, config.VISION_TILE_COLS,
                    config.VISION_TILE_OVERLAP_RATIO,
                )[:config.VISION_MAX_PRODUCT_TILE_ROIS]
                for tile in tiles:
                    tile_id = tile["tile_id"]
                    tile_box = tuple(tile["box"])
                    tile_result = {
                        "tile_id": tile_id,
                        "tile_box": list(tile_box),
                        "text": "",
                        "source": "secondary_tile_ocr",
                        "success": False,
                        "blocks": [],
                    }
                    try:
                        crop = _crop_exact(original, tile_box)
                    except Exception as exc:
                        tile_result["error"] = str(exc)
                        result["vision_diagnostics"]["errors"].append(
                            f"{tile_id}裁剪失败: {exc}"
                        )
                        tile_results.append(tile_result)
                        continue

                    roi_path = roi_dir / f"{base_name}_{tile_id}_{cache_tag}.jpg"
                    local_path_value = ""
                    try:
                        _write_jpeg(roi_path, crop)
                        local_path_value = str(roi_path)
                    except Exception as exc:
                        result["vision_diagnostics"]["errors"].append(
                            f"{tile_id} ROI写入失败: {exc}"
                        )

                    try:
                        tile_blocks, tile_markdown = run_paddleocr_vl_ocr_only(runtime, crop)
                        translated = translate_blocks_to_original(tile_blocks, tile_box, tile_id)
                        tile_result.update({
                            "text": extract_ocr_text(translated, tile_markdown),
                            "success": True,
                            "blocks": translated,
                        })
                    except Exception as exc:
                        tile_result["error"] = str(exc)
                        result["vision_diagnostics"]["errors"].append(
                            f"{tile_id}二次OCR失败: {exc}"
                        )

                    tile_results.append(tile_result)
                    visual_regions.append({
                        "region_id": tile_id,
                        "box": list(tile_box),
                        "local_path": local_path_value,
                        "ocr_text": tile_result["text"],
                        "label": "image",
                        "region_type": "product_tile_candidate",
                        "contains_product_visual": True,
                        "source": "secondary_tile_ocr",
                    })
            else:
                regions = select_vl_regions(blocks, width, height)
                for idx, region in enumerate(regions[:config.VISION_MAX_ROIS]):
                    try:
                        crop = _crop_region(original, tuple(region["box"]))
                        roi_path = roi_dir / f"{base_name}_product_roi_{cache_tag}_{idx:02d}.jpg"
                        _write_jpeg(roi_path, crop)
                        visual_regions.append({
                            "region_id": f"roi_{idx}",
                            "box": list(region["box"]),
                            "local_path": str(roi_path),
                            "ocr_text": region.get("ocr_text", ""),
                            "label": region.get("label", "image"),
                            "region_type": "product_visual",
                            "contains_product_visual": True,
                            "source": "paddleocr_vl_1.6",
                        })
                    except Exception as exc:
                        result["vision_diagnostics"]["errors"].append(f"ROI {idx}失败: {exc}")

            result["ocr_tile_results"] = tile_results
            result["ocr_merged_text"] = merge_ocr_text(blocks, markdown, tile_results)
            result["visual_regions"] = visual_regions
            result["vision_diagnostics"].update({
                "tile_count": len(tile_results),
                "tile_success_count": sum(bool(item.get("success")) for item in tile_results),
                "tile_failure_count": sum(not item.get("success") for item in tile_results),
                "tile_ocr_duration_seconds": round(time.time() - tile_ocr_started, 3),
                "merged_text_chars": len(result["ocr_merged_text"]),
                "product_roi_count": len(visual_regions),
                "roi_count": len(visual_regions),
            })

            ocr_dir = Path(config.IMG_CACHE_OCR_DIR)
            ocr_dir.mkdir(parents=True, exist_ok=True)
            try:
                cache_path.write_text(json.dumps({
                    "blocks": blocks,
                    "markdown": markdown,
                    "merged_text": result["ocr_merged_text"],
                    "tile_results": tile_results,
                    "visual_regions": visual_regions,
                    "diagnostics": result["vision_diagnostics"],
                }, ensure_ascii=False, indent=2), encoding="utf-8")
                result["vision_diagnostics"]["ocr_cache_path"] = str(cache_path)
            except Exception as exc:
                result["vision_diagnostics"]["errors"].append(f"OCR缓存写入失败: {exc}")
    except Exception as exc:
        result["vision_diagnostics"]["errors"].append(f"视觉处理失败: {exc}")

    result["vision_diagnostics"]["duration_seconds"] = round(time.time() - started, 3)
    diag = result["vision_diagnostics"]
    print(
        "[vision] PaddleOCR-VL-1.6 "
        f"blocks={len(result['ocr_blocks'])} tiles={len(result['ocr_tile_results'])} "
        f"merged={len(result['ocr_merged_text'])} roi={len(result['visual_regions'])} "
        f"duration={diag['duration_seconds']:.2f}s "
        f"errors={len(diag.get('errors', []))}"
    )
    return result
