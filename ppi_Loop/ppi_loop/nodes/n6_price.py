"""n6_price — 价格角色判断 + 优惠券清洗。

对应 prompt:
- Step3.6: 到手价角色判断（到手价 vs 优惠券互斥、多候选取最低）
- Step3.7: 优惠券字段清洗（名称、金额、使用条件、汇总金额排除）
"""

import re
from typing import Any

from .combination_utils import expand_by_combination, normalize_sales_combinations

# ==================== 价格角色判断关键词 ====================

# 到手价语义关键词（该金额是"实际支付价格"）
FINAL_PRICE_KEYWORDS = [
    "到手价", "成交价", "券后价", "实付价", "现价", "售价",
    "价格", "到手", "券后", "预估低至",
]

# 优惠金额语义关键词（该金额是"减少的金额"，非到手价）
DISCOUNT_KEYWORDS = [
    "优惠", "减免", "降价", "补贴", "加补", "抵扣", "折扣",
    "券", "红包", "礼金", "直降", "立减", "减",
]

# 明确优惠语义：当文案只有孤立的“到手/券后”等弱到手词时，优惠语义优先。
EXPLICIT_DISCOUNT_KEYWORDS = [
    "闪降", "立减", "直降", "直减", "减免", "补贴", "加补", "抵扣", "满减",
]

# 明确到手价语义：出现这些完整词时，仍按到手价处理。
EXPLICIT_FINAL_PRICE_KEYWORDS = [
    "到手价", "成交价", "券后价", "实付价", "现价", "售价", "预估低至",
]

# 禁止作为到手价的语义
NOT_FINAL_PRICE_KEYWORDS = [
    "原价", "划线价", "rsp", "赠品价值", "正装量", "使用门槛",
    "至高叠减", "至高省", "最高优惠", "凑单", "至高减",
]


def is_final_price(context_text: str) -> bool:
    # 用途:判断一段金额文案是否为"到手价"(实际支付价格)。
    # 要点:先排除原价/划线价等;明确优惠词(立减/直降)优先于孤立"到手/券后",
    #       但出现完整到手价词(券后价/成交价等)时仍按到手价处理。
    """判断一个金额是否为到手价（实际支付价格）。

    关键: 明确到手价词优先；明确优惠词优先于孤立的“到手/券后”等弱到手词。
    """
    ctx = context_text.lower()

    # 排除: 明确不是到手价（原价/划线价/汇总金额等）
    for kw in NOT_FINAL_PRICE_KEYWORDS:
        if kw.lower() in ctx:
            return False

    is_final = any(kw.lower() in ctx for kw in FINAL_PRICE_KEYWORDS)
    is_discount = any(kw.lower() in ctx for kw in DISCOUNT_KEYWORDS)
    is_explicit_final = any(kw.lower() in ctx for kw in EXPLICIT_FINAL_PRICE_KEYWORDS)
    is_explicit_discount = any(kw.lower() in ctx for kw in EXPLICIT_DISCOUNT_KEYWORDS)

    # “立减¥300到手”中的“到手”不是300元到手价，明确优惠语义优先。
    # 若文案明确写有“到手价/券后价”等完整词，则仍按到手价处理。
    if is_explicit_discount and not is_explicit_final:
        return False

    # 到手价关键词优先: '券后价'里虽然含'券',但整体是最终价
    if is_final:
        return True
    # 仅有优惠语义,无到手语义
    if is_discount:
        return False
    # 都不明确 → 不是到手价
    return False


