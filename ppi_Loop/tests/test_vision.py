from PIL import Image

from ppi_loop.image_utils import local_to_data_url
from ppi_loop.nodes.n1_prepare import build_multimodal_message
from ppi_loop.vision import pipeline
from ppi_loop.vision.ocr import (
    find_largest_image_block,
    generate_overlapping_tiles,
    merge_ocr_text,
    normalize_paddleocr_vl_results,
    run_paddleocr_vl_ocr_only,
    select_vl_regions,
)
from ppi_loop.vision.runtime import select_paddle_device


def test_paddle_device_selection():
    assert select_paddle_device("auto", "Darwin") == "cpu"
    assert select_paddle_device("auto", "Windows") == "gpu:0"
    assert select_paddle_device("cpu", "Windows") == "cpu"


def test_normalize_vl_json_blocks_spotting_and_markdown():
    class FakeResult:
        json = {"res": {
            "parsing_res_list": [{
                "block_label": "text",
                "block_content": "柔肤液25ml+洁面15ml+夜霜5ml",
                "block_bbox": [1, 2, 101, 22],
            }],
            "spotting_res": {
                "rec_texts": ["券后价¥300"],
                "rec_polys": [[[5, 30], [100, 30], [100, 50], [5, 50]]],
            },
        }}
        markdown = {"markdown_texts": "柔肤液25ml+洁面15ml+夜霜5ml"}

    blocks, markdown = normalize_paddleocr_vl_results([FakeResult()])
    assert [block["text"] for block in blocks] == [
        "柔肤液25ml+洁面15ml+夜霜5ml", "券后价¥300",
    ]
    assert blocks[0]["box"] == [[1.0, 2.0], [101.0, 2.0], [101.0, 22.0], [1.0, 22.0]]
    assert markdown == "柔肤液25ml+洁面15ml+夜霜5ml"


def test_largest_image_block_and_grid_geometry():
    blocks = [
        {"text": "small", "label": "image",
         "box": [[0, 0], [300, 0], [300, 300], [0, 300]]},
        {"text": "large", "label": "figure",
         "box": [[0, 0], [900, 0], [900, 900], [0, 900]]},
    ]
    largest = find_largest_image_block(blocks, 1000, 1000)
    assert largest["block"]["text"] == "large"
    assert largest["area_ratio"] == 0.81

    tiles = generate_overlapping_tiles(largest["box"], 1000, 1000, 3, 3, 0.10)
    assert [tile["tile_id"] for tile in tiles] == [
        f"tile_r{row}_c{col}" for row in range(1, 4) for col in range(1, 4)
    ]
    assert len(tiles) == 9
    assert tiles[0]["box"][0:2] == (0, 0)
    assert tiles[-1]["box"][2:4] == (900, 900)
    # 核心宽300，相邻两块共重叠30像素，即10%。
    assert tiles[0]["box"][2] - tiles[1]["box"][0] == 30


def test_roi_selection_only_keeps_image_or_figure_blocks():
    blocks = [
        {"text": "图中物品解析内容", "label": "image",
         "box": [[100, 100], [600, 100], [600, 600], [100, 600]]},
        {"text": "说明甲", "label": "text",
         "box": [[120, 610], [260, 610], [260, 650], [120, 650]]},
        {"text": "页面标题", "label": "doc_title",
         "box": [[200, 20], [500, 20], [500, 70], [200, 70]]},
    ]
    regions = select_vl_regions(blocks, 800, 800)
    assert len(regions) == 1
    assert regions[0]["region_type"] == "product_visual"
    assert regions[0]["ocr_text"] == "图中物品解析内容"
    assert all("priority" not in region for region in regions)


