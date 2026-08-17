"""n5_classify — 主品/赠品类型归一化 + 礼盒拆解。

对应 prompt:
- Step3.2: 礼盒拆解(VLM 识别 is_gift_box/box_contents → Python 拆成独立条目)
- Step3.3.1: 主品/赠品归类(VLM 给出 role → 本节点验正)
- Step3.3.2: 所有非主品产品统一归为赠品，不再区分满赠。

当前口径:
- role=主品 → 主品
- role=赠品 → 赠品
- gift_label 仅保留为识别上下文，不再产生类型或使用条件差异
"""

from typing import Any

# ==================== 赠品类型归一化 ====================


def classify_gift_type(gift_label: str | None) -> tuple[str, str | None]:
    # 用途:赠品类型归一化(遗留模块,仅测试引用;graph 运行时已不调用本文件)。
    # 要点:当前口径所有非主品一律返回("赠品",None),不再区分满赠与使用门槛。
    """根据当前业务口径统一返回赠品，不输出赠品门槛。

    Args:
        gift_label: VLM 提供的赠品标签原文

    Returns:
        (type_name, threshold): 固定为 ``("赠品", None)``

    >>> classify_gift_type("满1000赠")
    ('赠品', None)
    >>> classify_gift_type(None)
    ('赠品', None)
    """
    return "赠品", None


# ==================== 礼盒拆解 ====================

def unpack_gift_box(item: dict[str, Any]) -> list[dict[str, Any]]:
    # 用途:把礼盒条目按 box_contents 拆成独立单品条目,其余字段原样保留。
    # 要点:仅当 box_contents 明确含"+"且能拆出多条时才拆,否则原样返回不强拆。
    """将礼盒条目拆为独立单品条目。

    从 box_contents 原始文本中拆出各单品,只在 box_contents 有明确内容时拆;
    无法确认则保留原条目(不强拆)。
    """
    contents = (item.get("box_contents") or "").strip()
    if not contents or "+" not in contents:
        return [item]

    from .n3_normalize import split_gift_detail
    sub_items = split_gift_detail(contents)

    if not sub_items or len(sub_items) <= 1:
        return [item]

    unpacked = []
    for si in sub_items:
        new_item = dict(item)
        new_item["raw_name"] = si.get("raw_name", item.get("raw_name", ""))
        new_item["spec_text"] = (
            f"{si.get('spec','')}{si.get('unit','')}"
            if si.get("spec") else None
        )
        new_item["qty_text"] = si.get("qty", "1")
        new_item["is_gift_box"] = False
        new_item["box_contents"] = None
        if si.get("gift_label", "赠品") != "赠品":
            new_item["gift_label"] = si["gift_label"]
        unpacked.append(new_item)

    return unpacked


# ==================== 主节点 ====================

def classify_items(matched_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # 用途:批量归类——先拆礼盒,再按 role 打"类型":主品保留,其余一律归赠品。
    # 要点:名称取 n4 的 matched_name(缺省回退 raw_name),并移除"使用条件"字段。
    """对匹配后的条目做主品/赠品分类并拆解礼盒。

    逻辑:
    1. 礼盒条目(is_gift_box=True) → unpack_gift_box 拆成单品
    2. role=主品 → 主品；其余产品角色 → 赠品
    3. 产品条目不保留赠品门槛使用条件
    """
    results = []

    expanded_items = []
    for item in matched_items:
        if item.get("is_gift_box"):
            expanded_items.extend(unpack_gift_box(item))
        else:
            expanded_items.append(item)

    for item in expanded_items:
        result = dict(item)
        role = item.get("role", "主品")

        if role == "主品":
            result["类型"] = "主品"
        else:
            result["类型"], _ = classify_gift_type(item.get("gift_label"))
        result.pop("使用条件", None)

        # 使用 VLM 匹配到的名称(n4 matched_name)
        result["名称"] = item.get("matched_name") or item.get("raw_name")

        results.append(result)

    return results


def get_node():
    # 用途:返回 LangGraph 节点函数(遗留包装,当前 graph 已不引用本模块)。
    """返回 LangGraph 节点函数。"""
    def classify_node(state: dict) -> dict:
        # 用途:节点主体——读 matched_items,调用 classify_items 后写回 classified_items。
        items = state.get("matched_items") or []
        classified = classify_items(items)
        return {"classified_items": classified}
    return classify_node


node = get_node()
