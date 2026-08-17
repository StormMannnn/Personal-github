"""PaddleOCR-VL-1.6输出归一化、二次分块OCR和商品候选ROI。"""

from __future__ import annotations

import unicodedata
from typing import Any

from .. import config

PRODUCT_VISUAL_LABELS = {"image", "figure"}


def _as_dict(value: Any) -> dict:
    # 把PaddleOCR-VL返回的任意对象统一转成dict，优先取"res"字段
    # 依次支持dict、可迭代对象以及带json()/to_dict()方法的对象
    # 都转不了就返回空dict，保证下游拿到的结构一致
    if isinstance(value, dict):
        return value.get("res", value)
    try:
        return dict(value)
    except Exception:
        pass
    for name in ("json", "to_dict"):
        converted = getattr(value, name, None)
        if callable(converted):
            converted = converted()
        if isinstance(converted, dict):
            return converted.get("res", converted)
    return {}


def _block_field(block: Any, name: str, default: Any = None) -> Any:
    # 统一从块对象里取字段：dict用get，普通对象用getattr
    # 目的是屏蔽PaddleOCR-VL返回dict还是对象两种形态的差异
    if isinstance(block, dict):
        return block.get(name, default)
    return getattr(block, name, default)


def _polygon_from_bbox(bbox: Any) -> list[list[float]]:
    # 把bbox转成四边形的顶点列表[[x,y],...]
    # 输入已是四点列表就直接透传转float；输入是x1,y1,x2,y2则补成顺时针四角
    # 输入不合法返回空列表，避免下游处理报错
    if not isinstance(bbox, (list, tuple)) or len(bbox) < 4:
        return []
    if isinstance(bbox[0], (list, tuple)):
        return [[float(p[0]), float(p[1])] for p in bbox]
    x1, y1, x2, y2 = map(float, bbox[:4])
    return [[x1, y1], [x2, y1], [x2, y2], [x1, y2]]


def _rect(block: dict) -> tuple[float, float, float, float]:
    # 从块的box顶点里算出外接矩形的x1,y1,x2,y2
    # box为空时返回全0，表示没有有效区域
    points = block.get("box") or []
    if not points:
        return 0.0, 0.0, 0.0, 0.0
    xs = [float(p[0]) for p in points]
    ys = [float(p[1]) for p in points]
    return min(xs), min(ys), max(xs), max(ys)


def normalize_paddleocr_vl_results(raw_results: Any) -> tuple[list[dict], str]:
    # 把PaddleOCR-VL原始输出归一化成统一的版面块列表和Markdown文本
    # 版面块取自parsing_res_list，词级文本取自spotting_res的rec_texts/rec_polys
    # 最后按(text,坐标)去重，同时保持模型给出的阅读顺序
    """转为现有下游可消费的版面块，并提取完整Markdown文本。"""
    results = raw_results if isinstance(raw_results, list) else [raw_results]
    blocks: list[dict] = []
    markdown_parts: list[str] = []

    for result in results:
        data = _as_dict(result)
        for block in data.get("parsing_res_list") or []:
            text = str(_block_field(block, "content", None)
                       or _block_field(block, "block_content", "") or "").strip()
            box = _polygon_from_bbox(_block_field(block, "bbox", None)
                                     or _block_field(block, "block_bbox", []))
            if not text:
                continue
            blocks.append({
                "text": text,
                "label": str(_block_field(block, "label", None)
                             or _block_field(block, "block_label", "text") or "text"),
                "box": box,
                "source": "paddleocr_vl_1.6",
            })

        spotting = _as_dict(data.get("spotting_res"))
        texts = spotting.get("rec_texts") or []
        scores = spotting.get("rec_scores") or []
        polys = spotting.get("rec_polys") or []
        for idx, text in enumerate(texts):
            text = str(text or "").strip()
            if not text or idx >= len(polys):
                continue
            blocks.append({
                "text": text,
                "label": "spotting",
                "box": _polygon_from_bbox(polys[idx]),
                "score": float(scores[idx]) if idx < len(scores) else None,
                "source": "paddleocr_vl_1.6",
            })

        markdown = getattr(result, "markdown", None)
        if callable(markdown):
            markdown = markdown()
        if isinstance(markdown, dict):
            markdown = markdown.get("markdown_texts") or markdown.get("text") or ""
        if isinstance(markdown, (list, tuple)):
            markdown = "\n\n".join(str(item) for item in markdown if item)
        if markdown:
            markdown_parts.append(str(markdown).strip())

    # 去掉完全相同的重复块，同时保持PaddleOCR-VL给出的阅读顺序。
    unique: list[dict] = []
    seen: set[tuple] = set()
    for block in blocks:
        key = (block.get("text"), tuple(round(v) for v in _rect(block)))
        if key not in seen:
            seen.add(key)
            unique.append(block)
    return unique, "\n\n".join(part for part in markdown_parts if part)


