"""Runtime configuration for the independent PPI loop agent."""

from __future__ import annotations

import os
import platform
from pathlib import Path


def _env_bool(name: str, default: bool) -> bool:
    # 解析布尔环境变量:未设置时返回默认值,设置了则按 1/true/yes/on 判为 True,
    # 其它值一律算 False,兼容大小写和前后空格。
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


LOOP_VERSION = "ppi-loop-v10"

KIMI_MODEL = os.getenv("PPI_CHECKER_MODEL", "kimi-k2.6")
QWEN_MODEL = os.getenv("PPI_MAKER_MODEL", "qwen3.7-plus")
PRODUCTS_MODEL = QWEN_MODEL
PRICE_COUPON_MODEL = QWEN_MODEL
PRODUCT_MATCH_JUDGE_MODEL = KIMI_MODEL


def _required_key(name: str) -> str:
    # 读取必须存在的环境变量并去空格;缺失时直接抛 RuntimeError,
    # 避免空 key 一路传到请求阶段才失败、难以排查。
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"缺少必需环境变量 {name}")
    return value


def endpoint_for(model_name: str) -> dict:
    # 端点路由:Kimi 与 Qwen 当前都走阿里云百炼 DashScope 兼容模式,
    # key 从环境变量读(缺失即报错);不认识的名字抛 KeyError 快速暴露配置错误。
    if model_name == KIMI_MODEL:
        return {
            "base_url": os.getenv(
                "DASHSCOPE_BASE_URL",
                "https://dashscope.aliyuncs.com/compatible-mode/v1",
            ),
            "api_key": _required_key("DASHSCOPE_API_KEY"),
            "extra_body": {"enable_thinking": True},
        }
    if model_name == QWEN_MODEL:
        return {
            "base_url": os.getenv(
                "DASHSCOPE_BASE_URL",
                "https://dashscope.aliyuncs.com/compatible-mode/v1",
            ),
            "api_key": _required_key("DASHSCOPE_API_KEY"),
            "extra_body": {"enable_thinking": True},
        }
    raise KeyError(f"未配置模型端点: {model_name}")


def get_price_coupon_model() -> str:
    # 遗留兼容函数:返回价格/优惠券阶段用的模型名,
    # 让旧调用方无需感知常量改名或配置变化。
    return PRICE_COUPON_MODEL


MAX_TOKENS = int(os.getenv("PPI_MAX_TOKENS", "16384"))
TEMPERATURE = 0
TOP_P = 0.9
MAX_RETRY = int(os.getenv("PPI_MAX_RETRY", "3"))
CHECKER_SEMANTIC_MAX_RETRY = int(os.getenv("PPI_CHECKER_SEMANTIC_MAX_RETRY", "2"))
MODEL_CONTRACT_MAX_RETRY = int(os.getenv("PPI_MODEL_CONTRACT_MAX_RETRY", "2"))
BACKOFF_BASE = float(os.getenv("PPI_BACKOFF_BASE", "2"))
RETRYABLE_HINTS = (
    "503", "429", "50508", "too busy", "rate limit", "overloaded",
    "timeout", "502", "504",
)

CONCURRENCY = int(os.getenv("PPI_CONCURRENCY", "10"))
CHECKER_CONCURRENCY = int(os.getenv("PPI_CHECKER_CONCURRENCY", "10"))
PRODUCT_MATCH_JUDGE_CONCURRENCY = CHECKER_CONCURRENCY
PRODUCT_MATCH_JUDGE_MAX_RETRY = int(os.getenv("PPI_KIMI_MAX_RETRY", "5"))
PRODUCT_MATCH_JUDGE_BACKOFF_BASE = float(os.getenv("PPI_KIMI_BACKOFF_BASE", "4"))
PRODUCT_MATCH_JUDGE_BACKOFF_MAX = float(os.getenv("PPI_KIMI_BACKOFF_MAX", "20"))

