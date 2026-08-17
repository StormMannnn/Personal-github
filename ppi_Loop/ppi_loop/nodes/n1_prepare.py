"""n1_prepare — 下载原图 + PaddleOCR-VL视觉资产 + 多模态消息。

对应: 原 notebook `download_one` + `local_to_data_url` + `build_messages`。
"""

import asyncio
import json
import logging

from ..image_utils import download_one, local_to_data_url
from .. import config
from ..vision import prepare_visual_assets

logger = logging.getLogger(__name__)


def build_multimodal_message(image_url: str, data_url: str,
                              product_title: str,
                              word_package: list[str],
                              system_prompt: str,
                              ocr_merged_text: str = "",
                              task: str = "general",
                              sales_combinations: list[dict] | None = None) -> list[dict]:
    # 构建发给 VLM 的多模态消息:user 部分拼图片标识/商品标题/词袋文本与 base64 图片;
    # 遗留辅助函数,现已被 nodes.common.multimodal_messages 取代,仅保留兼容。
    """构建发给 VLM 的多模态消息列表。

    user content 包含:
    - 文本: 图片标识/商品标题(辅助)/标准商品列表
    - 图片: base64 data URL
    """
    image_key = image_url  # 原 URL 作为 JSON 输出的 key 标识符

    wp_text = "\n".join(word_package)

    user_text = (
        f"请提取以下图片中的信息。图片标识: {image_key}\n"
        f"图片所属商品标题(辅助参考): {product_title}\n"
        f"标准商品名称列表(最终输出名称须严格来自此列表):\n{wp_text}\n\n"
        "视觉输入说明：下方OCR文本来自同一张原图及其3×3重叠分块，"
        "已做确定性重复合并，但仍可能有错字。"
        "必须以原图判断版面归属，不得跨卡片、边框或区域拼接文字。\n"
    )
    if sales_combinations:
        user_text += (
            "\n已经Kimi复核并锁定的销售组合台账（价格/优惠券必须按此归属，"
            "不得自行删除、合并或改号）:\n"
            f"{json.dumps(sales_combinations, ensure_ascii=False)}\n"
        )
    if ocr_merged_text:
        user_text += f"\nPaddleOCR-VL合并OCR文本:\n{ocr_merged_text}"

    content = [
        {"type": "text", "text": user_text},
        {"type": "text", "text": "【原始全图】"},
        {
            "type": "image_url",
            "image_url": {"url": data_url},
        },
    ]

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": content},
    ]
    return messages


async def prepare_node(state: dict) -> dict:
    # n1 节点:线程池下载原图 → 跑 PaddleOCR-VL 生成版面/OCR/ROI 资产;
    # 只写 local_image_path 不持久化 data_url(保持 checkpoint 精简),
    # 下载失败直接返回 INFRA_ERROR + stop 终止本轮。
    """LangGraph节点：下载原图并生成PaddleOCR-VL解析和ROI。

    下载为阻塞 IO,用 run_in_executor 丢线程池执行。
    """
    image_url = state.get("image_url", "")
    product_title = state.get("product_title", "")
    word_package = state.get("word_package", [])

    # 下载 + 预处理（阻塞 IO → 线程池）
    loop = asyncio.get_event_loop()
    local_path = await loop.run_in_executor(
        None, download_one, image_url, config.IMG_CACHE_ORIGINAL_DIR, config.CACHE_MAX_DAYS
    )
    if local_path is None:
        return {
            "errors": [f"图片下载失败: {image_url}"],
            "data_url": "",
            "ocr_blocks": [],
            "ocr_markdown": "",
            "ocr_merged_text": "",
            "ocr_tile_results": [],
            "visual_regions": [],
            "vision_diagnostics": {"errors": ["图片下载失败"]},
            "terminal_status": "INFRA_ERROR",
            "next_action": "stop",
        }

    # 原图base64 + PaddleOCR-VL视觉资产（阻塞任务 → 线程池）
    visual_assets = await loop.run_in_executor(None, prepare_visual_assets, local_path)

    # 构建多模态消息：由 n2 使用；这里暂不暴露完整消息到 state，
    # n2 自行调用 build_multimodal_message（保持 state 简洁）
    return {
        # Base64 is reconstructed immediately before each model call and is never
        # checkpointed. This keeps the SQLite state compact and auditable.
        "local_image_path": str(local_path),
        "data_url": "",
        "ocr_blocks": visual_assets.get("ocr_blocks", []),
        "ocr_markdown": visual_assets.get("ocr_markdown", ""),
        "ocr_merged_text": visual_assets.get("ocr_merged_text", ""),
        "ocr_tile_results": visual_assets.get("ocr_tile_results", []),
        "visual_regions": visual_assets.get("visual_regions", []),
        "vision_diagnostics": visual_assets.get("vision_diagnostics", {}),
    }
