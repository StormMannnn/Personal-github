"""n3_normalize — 商品归一化 + 规格/单位/数量分离 + 赠品明细拆条。

对应 prompt: Step2(归一化去修饰词)、Step3.5(规格单位分离/数量兜底/1片×7)。
"""

import re
from decimal import Decimal, InvalidOperation
from typing import Any


# 常见促销修饰词（按中文逗号/空格/关键词去除）
PROMO_MODIFIERS = re.compile(
    r"(全新|升级|新升级|新一代|抗皱|紧致|保湿|护肤|化妆品|礼盒|"
    r"520|礼物|送女友|送男友|新年|618|双11|双十二|"
    r"限量|限定|新品|首发|爆款|热卖)",
    re.IGNORECASE,
)

# 规格单位分离正则： "50ml" → 规格="50", 单位="ml"
SPEC_UNIT_RE = re.compile(
    r"""(?P<spec>\d+(?:\.\d+)?)\s*         # 数字（可为小数）
        (?P<unit>ml|mL|ML|g|kg|片|支|瓶|盒|张|套|个|oz|fl|L)  # 单位
    """,
    re.VERBOSE | re.IGNORECASE,
)

# 乘号形式解析： "5ml×7" → 规格="5", 单位="ml", 数量="7"
MULTIPLY_RE = re.compile(
    r"""(?P<spec>\d+(?:\.\d+)?)\s*(?P<unit>ml|mL|ML|g|kg|片|支|瓶|盒|张|套|个|oz|fl|L)\s*
        [×xX*]\s*(?P<qty>\d+)
    """,
    re.VERBOSE,
)

# 赠品明细分隔符： "黑绷带5ml×5 + 白绷带5ml×1 (会员直播加赠) + 小露珠30ml×1"
GIFT_SPLIT_RE = re.compile(r"""\s*\+\s*""")
EXPLICIT_MULTIPLIER_RE = re.compile(r"[×xX*]")
GENERIC_GIFT_LABELS = {"", "赠", "赠品", "享"}


def strip_matching_trailing_spec(raw_name: str, spec_text: str | None) -> str:
    # 用途:去掉名称末尾与规格完全一致的文本(如"防晒50ml"+规格"50ml"→"防晒")。
    # 要点:只删末尾、忽略大小写,带数字边界保护防止误截"150ml"里的"50ml",删空则保留原名。
    r"""删除 raw_name 末尾与 spec_text 完全对应的规格文本。

    规则:
    - spec_text 为空 → 原样返回。
    - 只删名称末尾，不删名称中间的相同文本。
    - 忽略大小写（50ml 与 50ML）。
    - 允许名称与规格间有空格。
    - 数字边界保护：spec_text="50ml" 不能错误截断 raw_name="防晒150ml"。
    - 删除后名称为空 → 保留原名称。

    >>> strip_matching_trailing_spec("「蓝胖子」防晒50ml", "50ml")
    '「蓝胖子」防晒'
    >>> strip_matching_trailing_spec("防晒 50ML", "50ml")
    '防晒'
    >>> strip_matching_trailing_spec("防晒150ml", "50ml")   # 数字边界保护
    '防晒150ml'
    >>> strip_matching_trailing_spec("50ml防晒", "50ml")     # 不在末尾
    '50ml防晒'
    >>> strip_matching_trailing_spec("防晒", None)
    '防晒'
    """
    if not spec_text:
        return raw_name
    # 转义 spec_text 中的正则特殊字符，忽略大小写
    escaped = re.escape(spec_text)
    # 末尾匹配：(?<!\d) 数字边界保护，确保 spec_text 前面不是数字
    # 可选空格 + 规格文本 + 末尾
    pattern = re.compile(rf"(?<!\d)\s*{escaped}$", re.IGNORECASE)
    result = pattern.sub("", raw_name)
    if not result.strip():
        return raw_name
    return result


def normalize_name(raw_name: str) -> str:
    # 用途:清洗商品名,去掉促销修饰词(抗皱/礼盒/618等),再合并多余空格。
    # 输出:归一化后的干净名称,供后续去重与匹配使用。
    """去除促销修饰词，返回归一化商品名。

    >>> normalize_name("赫莲娜HR新黑绷带面霜50ml抗皱紧致保湿护肤品化妆品礼盒")
    '赫莲娜hr黑绷带面霜50ml'
    """
    cleaned = PROMO_MODIFIERS.sub("", raw_name)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def split_spec_unit(text: str) -> tuple[str | None, str | None]:
    # 用途:把规格文本拆成(数值,单位),如"50ml"→("50","ml")。
    # 要点:只认"数字+已知单位"结构,匹配不到返回(None,None),单位统一转小写。
    """从规格文本中分离规格数值和单位。

    >>> split_spec_unit("50ml")
    ('50', 'ml')
    >>> split_spec_unit("125")
    (None, None)
    """
    m = SPEC_UNIT_RE.search(text)
    if m:
        return m.group("spec"), m.group("unit").lower()
    return None, None


