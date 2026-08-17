# 本包对外只导出一个入口:prepare_visual_assets(单张原图 → 版面/OCR/ROI 资产),
# 其余内部实现(运行时、版面解析等)一律不暴露,保持调用方与实现解耦。
"""
``vision`` 包的公共入口。

这个包使用本地 PaddleOCR-VL-1.6 将单张原图解析为结构化版面块、
完整 Markdown，以及从原图裁剪出的 ROI 局部图。

外部代码只需调用 :func:`prepare_visual_assets`，不必了解内部
的运行时和版面解析实现。
"""

# 只暴露高层编排函数，避免调用方与内部实现耦合。
from .pipeline import prepare_visual_assets

__all__ = ["prepare_visual_assets"]