def run_paddleocr_vl(runtime: Any, local_path: str) -> tuple[list[dict], str]:
    # 用运行时已加载的Pipeline对本地图片做一次完整版面解析
    # 返回(版面块列表, Markdown文本)，供prepare_visual_assets消费
    pipeline = runtime.get_pipeline()
    raw_results = list(pipeline.predict(input=local_path))
    return normalize_paddleocr_vl_results(raw_results)


def run_paddleocr_vl_ocr_only(runtime: Any, image: Any) -> tuple[list[dict], str]:
    # 对单个分块图像只做纯文字识别，关闭版面检测
    # 关闭merge_layout_blocks并指定prompt_label=ocr，让输出更聚焦文本
    # 输入是numpy图像而非路径，方便直接喂裁剪后的分块
    """对单个分块关闭版面检测，仅执行PaddleOCR-VL文字识别。"""
    pipeline = runtime.get_pipeline()
    raw_results = list(pipeline.predict(
        input=image,
        use_layout_detection=False,
        prompt_label="ocr",
        merge_layout_blocks=False,
    ))
    return normalize_paddleocr_vl_results(raw_results)


def find_largest_image_block(blocks: list[dict], width: int, height: int) -> dict | None:
    # 在所有image/figure块中找面积最大的一块，返回其box和整图占比
    # 占比=块面积/整图面积，用于判断是否需要触发3×3二次OCR
    # 没有任何视觉块时返回None
    """返回面积最大的image/figure块及其整图占比。"""
    image_area = max(width * height, 1)
    largest: dict | None = None
    for block in blocks:
        if str(block.get("label", "")).lower() not in PRODUCT_VISUAL_LABELS:
            continue
        x1, y1, x2, y2 = _rect(block)
        x1, y1 = max(0.0, x1), max(0.0, y1)
        x2, y2 = min(float(width), x2), min(float(height), y2)
        if x2 <= x1 or y2 <= y1:
            continue
        area = (x2 - x1) * (y2 - y1)
        candidate = {
            "box": (round(x1), round(y1), round(x2), round(y2)),
            "area_ratio": area / image_area,
            "block": block,
        }
        if largest is None or candidate["area_ratio"] > largest["area_ratio"]:
            largest = candidate
    return largest


def generate_overlapping_tiles(
    box: tuple[int, int, int, int],
    width: int,
    height: int,
    rows: int,
    cols: int,
    overlap_ratio: float,
) -> list[dict]:
    # 把目标区域按rows×cols切成行优先的重叠网格，返回每个分块的坐标
    # 相邻格子向里扩展overlap_ratio/2形成重叠，边缘格子不越界
    # 行列数必须大于0、重叠比例必须在[0,1)，否则抛异常
    """将目标区域分成行优先的重叠网格，相邻格总重叠约为overlap_ratio。"""
    if rows <= 0 or cols <= 0:
        raise ValueError("分块行列数必须大于0")
    if not 0 <= overlap_ratio < 1:
        raise ValueError("分块重叠比例必须在[0, 1)内")

    x1, y1, x2, y2 = box
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(width, x2), min(height, y2)
    region_w, region_h = x2 - x1, y2 - y1
    if region_w <= 0 or region_h <= 0:
        return []

    core_w, core_h = region_w / cols, region_h / rows
    expand_x = core_w * overlap_ratio / 2
    expand_y = core_h * overlap_ratio / 2
    tiles: list[dict] = []
    for row in range(rows):
        for col in range(cols):
            core_x1 = x1 + col * core_w
            core_y1 = y1 + row * core_h
            core_x2 = x1 + (col + 1) * core_w
            core_y2 = y1 + (row + 1) * core_h
            tile_box = (
                max(x1, int(round(core_x1 - (expand_x if col > 0 else 0)))),
                max(y1, int(round(core_y1 - (expand_y if row > 0 else 0)))),
                min(x2, int(round(core_x2 + (expand_x if col < cols - 1 else 0)))),
                min(y2, int(round(core_y2 + (expand_y if row < rows - 1 else 0)))),
            )
            tiles.append({
                "tile_id": f"tile_r{row + 1}_c{col + 1}",
                "row": row + 1,
                "col": col + 1,
                "box": tile_box,
            })
    return tiles


