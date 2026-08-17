"""原图下载、本地缓存和base64转换；增强产物由vision模块另存。

从 notebook `tets_v2 kimi2.6.ipynb` 的 cell-1 原样迁移并模块化，
所有函数保持与原 notebook 行为一致。
"""

import os
import io
import time
import base64
import hashlib
from typing import Optional

import requests
import urllib3
from PIL import Image
from PIL import ImageEnhance

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from . import config


# ======== 图片预处理增强 ========

def preprocess_image(local_path: str) -> str:
    # 图片增强:短边不足阈值则等比放大(LANCZOS 高质量插值),再统一锐化+提对比,
    # 原地覆盖保存 JPEG;失败只打警告保留原图,不中断主流程。
    """对本地图片做预处理并原地覆盖保存。

    1) 短边 < MIN_SHORT_SIDE 时等比放大到短边=MIN_SHORT_SIDE（LANCZOS 高质量插值）
       短边已足够则不放大。
    2) 放大后统一做轻度锐化 + 提对比。

    失败时静默跳过（保留原图），不影响主流程。
    返回 local_path。
    """
    try:
        img = Image.open(local_path)
        img.load()

        # 统一转 RGB（避免 P / RGBA / CMYK 等模式在增强/保存时报错）
        if img.mode != "RGB":
            img = img.convert("RGB")

        w, h = img.size
        short = min(w, h)

        # 1) 仅当短边低于阈值时放大
        if short < config.MIN_SHORT_SIDE:
            scale = config.MIN_SHORT_SIDE / short
            new_size = (round(w * scale), round(h * scale))
            img = img.resize(new_size, Image.LANCZOS)

        # 2) 锐化 + 提对比（放大与否都做）
        img = ImageEnhance.Sharpness(img).enhance(config.SHARPNESS_FACTOR)
        img = ImageEnhance.Contrast(img).enhance(config.CONTRAST_FACTOR)

        # 覆盖保存为 JPEG（高质量）
        img.save(local_path, format="JPEG", quality=92)
    except Exception as e:
        print(f"[preprocess] 预处理失败，保留原图：{local_path}  错误: {e}")

    return local_path


# ======== 缓存管理 ========

def cleanup_old_cache(base_dir: str = config.IMG_CACHE_DIR,
                      days: int = config.CACHE_MAX_DAYS) -> None:
    # 扫描 original/vl_rois/ocr 三个缓存目录,统计超过 days 天未动的文件,
    # 只报告不删除(安全策略),让调用方决定是否人工清理。
    """扫描 original/vl_rois/ocr 三个子目录并报告过期缓存。"""
    subdirs = [
        config.IMG_CACHE_ORIGINAL_DIR,
        config.IMG_CACHE_VL_ROI_DIR,
        config.IMG_CACHE_OCR_DIR,
    ]
    now = time.time()
    total_expired = 0
    for sd in subdirs:
        os.makedirs(sd, exist_ok=True)
        for name in os.listdir(sd):
            fpath = os.path.join(sd, name)
            try:
                if os.path.isfile(fpath) and (now - os.path.getmtime(fpath) > days * 86400):
                    total_expired += 1
            except Exception as e:
                print(f"[cache] 检查 {name} 失败（已忽略）: {e}")
    if total_expired:
        print(f"[cache] 发现 {total_expired} 个过期缓存文件（分布在 original/vl_rois/ocr），按安全策略不自动删除")


# ======== 图片下载 ========

def download_one(url: str,
                 cache_dir: str = config.IMG_CACHE_ORIGINAL_DIR,
                 days: int = config.CACHE_MAX_DAYS) -> Optional[str]:
    # 单图下载:URL 的 md5 作缓存文件名;命中未过期缓存直接复用并刷新 mtime,
    # 否则按域名匹配 Referer 绕过防盗链(京东/抖音/淘宝系),下载失败返回 None。
    """将单张图片下载到本地缓存并返回本地路径。

    - 命中未过期缓存则直接复用（刷新 mtime）。
    - 下载成功后原样保留，不原地增强或改写。
    - 根据图片域名匹配 Referer 绕过防盗链（京东/天猫/抖音）。
    - 失败返回 None。
    """
    os.makedirs(cache_dir, exist_ok=True)

    ext = os.path.splitext(url.split("?")[0])[1]
    if ext.lower() not in [".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"]:
        ext = ".jpg"
    fname = hashlib.md5(url.encode("utf-8")).hexdigest() + "_original" + ext
    fpath = os.path.join(cache_dir, fname)

    # 命中未过期缓存：刷新 mtime 后直接复用
    if os.path.isfile(fpath) and (time.time() - os.path.getmtime(fpath) <= days * 86400):
        os.utime(fpath, None)
        return fpath

    # 下载
    try:
        low = url.lower()
        if "360buyimg" in low or "jd.com" in low:
            referer = "https://www.jd.com/"
        elif "douyin" in low or "bytimg" in low or "byteimg" in low or "amemv" in low or "pstatp" in low:
            referer = "https://www.douyin.com/"
        elif "alicdn" in low or "taobao" in low or "tmall" in low:
            referer = "https://www.taobao.com/"
        else:
            referer = "https://www.taobao.com/"

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
            ),
            "Referer": referer,
        }
        resp = requests.get(url, headers=headers, verify=False, timeout=30)
        resp.raise_for_status()
        with open(fpath, "wb") as f:
            f.write(resp.content)

        # 原图只读保留；增强图由 vision pipeline 另存为派生产物。
        return fpath
    except Exception as e:
        print(f"[download] 下载失败，已跳过: {url}  错误: {e}")
        return None


# ======== base64 转换 ========

def local_to_data_url(local_path: str) -> str:
    # 把本地图片读成 base64 data URL(供 VLM 的 image_url 字段使用);
    # MIME 按图片实际内容判断,避免扩展名与 JPEG 派生内容不一致。
    """将本地图片文件转为 base64 data URL。"""
    # MIME按实际图片内容判断，避免扩展名与JPEG派生内容不一致。
    try:
        with Image.open(local_path) as img:
            image_format = (img.format or "JPEG").upper()
    except Exception:
        image_format = "JPEG"
    mime_map = {
        "JPEG": "jpeg", "PNG": "png", "WEBP": "webp",
        "BMP": "bmp", "GIF": "gif",
    }
    mime = mime_map.get(image_format, "jpeg")
    with open(local_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("utf-8")
    return f"data:image/{mime};base64,{b64}"