def parse_qty_from_text(text: str) -> str:
    # 用途:从文本提取数量,如"5ml×7"→"7";识别不到一律兜底"1"。
    # 要点:优先解析乘号形式,其次匹配独立数字(支持"6对"),保证数量字段恒有值。
    """从数量文本中提取数量，兜底 '1'。

    优先检测乘号形式 "5ml×7" → "7"（配合 split_spec_unit 使用）
    也处理 "×7" 独立出现的情况。
    """
    m = MULTIPLY_RE.search(text)
    if m:
        return m.group("qty")
    # 独立乘号/数字 — 支持 "6对"、"6 对"
    m2 = re.search(r"[×xX*]?\s*(\d+)\s*(?:片|支|瓶|盒|张|套|个|对)?$", text)
    if m2:
        return m2.group(1)
    return "1"


def split_gift_detail(detail_text: str) -> list[dict[str, Any]]:
    # 用途:把"黑绷带5ml×5 + 白绷带5ml×1(会员加赠)"按"+"拆成多条独立赠品明细。
    # 输出:每条含 raw_name/spec/unit/qty/gift_label,括号内文本作为 gift_label。
    # 要点:乘号形式优先拆规格数量,无乘号时按简单规格兜底并从名称中剔除规格片段。
    r"""将赠品明细文本拆为独立条目。

    输入: '黑绷带面霜5ml×5 + 黑绷带面霜5ml×1(会员直播加赠) + 白绷带面霜5ml×1'
    输出: 每条目含 raw_name / spec / unit / qty / gift_label

    >>> items = split_gift_detail('黑绷带面霜5ml×5')
    >>> items[0]['raw_name']
    '黑绷带面霜'
    >>> items[0]['gift_label']
    '赠品'
    """
    result = []
    parts = GIFT_SPLIT_RE.split(detail_text)
    for part in parts:
        part = part.strip()
        if not part:
            continue

        # 提取赠品标签（括号内的文本）
        gift_label = "赠品"
        label_match = re.search(r"[（(](.+?)[）)]", part)
        if label_match:
            gift_label = label_match.group(1)
            part = re.sub(r"[（(].+?[）)]", "", part).strip()

        # 分离名称和规格数量
        # 模式: "产品名 50ml×5" 或 "产品名5ml×5"
        m = re.match(
            r"^(?P<name>.+?)\s*(?P<spec_qty>\d+(?:\.\d+)?\s*(?:ml|mL|ML|g|kg|片|支|瓶|盒|张|套|个)\s*[×xX*]\s*\d+)$",
            part,
            re.IGNORECASE,
        )
        if m:
            raw_name = m.group("name").strip()
            spec_qty_text = m.group("spec_qty")

            m2 = MULTIPLY_RE.search(spec_qty_text)
            if m2:
                spec = m2.group("spec")
                unit = m2.group("unit").lower()
                qty = m2.group("qty")
            else:
                spec, unit = split_spec_unit(spec_qty_text)
                qty = parse_qty_from_text(spec_qty_text)
        else:
            # 简单规格（无乘号）
            raw_name = part
            spec, unit = split_spec_unit(part)
            qty = parse_qty_from_text(part)
            # 从名称中去掉规格部分
            if spec and unit:
                raw_name = re.sub(
                    rf"\s*\d+(?:\.\d+)?\s*{unit}\b",
                    "",
                    raw_name,
                    flags=re.IGNORECASE,
                ).strip()

        result.append({
            "raw_name": raw_name,
            "spec": spec,
            "unit": unit,
            "qty": qty if qty else "1",
            "gift_label": gift_label,
        })
    return result


