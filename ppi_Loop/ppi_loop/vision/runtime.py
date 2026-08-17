"""PaddleOCR-VL-1.6 本地运行时与并发控制。"""

from __future__ import annotations

import platform
import threading
from contextlib import contextmanager
from typing import Any, Iterator

from .. import config


def select_paddle_device(device: str = "auto", system: str | None = None) -> str:
    # 根据配置和操作系统决定PaddleOCR-VL跑在CPU还是GPU
    # Mac强制CPU；Windows/Linux的auto默认用GPU 0；显式指定则按指定值返回
    """Mac 使用 CPU；Windows/Linux 的 auto 优先 GPU 0。"""
    system = system or platform.system()
    if device == "cpu":
        return "cpu"
    if device in {"cuda", "gpu", "gpu:0"}:
        return "gpu:0"
    return "cpu" if system == "Darwin" else "gpu:0"


class VisionRuntime:
    """进程级懒加载 PaddleOCR-VL Pipeline。"""

    def __init__(self) -> None:
        # 初始化运行时：模型懒加载、记录加载错误、选定推理设备
        # 用线程锁保证并发场景下模型只加载一次
        self._pipeline: Any | None = None
        self._error = ""
        self.paddle_device = select_paddle_device(config.VISION_DEVICE)
        self._load_lock = threading.Lock()

    def _create_pipeline(self, device: str) -> Any:
        # 用配置里的模型名和版本参数构建PaddleOCRVL Pipeline实例
        # 关闭文档方向分类/矫正和图表、印章识别，只保留版面检测+OCR
        from paddleocr import PaddleOCRVL

        return PaddleOCRVL(
            pipeline_version=config.VISION_PADDLEOCR_VL_VERSION,
            layout_detection_model_name=config.VISION_LAYOUT_MODEL,
            vl_rec_model_name=config.VISION_PADDLEOCR_VL_MODEL,
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_ocr_for_image_block=True,
            use_layout_detection=True,
            use_chart_recognition=False,
            use_seal_recognition=False,
            format_block_content=False,
            merge_layout_blocks=True,
            device=device,
        )

    def get_pipeline(self) -> Any:
        # 获取已加载的Pipeline，首次调用时才真正加载模型
        # GPU初始化失败只回退CPU一次；加载失败记录错误并抛RuntimeError
        """首次调用时加载模型；GPU失败时仅回退一次CPU。"""
        if self._pipeline is not None:
            return self._pipeline
        if self._error:
            raise RuntimeError(self._error)
        with self._load_lock:
            if self._pipeline is not None:
                return self._pipeline
            try:
                try:
                    self._pipeline = self._create_pipeline(self.paddle_device)
                except Exception as gpu_exc:
                    if self.paddle_device == "cpu":
                        raise
                    print(f"[vision] PaddleOCR-VL GPU初始化失败，回退CPU: {gpu_exc}")
                    self.paddle_device = "cpu"
                    self._pipeline = self._create_pipeline("cpu")
                print(
                    "[vision] PaddleOCR-VL已加载: "
                    f"version={config.VISION_PADDLEOCR_VL_VERSION} "
                    f"device={self.paddle_device}"
                )
                return self._pipeline
            except Exception as exc:
                self._error = str(exc)
                raise RuntimeError(self._error) from exc

    def diagnostics(self) -> dict:
        # 返回当前运行时的诊断信息：模型名、设备、加载错误等
        # 供pipeline把运行状态并入vision_diagnostics
        return {
            "backend": "paddleocr_vl",
            "pipeline_version": config.VISION_PADDLEOCR_VL_VERSION,
            "vl_model": config.VISION_PADDLEOCR_VL_MODEL,
            "layout_model": config.VISION_LAYOUT_MODEL,
            "paddle_device": self.paddle_device,
            "runtime_error": self._error,
        }


_RUNTIME = VisionRuntime()
_VISION_SEMAPHORE = threading.BoundedSemaphore(max(1, config.VISION_WORKERS))


def get_vision_runtime() -> VisionRuntime:
    # 返回进程级共享的VisionRuntime单例
    # 所有调用方共用同一个模型实例，避免重复加载
    return _RUNTIME


@contextmanager
def vision_slot() -> Iterator[None]:
    # 上下文管理器：进入时获取信号量，退出时释放
    # 用信号量限制同时进行的视觉任务数，防止显存被占满
    _VISION_SEMAPHORE.acquire()
    try:
        yield
    finally:
        _VISION_SEMAPHORE.release()


def reset_runtime_for_tests() -> None:
    # 测试专用：重置全局单例，让下次调用重新加载模型
    global _RUNTIME
    _RUNTIME = VisionRuntime()