def translate_blocks_to_original(
    blocks: list[dict], tile_box: tuple[int, int, int, int], tile_id: str
) -> list[dict]:
    # 把分块内OCR出的块的坐标平移回原图坐标
    # 每个块加上tile_box左上角的偏移，并打上来源和tile_id标记
    """将分块内坐标映射回原图坐标。"""
    offset_x, offset_y = tile_box[0], tile_box[1]
    translated = []
    for block in blocks:
        item = dict(block)
        item["box"] = [
            [float(point[0]) + offset_x, float(point[1]) + offset_y]
            for point in block.get("box", [])
        ]
        item["source"] = "secondary_tile_ocr"
        item["tile_id"] = tile_id
        translated.append(item)
    return translated


def extract_ocr_text(blocks: list[dict], markdown: str = "") -> str:
    # 从块列表里提取纯文本，优先使用结构化块文字
    # 块文字为空时才退回使用Markdown文本
    """优先使用结构化块文字；无块时才使用Markdown。"""
    texts = [str(block.get("text", "")).strip() for block in blocks]
    texts = [text for text in texts if text]
    return "\n".join(texts) if texts else str(markdown or "").strip()


def _normalized_text(text: str) -> str:
    # 文本规范化：先NFKC统一全半角，再合并连续空白为单个空格
    # 供去重时比较两段文本是否实质相同
    return " ".join(unicodedata.normalize("NFKC", text).split())


def merge_ocr_text(
    first_blocks: list[dict], first_markdown: str, tile_results: list[dict]
) -> str:
    # 把首次OCR文本和3×3分块OCR结果按顺序合并成一段纯文字
    # 逐行做规范化去重，相同文本只保留第一次出现的原始写法
    """按首次OCR→行优先分块OCR合并，只输出去重后的纯文字。"""
    entries: list[str] = []
    for block in first_blocks:
        text = str(block.get("text", "")).strip()
        if text:
            entries.append(text)
    if not entries and first_markdown.strip():
        entries.append(first_markdown.strip())
    for result in tile_results:
        if result.get("success") and str(result.get("text", "")).strip():
            entries.append(str(result["text"]).strip())

    ordered: list[str] = []
    seen: set[str] = set()
    for text in entries:
        for line in text.splitlines():
            original = line.strip()
            key = _normalized_text(original)
            if not key:
                continue
            if key not in seen:
                seen.add(key)
                ordered.append(original)
    return "\n".join(ordered)


def select_vl_regions(blocks: list[dict], width: int, height: int) -> list[dict]:
    # 未触发3×3二次OCR时，从版面块中挑出image/figure视觉块作为ROI
    # 只保留有有效面积的块，并限制在VISION_MAX_ROIS数量内
    """未触发3×3时只输出PaddleOCR-VL的image/figure视觉块。"""
    regions = []
    for block in blocks:
        if str(block.get("label", "")).lower() not in PRODUCT_VISUAL_LABELS:
            continue
        box = tuple(round(v) for v in _rect(block))
        if box[2] > box[0] and box[3] > box[1]:
            regions.append({
                "box": box,
                "ocr_text": str(block.get("text", "")),
                "label": str(block.get("label", "text")),
                "region_type": "product_visual",
                "contains_product_visual": True,
            })
    return regions[:config.VISION_MAX_ROIS]


def fallback_grid_regions(width: int, height: int) -> list[dict]:
    # 兜底方案：不依赖OCR结果，直接把整图分成2×2带重叠的四个区域
    # 每格带10%重叠避免边缘内容被切掉，OCR失败时也能产出ROI
    overlap_x, overlap_y = round(width * 0.10), round(height * 0.10)
    half_w, half_h = width // 2, height // 2
    boxes = [
        (0, 0, min(width, half_w + overlap_x), min(height, half_h + overlap_y)),
        (max(0, half_w - overlap_x), 0, width, min(height, half_h + overlap_y)),
        (0, max(0, half_h - overlap_y), min(width, half_w + overlap_x), height),
        (max(0, half_w - overlap_x), max(0, half_h - overlap_y), width, height),
    ]
    return [{"box": b, "ocr_text": "", "label": "fallback_grid"} for b in boxes]


def format_ocr_for_prompt(blocks: list[dict], limit: int = 100) -> str:
    # 把版面块格式化成人可读文本，供拼进VLM提示词
    # 每块输出序号、label、区域坐标、置信度和文本内容，limit限制条数
    lines = []
    for idx, block in enumerate(blocks[:limit], 1):
        x1, y1, x2, y2 = [round(v) for v in _rect(block)]
        score = block.get("score")
        score_text = f", 置信度={float(score):.3f}" if score is not None else ""
        lines.append(
            f"[{idx}] label={block.get('label', 'text')}, "
            f"区域={x1},{y1},{x2},{y2}{score_text}\n{block.get('text', '')}"
        )
    return "\n".join(lines)