def normalize_item(item: dict[str, Any]) -> dict[str, Any]:
    # 用途:归一化单个 VLM 商品条目:清洗名称、分离规格/单位、提取数量。
    # 要点:v9 兜底——规格识别不出写"0"、数量写"1"、单位可为空,字段不省略;
    #       先剥掉名称末尾与 spec_text 一致的规格再归一化,避免名称重复带规格。
    """对单个 VLM 提取产品做归一化处理。

    输入: n2 raw_items 中的一个条目
    输出: 归一化后的条目(ready for n4 matching)

    v9 兜底:
    - 规格无法识别 → 输出 "0"
    - 数量无法识别 → 兜底 "1",不省略字段
    """
    result = dict(item)

    # 先删除 raw_name 末尾与 spec_text 完全一致的规格文本，再归一化
    # （"「蓝胖子」防晒50ml" + spec_text="50ml" → 先剥离 → "「蓝胖子」防晒"）
    raw = item.get("raw_name", "")
    spec_text = item.get("spec_text") or ""
    cleaned_raw = strip_matching_trailing_spec(raw, spec_text)
    result["normalized_name"] = normalize_name(cleaned_raw)

    # 规格/单位分离
    spec_text = item.get("spec_text") or ""
    spec, unit = split_spec_unit(spec_text)
    # 规格无值时统一兜底 "0"；单位仍保持空字符串。
    result["spec"] = spec if spec else "0"
    result["unit"] = unit if unit else ""

    # 数量提取(v9: 始终有值,兜底"1",不省略字段)
    qty_text = item.get("qty_text") or spec_text
    result["qty"] = parse_qty_from_text(qty_text)

    # 计数单位回填：规格未解析出单位时，从 qty_text 的"数字+计数单位"结构提取
    # ("6对" → unit="对"; 50ml 的 "ml" 已由 spec_text 解析，不覆盖)
    if not result["unit"]:
        qty_txt = item.get("qty_text") or ""
        unit_m = re.search(r"\d+\s*(片|支|瓶|盒|张|套|个|对)", qty_txt)
        if unit_m:
            result["unit"] = unit_m.group(1)

    return result


def _positive_decimal(value: Any) -> Decimal | None:
    # 用途:把规格/数量字符串转成正数 Decimal,供等总量对比使用。
    # 要点:解析失败或值≤0 时返回 None,非法值不参与计算。
    """将规格或数量转为正Decimal，非法值返回None。"""
    try:
        number = Decimal(str(value).strip())
    except (InvalidOperation, ValueError, TypeError):
        return None
    return number if number > 0 else None


def _has_explicit_multiplier(item: dict[str, Any]) -> bool:
    # 用途:判断条目是否明确写了乘号(如"5ml×5")。
    # 要点:只认原始 spec_text/qty_text 里的乘号,防止按默认数量误删真实明细。
    """原始规格或数量必须明确包含乘号，避免根据默认数量误删。"""
    evidence = f"{item.get('spec_text') or ''} {item.get('qty_text') or ''}"
    return bool(EXPLICIT_MULTIPLIER_RE.search(evidence))


def _gift_context_key(item: dict[str, Any]) -> str:
    # 用途:生成赠送上下文键,用于比较两条赠品的赠送条件是否一致。
    # 要点:"赠/赠品/享"等通用标签统一归为 generic_gift,其余保留原文并去除空白标点。
    """归一化赠送条件；普通的“赠/赠品/享”视为同一上下文。"""
    label = re.sub(r"[\s:：,，。;；]+", "", str(item.get("gift_label") or ""))
    return "generic_gift" if label in GENERIC_GIFT_LABELS else label


def _dedupe_name(item: dict[str, Any]) -> str:
    # 用途:取条目用于等总量匹配的名称,优先用 n3 归一化名。
    # 要点:拆条结果缺 normalized_name 时,安全回退到 raw_name 再归一化。
    """优先使用n3名称；拆条结果缺少该字段时从raw_name安全回退。"""
    normalized = str(item.get("normalized_name") or "").strip()
    if normalized:
        return normalized
    return normalize_name(str(item.get("raw_name") or ""))