def test_merge_ocr_text_exact_dedup_only():
    merged = merge_ocr_text(
        [{"text": "柔肤液25ml\n洁面15ml"}],
        "",
        [
            {"tile_id": "tile_r1_c1", "success": True, "text": "洁面15ml\n夜霜5ml"},
            {"tile_id": "tile_r1_c2", "success": True, "text": "洁面 15ml"},
        ],
    )
    assert merged.count("洁面15ml") == 1
    assert "夜霜5ml" in merged
    assert "洁面 15ml" in merged
    assert "[来源=" not in merged
    assert "tile_r1_c1" not in merged


def test_secondary_ocr_call_disables_layout_detection():
    captured = {}

    class FakePipeline:
        def predict(self, **kwargs):
            captured.update(kwargs)
            return []

    class Runtime:
        def get_pipeline(self):
            return FakePipeline()

    blocks, markdown = run_paddleocr_vl_ocr_only(Runtime(), "tile")
    assert blocks == [] and markdown == ""
    assert captured["use_layout_detection"] is False
    assert captured["prompt_label"] == "ocr"
    assert captured["merge_layout_blocks"] is False


def test_multimodal_message_contains_only_original_and_merged_ocr_text():
    messages = build_multimodal_message(
        "https://example/image.jpg", "data:image/jpeg;base64,original", "防晒",
        ["蓝胖子防晒"], "system",
        ocr_merged_text="蓝胖子防晒50ml",
        task="products",
    )
    content = messages[1]["content"]
    urls = [item["image_url"]["url"] for item in content if item.get("type") == "image_url"]
    assert urls == ["data:image/jpeg;base64,original"]
    assert "PaddleOCR-VL合并OCR文本" in content[0]["text"]
    assert "蓝胖子防晒50ml" in content[0]["text"]
    assert "[来源=" not in content[0]["text"]
    assert "完整Markdown" not in content[0]["text"]
    assert "版面块" not in content[0]["text"]


def test_data_url_uses_actual_image_format(tmp_path):
    path = tmp_path / "image.png"
    Image.new("RGB", (8, 8), "white").save(path, format="JPEG")
    assert local_to_data_url(str(path)).startswith("data:image/jpeg;base64,")


def _patch_cache_dirs(monkeypatch, tmp_path):
    monkeypatch.setattr(pipeline.config, "IMG_CACHE_VL_ROI_DIR", str(tmp_path / "rois"))
    monkeypatch.setattr(pipeline.config, "IMG_CACHE_OCR_DIR", str(tmp_path / "ocr"))


def test_visual_pipeline_ocr_failure_keeps_original_only(monkeypatch, tmp_path):
    path = tmp_path / "sample_original.jpg"
    Image.new("RGB", (64, 64), "white").save(path)

    class Runtime:
        def diagnostics(self):
            return {"backend": "paddleocr_vl", "paddle_device": "cpu"}

    _patch_cache_dirs(monkeypatch, tmp_path)
    monkeypatch.setattr(pipeline, "get_vision_runtime", lambda: Runtime())
    monkeypatch.setattr(
        pipeline, "run_paddleocr_vl",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("no vl")),
    )
    result = pipeline.prepare_visual_assets(str(path))
    assert result["ocr_blocks"] == []
    assert result["ocr_markdown"] == ""
    assert result["ocr_merged_text"] == ""
    assert result["ocr_tile_results"] == []
    assert result["visual_regions"] == []
    assert result["vision_diagnostics"]["errors"]