def extract_final_prices(
    prices: list[dict[str, Any]],
    sales_combinations: list[dict] | None = None,
) -> list[dict[str, Any]]:
    # 用途:从 VLM 价格列表筛出到手价条目。
    # 要点:仅接受 is_final_price 判为 True 的候选,并按组合各自取最低金额;
    #       sales_combinations 非 None 时输出带"组合ID"。
    """从 VLM 价格列表提取到手价条目。

    规则: 多候选取最低（仅限已明确判定为到手价的）
    """
    include_combination = sales_combinations is not None
    final_candidates: dict[str, list[dict[str, Any]]] = {}
    for p in prices:
        ctx = p.get("context_text", "")
        if is_final_price(ctx):
            for expanded in expand_by_combination(p, sales_combinations):
                final_candidates.setdefault(expanded["combination_id"], []).append(expanded)

    if not final_candidates:
        return []

    # 每个组合独立取最低到手价，防止S1/S2互相抢最低价。
    results = []
    for combo_id, candidates in final_candidates.items():
        min_amount = str(min(int(p["amount"]) for p in candidates))
        result = {"类型": "到手价", "金额": min_amount}
        if include_combination:
            result["组合ID"] = combo_id
        results.append(result)
    return results


# ==================== 优惠券清洗 ====================

# 领取动作/引导词（需从名称中删除）
ACTION_WORDS_RE = re.compile(
    r"(直播间下单|限时|点击|下拉|进入|领取|去领|立即领取|下滑|查看|了解|去使用)",
    re.IGNORECASE,
)

# 使用条件无意义动作词（需删除）
CONDITION_NOISE_RE = re.compile(
    r"(使用|可用|适用|专用|可使用)\s*",
    re.IGNORECASE,
)

# 仅允许从“券名 + 裸金额 + 满额门槛”结构中读取无货币符号的优惠金额。
# 例如“88VIP消费券 25 满200可用”提取25，不能误取88或200。
BARE_COUPON_AMOUNT_RE = re.compile(
    r"((?:优惠券|消费券|品类券|会员券|单品券|礼金|券))"
    r"\s*[：:]?\s*[*＊]?\s*(\d+(?:\.\d+)?)\s*"
    r"(?=(?:实(?:付\s*满|满\s*付)|每\s*满|满))"
)

# 从券文案中提取金额正则可匹配的模式:
# - "满500减100" → 金额=100
# - "¥200" → 金额=200
# - "立减30" → 金额=30
# - "折扣类无法换算" → None
COUPON_AMOUNT_PATTERNS = [
    # 裸金额券: "88VIP消费券 25 满200"
    (BARE_COUPON_AMOUNT_RE, 2),
    # 满减: "满500减100"
    (re.compile(r"满\d+[减抵扣]\s*(\d+)"), 1),
    # 立减: "立减30"
    (re.compile(r"立减\s*(\d+)"), 1),
    # 直减: "直减50"
    (re.compile(r"直减\s*(\d+)"), 1),
    (re.compile(r"直降\s*[¥￥]?\s*(\d+)"), 1),
    (re.compile(r"闪降\s*[¥￥]?\s*(\d+)"), 1),
    (re.compile(r"(?:补贴|加补|抵扣)\s*[¥￥]?\s*(\d+)"), 1),
    # 金额: "¥200" / "200元"
    (re.compile(r"[¥￥]\s*(\d+)"), 1),
    (re.compile(r"(\d+)\s*元"), 1),
    # 折扣: "8折" → 无法换算金额
]


def extract_coupon_amount(text: str) -> str | None:
    # 用途:从券文案提取优惠金额数字(如"满1500减200"→"200")。
    # 要点:纯折扣(如"8折"且无减/元/货币符号)无法换算金额,直接返回 None。
    """从优惠券文案提取金额数字。

    >>> extract_coupon_amount("满1500减200")
    '200'
    >>> extract_coupon_amount("¥400")
    '400'
    """
    # 如果是折扣类（无法换算金额）
    if re.search(r"\d+折", text) and not any(kw in text.lower() for kw in ["减", "¥", "￥", "元"]):
        return None

    for pattern, group_idx in COUPON_AMOUNT_PATTERNS:
        m = pattern.search(text)
        if m:
            return m.group(group_idx)
    return None


