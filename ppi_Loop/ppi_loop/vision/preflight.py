"""PaddleOCR-VL-1.6 本地运行环境预检。"""

from __future__ import annotations

import importlib.util
import json
import platform

from .. import config
from .runtime import select_paddle_device


def collect_preflight() -> dict:
    # 收集运行环境预检报告：系统信息、Python版本、视觉相关配置
    # 检查cv2/paddle/paddleocr是否可导入，能导入则补充版本和CUDA信息
    # 返回一个dict报告，供CLI打印或上层日志使用
    packages = {
        name: importlib.util.find_spec(name) is not None
        for name in ("cv2", "paddle", "paddleocr")
    }
    report = {
        "system": platform.system(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "vision_enabled": config.VISION_ENABLED,
        "vision_workers": config.VISION_WORKERS,
        "requested_device": select_paddle_device(config.VISION_DEVICE),
        "pipeline_version": config.VISION_PADDLEOCR_VL_VERSION,
        "vl_model": config.VISION_PADDLEOCR_VL_MODEL,
        "layout_model": config.VISION_LAYOUT_MODEL,
        "packages": packages,
        "paddleocr_vl_importable": False,
    }
    if packages["paddle"]:
        import paddle
        report["paddle_version"] = getattr(paddle, "__version__", "")
        report["paddle_compiled_with_cuda"] = bool(paddle.is_compiled_with_cuda())
    if packages["paddleocr"]:
        try:
            from paddleocr import PaddleOCRVL  # noqa: F401
            report["paddleocr_vl_importable"] = True
        except Exception as exc:
            report["paddleocr_vl_import_error"] = str(exc)
    return report


def main() -> None:
    # 命令行入口：收集预检报告并以JSON格式打印到标准输出
    print(json.dumps(collect_preflight(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