def test_visual_pipeline_large_image_runs_nine_serial_tiles(monkeypatch, tmp_path):
    path = tmp_path / "sample_original.jpg"
    Image.new("RGB", (100, 100), "white").save(path)

    class Runtime:
        def diagnostics(self):
            return {"backend": "paddleocr_vl", "paddle_device": "cpu"}

    calls = []
    _patch_cache_dirs(monkeypatch, tmp_path)
    monkeypatch.setattr(pipeline, "get_vision_runtime", lambda: Runtime())
    monkeypatch.setattr(pipeline, "run_paddleocr_vl", lambda *_args: ([{
        "text": "原图文本", "label": "image",
        "box": [[0, 0], [100, 0], [100, 90], [0, 90]],
    }], ""))

    def fake_tile_ocr(_runtime, image):
        calls.append(image.shape[:2])
        index = len(calls)
        return ([{
            "text": f"分块文本{index}", "label": "ocr",
            "box": [[0, 0], [10, 0], [10, 10], [0, 10]],
        }], "")

    monkeypatch.setattr(pipeline, "run_paddleocr_vl_ocr_only", fake_tile_ocr)
    result = pipeline.prepare_visual_assets(str(path))
    assert len(calls) == 9
    assert len(result["ocr_tile_results"]) == 9
    assert len(result["visual_regions"]) == 9
    assert all(r["region_type"] == "product_tile_candidate" for r in result["visual_regions"])
    assert all("data_url" not in r for r in result["visual_regions"])
    assert "分块文本9" in result["ocr_merged_text"]
    assert "[来源=" not in result["ocr_merged_text"]
    assert result["vision_diagnostics"]["secondary_ocr_triggered"] is True
    assert result["vision_diagnostics"]["tile_success_count"] == 9


def test_visual_pipeline_exact_sixty_percent_does_not_trigger(monkeypatch, tmp_path):
    path = tmp_path / "sample_original.jpg"
    Image.new("RGB", (100, 100), "white").save(path)

    class Runtime:
        def diagnostics(self):
            return {"backend": "paddleocr_vl", "paddle_device": "cpu"}

    _patch_cache_dirs(monkeypatch, tmp_path)
    monkeypatch.setattr(pipeline, "get_vision_runtime", lambda: Runtime())
    monkeypatch.setattr(pipeline, "run_paddleocr_vl", lambda *_args: ([{
        "text": "视觉块", "label": "image",
        "box": [[0, 0], [100, 0], [100, 60], [0, 60]],
    }], ""))
    monkeypatch.setattr(
        pipeline, "run_paddleocr_vl_ocr_only",
        lambda *_args: (_ for _ in ()).throw(AssertionError("不应调用二次OCR")),
    )
    result = pipeline.prepare_visual_assets(str(path))
    assert result["vision_diagnostics"]["large_image_area_ratio"] == 0.6
    assert result["vision_diagnostics"]["secondary_ocr_triggered"] is False
    assert result["ocr_tile_results"] == []
    assert len(result["visual_regions"]) == 1
    assert result["visual_regions"][0]["region_type"] == "product_visual"


def test_visual_pipeline_one_tile_failure_keeps_other_tiles(monkeypatch, tmp_path):
    path = tmp_path / "sample_original.jpg"
    Image.new("RGB", (100, 100), "white").save(path)

    class Runtime:
        def diagnostics(self):
            return {"backend": "paddleocr_vl", "paddle_device": "cpu"}

    calls = 0
    _patch_cache_dirs(monkeypatch, tmp_path)
    monkeypatch.setattr(pipeline, "get_vision_runtime", lambda: Runtime())
    monkeypatch.setattr(pipeline, "run_paddleocr_vl", lambda *_args: ([{
        "text": "大图", "label": "image",
        "box": [[0, 0], [100, 0], [100, 100], [0, 100]],
    }], ""))

    def fake_tile_ocr(_runtime, _image):
        nonlocal calls
        calls += 1
        if calls == 5:
            raise RuntimeError("单块失败")
        return ([{"text": f"成功{calls}", "label": "ocr", "box": []}], "")

    monkeypatch.setattr(pipeline, "run_paddleocr_vl_ocr_only", fake_tile_ocr)
    result = pipeline.prepare_visual_assets(str(path))
    assert calls == 9
    assert len(result["visual_regions"]) == 9
    assert result["vision_diagnostics"]["tile_success_count"] == 8
    assert result["vision_diagnostics"]["tile_failure_count"] == 1
    assert result["ocr_tile_results"][4]["success"] is False