MAX_PRODUCT_ITERATIONS = int(os.getenv("PPI_MAX_PRODUCT_ITERATIONS", "2"))
MAX_BENEFIT_ITERATIONS = int(os.getenv("PPI_MAX_BENEFIT_ITERATIONS", "1"))
NO_PROGRESS_LIMIT = int(os.getenv("PPI_NO_PROGRESS_LIMIT", "2"))

PROJECT_ROOT = Path(__file__).resolve().parents[2]
_default_state_root = (
    Path(os.getenv("LOCALAPPDATA", str(PROJECT_ROOT / "cache"))) / "Product_PPI" / "ppi_loop"
    if platform.system() == "Windows"
    else PROJECT_ROOT / "cache" / "ppi_loop"
)
STATE_DIR = Path(os.getenv("PPI_LOOP_STATE_DIR", str(_default_state_root)))
CHECKPOINT_DB = STATE_DIR / "checkpoints.sqlite"
DIAGNOSTICS_DIR = Path(
    os.getenv("PPI_LOOP_DIAGNOSTICS_DIR", str(PROJECT_ROOT / "output" / "ppi_loop_diagnostics"))
)

IMG_CACHE_DIR = os.getenv("PPI_IMG_CACHE_DIR", str(PROJECT_ROOT / "cache" / "img_cache"))
CACHE_MAX_DAYS = int(os.getenv("PPI_CACHE_MAX_DAYS", "7"))
IMG_CACHE_ORIGINAL_DIR = IMG_CACHE_DIR + "/original"
IMG_CACHE_VL_ROI_DIR = IMG_CACHE_DIR + "/vl_rois"
IMG_CACHE_OCR_DIR = IMG_CACHE_DIR + "/ocr"

MIN_SHORT_SIDE = 1600
SHARPNESS_FACTOR = 1.3
CONTRAST_FACTOR = 1.15
VISION_ENABLED = _env_bool("PPI_VISION_ENABLED", True)
VISION_DEVICE = os.getenv("PPI_VISION_DEVICE", "auto").strip().lower()
VISION_PIPELINE_VERSION = "paddleocr_vl_1_6_v1"
VISION_PADDLEOCR_VL_VERSION = "v1.6"
VISION_PADDLEOCR_VL_MODEL = "PaddleOCR-VL-1.6-0.9B"
VISION_LAYOUT_MODEL = "PP-DocLayoutV3"
VISION_MAX_ROIS = int(os.getenv("PPI_VISION_MAX_ROIS", "4"))
VISION_LARGE_IMAGE_RATIO = float(os.getenv("PPI_VISION_LARGE_IMAGE_RATIO", "0.60"))
VISION_TILE_ROWS = int(os.getenv("PPI_VISION_TILE_ROWS", "3"))
VISION_TILE_COLS = int(os.getenv("PPI_VISION_TILE_COLS", "3"))
VISION_TILE_OVERLAP_RATIO = float(os.getenv("PPI_VISION_TILE_OVERLAP_RATIO", "0.30"))
VISION_SECONDARY_OCR_ENABLED = _env_bool("PPI_VISION_SECONDARY_OCR_ENABLED", True)
VISION_MAX_PRODUCT_TILE_ROIS = int(os.getenv("PPI_VISION_MAX_PRODUCT_TILE_ROIS", "9"))
VISION_ROI_MIN_SHORT_SIDE = int(os.getenv("PPI_VISION_ROI_MIN_SHORT_SIDE", "800"))
VISION_ROI_MAX_LONG_SIDE = int(os.getenv("PPI_VISION_ROI_MAX_LONG_SIDE", "2048"))
VISION_ROI_PADDING_RATIO = float(os.getenv("PPI_VISION_ROI_PADDING_RATIO", "0.10"))
VISION_WORKERS = int(os.getenv("PPI_VISION_WORKERS", "1"))
FUZZY_MATCH_THRESHOLD = 60