def extract_coupon_discount(text: str) -> str | None:
    # 用途:提取折扣数字,支持"9折/8.5折/85折"。
    # 要点:两位数按百分比口径归一(85折→8.5),"10折"及越界值视为无效返回 None。
    """提取明确的折扣数字，支持 ``9折``、``8.5折``、``85折``。

    两位整数写法按百分比口径归一，如 ``85折`` → ``8.5``。
    ``10折``不视为有效优惠。
    """
    match = re.search(r"(?<![\d.])(\d{1,2}(?:\.\d+)?)\s*折", text)
    if not match:
        return None

    value = float(match.group(1))
    if value == 10 or not 0 < value < 100:
        return None
    normalized = value / 10 if value > 10 else value
    if not 0 < normalized < 10:
        return None
    if normalized.is_integer():
        return str(int(normalized))
    return f"{normalized:.10f}".rstrip("0").rstrip(".")


def clean_coupon_name(text: str) -> str:
    # 用途:清洗券名——去掉领取动作词、门槛片段、金额、尾部"到手"等噪音。
    # 要点:按固定顺序清理,裸数字不删(保护"88VIP"类名称),清空后兜底返回"无"。
    """清洗优惠券名称: 删除领取动作词、门槛信息、金额部分。

    清洗顺序:
      领取引导 → 完整门槛 → 完整金额 → 尾部「到手」/开头「领」
      → 孤立单位和连接符 → 空格归一化

    >>> clean_coupon_name("下滑领券 | 美妆品类券 ¥200")
    '美妆品类券'
    >>> clean_coupon_name("新会员专享券 ¥10元 满200使用")
    '新会员专享券'
    >>> clean_coupon_name("立减¥300到手")
    '立减'
    >>> clean_coupon_name("领会员券 ¥80")
    '会员券'
    >>> clean_coupon_name("百亿补贴 ¥368")
    '百亿补贴'
    """
    # 1) 管道符引导 + 领取动作词
    text = re.sub(r"^.*?\|\s*", "", text)
    text = ACTION_WORDS_RE.sub("", text)

    # 安全删除券名之后、满额门槛之前的裸优惠金额，保留“88VIP”中的数字。
    text = BARE_COUPON_AMOUNT_RE.sub(lambda m: m.group(1), text)

    # 2) 完整门槛片段（先于金额删除，防止 ¥ 符号被清后残留裸"满"）
    THRESHOLD_PATTERNS = [
        re.compile(r"\s*[+\-]?\s*实(?:付\s*满|满\s*付)\s*[¥￥]?\s*\d+\s*(?:[减抵扣]\s*[¥￥]?\s*\d+)?\s*(?:使用|可用|适用)?"),
        re.compile(r"\s*[+\-]?\s*每\s*满\s*[¥￥]?\s*\d+\s*(?:[减抵扣]\s*[¥￥]?\s*\d+)?\s*(?:使用|可用|适用)?"),
        re.compile(r"\s*[+\-]?\s*满\s*[¥￥]?\s*\d+\s*(?:[减抵扣]\s*[¥￥]?\s*\d+)?\s*(?:使用|可用|适用)?"),
    ]
    for pat in THRESHOLD_PATTERNS:
        text = pat.sub("", text)

    # 3) 完整金额表达式：
    #    - ¥200 / ¥ 200 / ￥200       货币符号 + 数字
    #    - ¥200元 / 200元 / 10 元      数字 + 元
    #    裸数字不删（避免误删 88VIP 等名称中的数字）
    AMOUNT_RE = re.compile(
        r"[¥￥]\s*\d+(?:\.\d+)?\s*元?|"     # ¥200 或 ¥200元
        r"\d+(?:\.\d+)?\s*元"               # 200元（数字后必须紧接"元"）
    )
    text = AMOUNT_RE.sub("", text)

    # 4) 尾部「到手」（仅在名称最末尾才删除）
    text = re.sub(r"\s*到手\s*$", "", text)

    # 5) 开头单独「领」（领取动作，不在名称中间删）
    text = re.sub(r"^领\s*", "", text)

    # 6) 清理金额被删除后残留的孤立"元"（不以汉字/字母为左邻，且后面不紧跟券/气 等合法名称用字）
    text = re.sub(r"\b元\s+(?!券|气|满)", "", text)
    text = re.sub(r"\s*元\s*$", "", text)

    # 7) 孤立门槛前缀/连接符/噪音词
    ORPHAN_RE = re.compile(
        r"\s*[+\-]?\s*(?:实(?:付\s*满|满\s*付)|每\s*满|满)\s*$|"
        r"^\s*(?:实(?:付\s*满|满\s*付)|每\s*满|满)\s*"
    )
    text = ORPHAN_RE.sub("", text)
    text = CONDITION_NOISE_RE.sub("", text)

    # 8) 收尾：合并空格，去首尾，清理遗留连接符
    text = re.sub(r"\s*[+\-]\s*$", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text if text else "无"


def clean_coupon_condition(text: str) -> str:
    # 用途:清洗券使用条件,保留纯门槛文本(如"满1500"/"实付满3000")。
    # 要点:删除货币符号与"减/抵/优惠/使用"等残留词,清空后兜底"无"。
    """清洗优惠券使用条件。

    规则: 提取的是纯门槛文本(如"满1500"/"实付满3000"),本身已是干净的;
    若原始文本含残留的 ¥/￥/减/抵/优惠/使用 等,进一步清洗。

    >>> clean_coupon_condition("满1500")
    '满1500'
    >>> clean_coupon_condition("实付满3000")
    '实付满3000'
    """
    # 删除 ¥ 符号
    text = text.replace("¥", "").replace("￥", "")
    # 删除无意义动作/连接词
    text = CONDITION_NOISE_RE.sub("", text)
    # 删除"减/抵/优惠"等可能混入的残留
    text = re.sub(r"[减抵扣](?:\s*\d+)?", "", text)
    text = re.sub(r"优惠", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text if text else "无"


def is_summary_coupon(text: str) -> bool:
    # 用途:判断是否为优惠汇总金额(至高叠减/至高省/最高优惠等)。
    # 要点:命中汇总语义即返回 True,调用方会整条排除、不单独输出。
    """判断是否为汇总金额（需排除，不单独输出）。

    "至高叠减600"、"至高省X"、"最高优惠X"、"至高减" 等属于汇总。
    """
    summary_hints = ["至高叠减", "至高省", "最高优惠", "凑单至高", "叠加至高", "至高减"]
    low = text.lower()
    return any(h in low for h in summary_hints)


def extract_coupon_condition_text(text: str) -> str | None:
    # 用途:从券文案提取使用门槛,并重装为"实付满X/每满X/满X"纯文本。
    # 要点:按 实付满→每满→普通满 优先级匹配,均无再试"限/指定"类兜底,最后返回 None。
    """从券文案中提取使用条件部分(消费门槛/实付限制等)。

    按优先级匹配，支持 ¥/￥ 与任意空格变体，捕获数字后重新组装纯门槛文本:
    1. 实付满 → 返回 '实付满{金额}'
    2. 每满   → 返回 '每满{金额}'
    3. 普通满 → 返回 '满{金额}'

    支持: 满¥1500 / 满 1500 / 实付满￥3000 / 每 满 1000 / 满1500.00

    >>> extract_coupon_condition_text("美妆品类券 ¥200 满¥1500减¥200")
    '满1500'
    >>> extract_coupon_condition_text("PLUS优惠券 实付满￥3000使用")
    '实付满3000'
    >>> extract_coupon_condition_text("每 满 1000 减 100")
    '每满1000'
    >>> extract_coupon_condition_text("满1500使用")
    '满1500'
    >>> extract_coupon_condition_text("百亿补贴 ¥368")  # 无门槛
    """
    # 1) 实付满 / 实满付 → 按金额重新组装为 "实付满{金额}"
    m = re.search(r"实(?:付\s*满|满\s*付)\s*[¥￥]?\s*(\d+(?:\.\d+)?)", text)
    if m:
        return f"实付满{_normalize_amount(m.group(1))}"

    # 2) 每满金额
    m = re.search(r"每\s*满\s*[¥￥]?\s*(\d+(?:\.\d+)?)", text)
    if m:
        return f"每满{_normalize_amount(m.group(1))}"

    # 3) 普通满金额(负向断言避免匹配"实付满"/"每满")
    m = re.search(r"(?<!实付)(?<!每)满\s*[¥￥]?\s*(\d+(?:\.\d+)?)", text)
    if m:
        return f"满{_normalize_amount(m.group(1))}"

    # 4) 限/指定类条件兜底（排除"限时"，它是时间限定不是商品使用门槛）
    m = re.search(r"(限(?!时)[一-鿿]+)", text)
    if m:
        return m.group(1)
    m = re.search(r"(指定[一-鿿]+)", text)
    if m:
        return m.group(1)

    return None


def _normalize_amount(amount_str: str) -> str:
    # 用途:金额规范化——整数金额去掉小数点(1500.00→1500)。
    # 要点:美妆门槛金额均以整数表达;确有小数的按原样保留。
    """去掉金额中的小数点（如 1500.00 → 1500）。
    美妆促销场景中门槛金额均以整数形式表达。"""
    val = float(amount_str)
    if val == int(val):
        return str(int(val))
    return amount_str


def _build_coupon_object(text: str, combination_id: str | None = None) -> dict[str, str] | None:
    # 用途:把一条优惠原文转成标准优惠券对象(类型/名称/金额/使用条件)。
    # 要点:先试折扣再试金额,两者都取不到返回 None(整条不输出);
    #       无门槛时券名若含明确优惠词(立减等)则用该词当名称。
    """将一条优惠原文转换为标准优惠券对象。"""
    discount = extract_coupon_discount(text)
    if discount:
        result = {
            "类型": "优惠券",
            "名称": "优惠券",
            "金额": discount,
            "使用条件": f"{discount}折",
        }
        if combination_id is not None:
            result["组合ID"] = combination_id
        return result

    amount = extract_coupon_amount(text)
    if not amount:
        return None

    cond_text = extract_coupon_condition_text(text)
    condition = clean_coupon_condition(cond_text) if cond_text else "无"
    name = clean_coupon_name(text)
    if condition == "无":
        name = next((keyword for keyword in EXPLICIT_DISCOUNT_KEYWORDS if keyword in text), name)
    result = {
        "类型": "优惠券",
        "名称": name,
        "金额": amount,
        "使用条件": condition,
    }
    if combination_id is not None:
        result["组合ID"] = combination_id
    return result


def extract_coupons(
    raw_coupons: list[dict[str, Any]],
    sales_combinations: list[dict] | None = None,
) -> list[dict[str, Any]]:
    # 用途:券清洗主流程——逐条过滤并构建优惠券对象,最后按四元组去重。
    # 要点:v9 收紧:含"购物金"或汇总金额直接丢弃,金额/折扣不可得整条不输出,
    #       禁止"金额:无";空间接近/同版块不足以证明金额归属。
    """从 VLM 优惠券原文列表提取清洗后的优惠券条目。

    v9 收紧: 金额必须明确可见且归属直接,否则整个优惠券对象不输出。
    禁止输出"金额:无"。
    空间接近/同版块/"+"连接不足以证明金额归属。
    """
    results = []
    for c in raw_coupons:
        text = c.get("text", "")

        # 购物金兜底过滤(约束4): 含"购物金"文案直接丢弃
        if "购物金" in text:
            continue

        # 汇总金额不输出
        if is_summary_coupon(text):
            continue

        expanded_items = (
            expand_by_combination(c, sales_combinations)
            if sales_combinations is not None else [{"combination_id": None}]
        )
        for expanded in expanded_items:
            coupon = _build_coupon_object(text, expanded["combination_id"])

            # v9 收紧: 金额或折扣不可得 → 整条不输出,禁止"金额:无"
            if coupon is None:
                continue
            results.append(coupon)
    return deduplicate_coupons(results)


def collect_coupon_candidates(
    raw_coupons: list[dict[str, Any]],
    raw_prices: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    # 用途:合并 Qwen 券结果与价格文本中带强优惠语义的候选,供券清洗使用。
    # 要点:按(text,组合)去重,过滤汇总/到手价/原价类文本,只收含明确优惠词(立减/直降等)的。
    """合并Qwen优惠券结果与价格文本中的强优惠语义候选。"""
    candidates = [dict(c) for c in raw_coupons]
    existing_keys = {
        (str(c.get("text", "")).strip(), tuple(c.get("combination_ids") or []))
        for c in candidates
    }

    for price in raw_prices:
        text = str(price.get("context_text", "")).strip()
        combo_ids = list(price.get("combination_ids") or [])
        key = (text, tuple(combo_ids))
        if not text or key in existing_keys or is_summary_coupon(text):
            continue
        if is_final_price(text) or any(keyword in text.lower() for keyword in ["原价", "划线价", "rsp"]):
            continue
        if not any(keyword in text for keyword in EXPLICIT_DISCOUNT_KEYWORDS):
            continue
        candidate = {"text": text}
        if combo_ids:
            candidate["combination_ids"] = combo_ids
        candidates.append(candidate)
        existing_keys.add(key)

    return candidates


# ==================== 优惠券去重 ====================

def deduplicate_coupons(coupons: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # 用途:按(组合ID,名称,金额,使用条件)四元组去重,保留首次出现与原始顺序。
    """按 (名称, 金额, 使用条件) 去重。

    三个字段完全相同才去重；任一字段不同都视为不同优惠券。
    保留第一次出现的记录和原始顺序。
    """
    if not coupons:
        return []

    seen: set[tuple[str, str, str, str]] = set()
    results = []
    for c in coupons:
        key = (
            c.get("组合ID", "S1"),
            c.get("名称", ""),
            c.get("金额", ""),
            c.get("使用条件", "无"),
        )
        if key in seen:
            continue
        seen.add(key)
        results.append(c)

    return results


# ==================== 互斥校验 ====================

def validate_exclusivity(final_prices: list[dict], coupons: list[dict]) -> None:
    # 用途:Step3.6/3.7 互斥校验——同一(组合,金额)不能同时当到手价和券金额。
    # 要点:冲突时移除到手价条目(优先级:优惠券胜);任一方为空则直接返回。
    """校验到手价金额与优惠券金额不重合（Step3.6/3.7 互斥规则）。

    若同一金额同时出现，从 final_prices 中移除冲突条目（priority: 优惠券胜）。
    """
    if not final_prices or not coupons:
        return

    coupon_amounts = {
        (c.get("组合ID", "S1"), c["金额"])
        for c in coupons
        if c.get("金额")
        and c["金额"] != "无"
        and not str(c.get("使用条件", "")).endswith("折")
    }

    # 移除与优惠券金额冲突的到手价
    for fp in list(final_prices):
        if (fp.get("组合ID", "S1"), fp.get("金额")) in coupon_amounts:
            final_prices.remove(fp)


# ==================== 主节点 ====================

def process_prices(state: dict) -> dict:
    # 用途:graph 注册节点——读 raw_prices/raw_coupons,做角色判断+清洗+互斥校验。
    # 输出:{"price_objects": 到手价列表, "coupon_objects": 券列表}。
    """处理价格和优惠券: 角色判断 + 清洗 + 互斥校验。

    输入: state 含 raw_prices + raw_coupons
    输出: {"price_objects": [...], "coupon_objects": [...]}
    """
    raw_prices = state.get("raw_prices") or []
    raw_coupons = state.get("raw_coupons") or []
    sales_combinations = normalize_sales_combinations(state.get("sales_combinations"))

    final_prices = extract_final_prices(raw_prices, sales_combinations)
    coupon_candidates = collect_coupon_candidates(raw_coupons, raw_prices)
    coupons = extract_coupons(coupon_candidates, sales_combinations)

    # 互斥校验
    validate_exclusivity(final_prices, coupons)

    return {
        "price_objects": final_prices,
        "coupon_objects": coupons,
    }


def get_node():
    # 用途:返回 LangGraph 节点函数(遗留包装),实际就是 process_prices。
    """返回 LangGraph 节点函数。"""
    return process_prices


node = get_node()