def remove_equivalent_gift_summaries(
    items: list[dict[str, Any]],
    *,
    return_decisions: bool = False,
) -> list[dict[str, Any]] | tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    # 用途:删除与"明确乘法明细"等总量的隐式赠品汇总行(如"5ml×5"汇总出的"25ml×1")。
    # 要点:汇总行须同名称、同赠送上下文、数量为1且规格总量相等才删;删除时生成对账记录,
    #       return_decisions=True 时一并返回,供 Coverage Checker 区分已删汇总与真实漏品。
    """删除与明确乘法明细等总量的隐式赠品汇总条目。

    默认保持旧返回值；生产处理链可要求返回可审计的确定性对账记录，
    供Coverage Checker区分“被删除的汇总行”和真正漏品。
    """
    removed_indexes: set[int] = set()
    decisions: list[dict[str, Any]] = []

    for detail_index, detail in enumerate(items):
        if detail.get("role") != "赠品" or not _has_explicit_multiplier(detail):
            continue

        detail_spec = _positive_decimal(detail.get("spec"))
        detail_qty = _positive_decimal(detail.get("qty"))
        detail_unit = str(detail.get("unit") or "").strip().lower()
        detail_name = _dedupe_name(detail)
        if not detail_spec or not detail_qty or detail_qty <= 1 or not detail_unit or not detail_name:
            continue
        detail_total = detail_spec * detail_qty

        for summary_index, summary in enumerate(items):
            if summary_index == detail_index or summary_index in removed_indexes:
                continue
            if summary.get("role") != "赠品" or _has_explicit_multiplier(summary):
                continue
            if _dedupe_name(summary) != detail_name:
                continue
            if _gift_context_key(summary) != _gift_context_key(detail):
                continue

            summary_spec = _positive_decimal(summary.get("spec"))
            summary_qty = _positive_decimal(summary.get("qty"))
            summary_unit = str(summary.get("unit") or "").strip().lower()
            if not summary_spec or summary_qty != Decimal("1"):
                continue
            if summary_unit != detail_unit or summary_spec * summary_qty != detail_total:
                continue

            removed_indexes.add(summary_index)
            total_text = format(detail_total.normalize(), "f")
            decisions.append({
                "decision_id": f"rec_{len(decisions) + 1:03d}",
                "reason": "equivalent_spec_summary",
                "dropped_item_id": summary.get("item_id"),
                "dropped_raw_name": summary.get("raw_name"),
                "dropped_spec": str(summary.get("spec") or ""),
                "dropped_qty": str(summary.get("qty") or ""),
                "dropped_unit": summary_unit,
                "retained_item_ids": [detail.get("item_id")]
                if detail.get("item_id") else [],
                "retained_raw_name": detail.get("raw_name"),
                "retained_spec": str(detail.get("spec") or ""),
                "retained_qty": str(detail.get("qty") or ""),
                "retained_unit": detail_unit,
                "calculated_total": total_text,
                "combination_ids": list(detail.get("combination_ids") or []),
                "gift_context": _gift_context_key(detail),
            })
            print(
                "[n3 DEBUG] 删除等总量赠品汇总: "
                f"{summary.get('raw_name')!r} {summary.get('spec')}{summary_unit}×1；"
                f"保留明确明细 {detail.get('raw_name')!r} "
                f"{detail.get('spec')}{detail_unit}×{detail.get('qty')}；"
                f"总量={total_text}{detail_unit}"
            )

    retained = [
        item for index, item in enumerate(items) if index not in removed_indexes
    ]
    if return_decisions:
        return retained, decisions
    return retained


def get_node(concurrency: int = None):
    # 用途:返回 LangGraph 节点函数(遗留节点包装)。
    # 要点:concurrency 参数仅为兼容旧接口,实际同步执行。
    """返回 LangGraph 节点函数（同步）。

    n3 负责: 归一化名称 + 规格/单位/数量分离 + 赠品明细拆条。
    """
    def normalize_node(state: dict) -> dict:
        # 用途:节点主体——读入 raw/filtered_items,逐条归一化并拆赠品明细。
        # 要点:赠品 spec_text 含"+"时拆成多条子条目(清空 qwen_matched_name 防串名),
        #       最后统一调 remove_equivalent_gift_summaries 删等总量汇总行。
        items = state.get("filtered_items") or state.get("raw_items") or []

        # 对每个 VLM 产品条目做归一化
        results = []
        for item in items:
            norm = normalize_item(item)

            # 如果该产品是赠品且有 gift_label，尝试从 spec_text 拆条
            # （多赠品的明细文本拆成多条独立条目）
            # spec_text 如 "黑绷带面霜5ml×5 + 白绷带面霜5ml×1(会员直播加赠)"
            spec = item.get("spec_text") or ""
            if item.get("role") == "赠品" and "+" in spec:
                sub_items = split_gift_detail(spec)
                if len(sub_items) > 1:
                    # 拆出多条赠品子条目
                    for si in sub_items:
                        merged = dict(item)
                        merged["raw_name"] = si.get("raw_name", merged.get("raw_name", ""))
                        # 父条目的Qwen初步真名不能安全复用给多个拆分子商品。
                        merged["qwen_matched_name"] = None
                        # split_gift_detail 返回的 dict 总有 spec/unit 键（可能是 None），
                        # 用 or 而非 dict.get 默认值，确保 None 时回退到 norm 的解析结果
                        merged["spec"] = si.get("spec") or norm.get("spec")
                        merged["unit"] = si.get("unit") or norm.get("unit")
                        merged["qty"] = si.get("qty") or "1"
                        merged["gift_label"] = si.get("gift_label",
                                                     item.get("gift_label", "赠品"))
                        results.append(merged)
                    continue  # 已拆，跳过原条目

            results.append(norm)

        return {"normalized_items": remove_equivalent_gift_summaries(results)}
    return normalize_node


# LangGraph 默认节点可调用对象
node = get_node()
