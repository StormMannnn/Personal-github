"""测试 n6_price 模块: 价格角色判断 + 优惠券清洗。"""

import sys
sys.path.insert(0, "/Users/stormman/Documents/coding/PPI项目")

from ppi_loop.nodes import n6_price


def test_is_final_price_clear():
    # 六类到手价关键词
    for ctx in [
        "预估低至¥299", "成交价¥299", "券后价¥299",
        "实付价¥299", "现价¥299", "售价¥299",
    ]:
        assert n6_price.is_final_price(ctx), f"应识别为到手价: {ctx!r}"
    assert n6_price.is_final_price("到手价299")
    assert n6_price.is_final_price("立减后到手价¥599")


def test_is_final_price_containing_text():
    """包含型文本：含关键词即可识别为到手价"""
    for ctx in [
        "活动预估低至¥299", "本商品成交价¥299", "会员券后价¥299",
    ]:
        assert n6_price.is_final_price(ctx), f"应识别为到手价: {ctx!r}"


def test_is_not_final_price():
    assert not n6_price.is_final_price("立减30")
    assert not n6_price.is_final_price("立减¥300到手")
    assert not n6_price.is_final_price("百亿补贴直降¥368到手")
    assert not n6_price.is_final_price("至高叠减600")
    assert not n6_price.is_final_price("至高减¥200")
    assert not n6_price.is_final_price("满1500减200")
    assert not n6_price.is_final_price("原价¥3680")


def test_is_final_price_ambiguous():
    """模糊/冲突 → 不是到手价（宁缺勿错）"""
    assert not n6_price.is_final_price("¥299")  # 纯金额，无语义


def test_extract_coupon_amount():
    assert n6_price.extract_coupon_amount("满1500减200") == "200"
    assert n6_price.extract_coupon_amount("¥400") == "400"
    assert n6_price.extract_coupon_amount("立减30") == "30"
    assert n6_price.extract_coupon_amount("会员9折券") is None
    assert n6_price.extract_coupon_amount("下拉领88VIP消费券 25 满200可用") == "25"
    assert n6_price.extract_coupon_amount("直播间限时闪降99") == "99"


def test_extract_coupon_discount():
    assert n6_price.extract_coupon_discount("会员9折券") == "9"
    assert n6_price.extract_coupon_discount("全场8.5折") == "8.5"
    assert n6_price.extract_coupon_discount("会员9.0折") == "9"
    assert n6_price.extract_coupon_discount("进直播间下单享85折") == "8.5"
    assert n6_price.extract_coupon_discount("会员95折") == "9.5"
    assert n6_price.extract_coupon_discount("3期免息") is None
    assert n6_price.extract_coupon_discount("全场0折") is None
    assert n6_price.extract_coupon_discount("全场10折") is None


def test_clean_coupon_name():
    assert "美妆品类券" in n6_price.clean_coupon_name(
        "下滑领券 | 美妆品类券 ¥200"
    )
    assert "PLUS优惠券" in n6_price.clean_coupon_name(
        "PLUS优惠券 ¥400"
    )
    assert n6_price.clean_coupon_name("下拉领88VIP消费券 25 满200可用") == "88VIP消费券"


def test_clean_coupon_condition():
    assert n6_price.clean_coupon_condition("满¥1500使用") == "满1500"
    assert "实付满3000" in n6_price.clean_coupon_condition("实付满¥3000使用")


def test_is_summary_coupon():
    assert n6_price.is_summary_coupon("至高叠减600")
    assert n6_price.is_summary_coupon("凑单至高叠减600元")
    assert n6_price.is_summary_coupon("至高减¥200")
    assert n6_price.is_summary_coupon("券 满额至高减¥253")
    assert n6_price.is_summary_coupon("券下单！至高减¥460")
    assert n6_price.is_summary_coupon("全场至高减200元")
    assert not n6_price.is_summary_coupon("满1500减200美妆品类券")


def test_extract_final_prices_empty():
    prices = [{"amount": "200", "context_text": "满1500减200"}]
    result = n6_price.extract_final_prices(prices)
    assert result == []  # 优惠金额，不应为到手价


def test_extract_final_prices_found():
    prices = [{"amount": "599", "context_text": "到手价¥599"}]
    result = n6_price.extract_final_prices(prices)
    assert len(result) == 1
    assert result[0]["金额"] == "599"


def test_extract_final_prices_yugudizhi():
    """"预估低至¥299" → 到手价 299"""
    prices = [{"amount": "299", "context_text": "预估低至¥299"}]
    result = n6_price.extract_final_prices(prices)
    assert len(result) == 1, f"应识别为到手价,实际 {result}"
    assert result[0] == {"类型": "到手价", "金额": "299"}


def test_extract_final_prices_min():
    """多候选取最低"""
    prices = [
        {"amount": "599", "context_text": "售价¥599"},
        {"amount": "499", "context_text": "券后价¥499"},
    ]
    result = n6_price.extract_final_prices(prices)
    assert len(result) == 1
    assert result[0]["金额"] == "499"


def test_extract_coupons():
    raw = [
        {"text": "美妆品类券 ¥200 满1500使用"},
        {"text": "至高叠减600"},  # 汇总金额,应被过滤
        {"text": "无门槛通用券"},  # v9: 无明确金额 → 整条不输出
        {"text": "购物金充200送20"},  # v9: 购物金 → 不输出
    ]
    results = n6_price.extract_coupons(raw)
    assert len(results) == 1, f"应只保留1条有明确金额的券,实际 {len(results)}"
    assert results[0]["金额"] == "200"


# ===== 使用条件提取（支持 ¥/￥ / 空格 / 实满付 变体） =====

def test_condition_man_yuan():
    cond = n6_price.extract_coupon_condition_text("美妆品类券 ¥200 + 满¥1500使用")
    assert cond == "满1500", f"期望满1500,实际 {cond!r}"


def test_condition_shifu_yuan():
    cond = n6_price.extract_coupon_condition_text("PLUS优惠券 ¥400 实付满￥3000使用")
    assert cond == "实付满3000", f"期望实付满3000,实际 {cond!r}"


def test_condition_shi_man_fu():
    """'实满付'(词序颠倒) → '实付满3000'"""
    cond = n6_price.extract_coupon_condition_text("88VIP消费券 ¥400 实满付¥3000使用")
    assert cond == "实付满3000", f"期望实付满3000,实际 {cond!r}"


def test_condition_every_man_space():
    cond = n6_price.extract_coupon_condition_text("每 满 1000 减 100")
    assert cond == "每满1000", f"期望每满1000,实际 {cond!r}"


def test_condition_man_space():
    cond = n6_price.extract_coupon_condition_text("美妆品类券 满 1500 使用")
    assert cond == "满1500", f"期望满1500,实际 {cond!r}"


def test_condition_no_threshold():
    cond = n6_price.extract_coupon_condition_text("百亿补贴 ¥368")
    assert cond is None


def test_condition_clean_pure():
    assert n6_price.clean_coupon_condition("满1500") == "满1500"
    assert n6_price.clean_coupon_condition("实付满3000") == "实付满3000"


def test_condition_man_jian_legacy():
    cond = n6_price.extract_coupon_condition_text("美妆品类券 ¥200 满1500减200")
    assert cond == "满1500"
    cond = n6_price.extract_coupon_condition_text("PLUS优惠券 实付满3000减¥400")
    assert cond == "实付满3000"
    cond = n6_price.extract_coupon_condition_text("美妆品类券 ¥200 满1500使用")
    assert cond == "满1500"


# ===== 优惠券名称清洗 =====

def test_clean_name_man_yuan():
    assert n6_price.clean_coupon_name("美妆品类券 ¥200 + 满¥1500使用") == "美妆品类券"


def test_clean_name_shifu_yuan():
    assert n6_price.clean_coupon_name("PLUS优惠券 ¥400 实付满￥3000使用") == "PLUS优惠券"


def test_clean_name_shi_man_fu():
    assert n6_price.clean_coupon_name("88VIP消费券 ¥400 实满付¥3000使用") == "88VIP消费券"


def test_clean_name_baibutie():
    assert n6_price.clean_coupon_name("百亿补贴 ¥368") == "百亿补贴"


# ===== 新增: 金额残留清洗（元/到手/领） =====

def test_clean_name_yuan_attached():
    """'新会员专享券 ¥10元 满200使用' → '新会员专享券'"""
    assert n6_price.clean_coupon_name("新会员专享券 ¥10元 满200使用") == "新会员专享券"


def test_clean_name_yuan_spaced():
    """'会员专享券 ￥ 100 元 满700使用' → '会员专享券'"""
    assert n6_price.clean_coupon_name("会员专享券 ￥ 100 元 满700使用") == "会员专享券"


def test_clean_name_yuan_no_symbol():
    """'新会员券 10元 满200使用' → '新会员券'"""
    assert n6_price.clean_coupon_name("新会员券 10元 满200使用") == "新会员券"


def test_clean_name_daoshou():
    """'立减¥300到手' → '立减'"""
    assert n6_price.clean_coupon_name("立减¥300到手") == "立减"


def test_clean_name_ling_prefix():
    """'领会员券 ¥80' → '会员券'"""
    assert n6_price.clean_coupon_name("领会员券 ¥80") == "会员券"


def test_clean_name_ling_prefix_350():
    """'领商品券 ¥350' → '商品券'"""
    assert n6_price.clean_coupon_name("领商品券 ¥350") == "商品券"


def test_clean_name_yuanqi_preserved():
    """'元气会员券 ¥10' → '元气会员券'，不得误删名称中的'元'"""
    assert n6_price.clean_coupon_name("元气会员券 ¥10") == "元气会员券"


def test_clean_name_idempotent():
    """重复清洗结果不再变化"""
    for raw in [
        "新会员专享券 ¥10元 满200使用",
        "会员专享券 ￥ 100 元 满700使用",
        "新会员券 10元 满200使用",
        "立减¥300到手",
        "领会员券 ¥80",
        "领商品券 ¥350",
    ]:
        first = n6_price.clean_coupon_name(raw)
        second = n6_price.clean_coupon_name(first)
        assert first == second, f"非幂等: '{raw}' → '{first}' → '{second}'"


# ===== 直播间下单 + 限时立减清洗 =====

def test_clean_live_time_limited():
    raw = "直播间下单 ¥100 限时立减"
    assert n6_price.clean_coupon_name(raw) == "立减"
    assert n6_price.extract_coupon_condition_text(raw) is None


def test_extract_coupons_live_time_limited():
    raw = [{"text": "直播间下单 ¥100 限时立减"}]
    result = n6_price.extract_coupons(raw)
    assert result == [{
        "类型": "优惠券",
        "名称": "立减",
        "金额": "100",
        "使用条件": "无",
    }], f"实际: {result}"


def test_condition_regression():
    """真实门槛条件仍能正确提取"""
    assert n6_price.extract_coupon_condition_text("美妆加补券 ¥93 满¥1500使用") == "满1500"
    assert n6_price.extract_coupon_condition_text("指定商品券 ¥50 限指定商品") == "限指定商品"
    assert n6_price.extract_coupon_condition_text("百亿补贴 ¥368") is None


# ===== 集成 =====

def test_extract_coupons_integration():
    """集成: 金额 + 使用条件 + 名称清洗 + 数量"""
    raw = [
        {"text": "美妆品类券 ¥200 + 满¥1500使用"},
        {"text": "PLUS优惠券 ¥400 实付满￥3000使用"},
        {"text": "百亿补贴 ¥368"},
    ]
    results = n6_price.extract_coupons(raw)
    assert len(results) == 3, f"应输出3条券,实际 {len(results)}"
    assert results[0]["名称"] == "美妆品类券"
    assert results[0]["金额"] == "200"
    assert results[0]["使用条件"] == "满1500"
    assert results[1]["名称"] == "PLUS优惠券"
    assert results[1]["金额"] == "400"
    assert results[1]["使用条件"] == "实付满3000"
    assert results[2]["名称"] == "补贴"
    assert results[2]["金额"] == "368"
    assert results[2]["使用条件"] == "无"


def test_extract_coupons_filters_summary():
    """'至高减'作为汇总金额被过滤，真实券保留。"""
    raw = [
        {"text": "至高减¥200"},
        {"text": "券 满额至高减¥253"},
        {"text": "券下单！至高减¥460"},
        {"text": "美妆品类券 ¥80 满600使用"},
        {"text": "PLUS专属券 ¥60 满500使用"},
    ]
    results = n6_price.extract_coupons(raw)
    assert len(results) == 2, f"应仅保留2条真实券,实际 {len(results)}"
    names = [r["名称"] for r in results]
    assert all("至高" not in n for n in names), f"汇总券不应出现,实际 {names}"


def test_no_amount_no_output():
    """v9: 金额不可得 → 整条不输出,禁止'金额:无'"""
    raw = [{"text": "全场通用券"}]
    results = n6_price.extract_coupons(raw)
    assert len(results) == 0, f"无金额券应不输出,实际 {len(results)}"


def test_extract_discount_coupons():
    raw = [
        {"text": "会员9折券"},
        {"text": "全场8.50折优惠"},
        {"text": "进直播间下单享85折"},
        {"text": "3期免息"},
    ]
    results = n6_price.extract_coupons(raw)
    assert results == [
        {"类型": "优惠券", "名称": "优惠券", "金额": "9", "使用条件": "9折"},
        {"类型": "优惠券", "名称": "优惠券", "金额": "8.5", "使用条件": "8.5折"},
    ]


def test_extract_bare_amount_coupon():
    results = n6_price.extract_coupons([
        {"text": "下拉领88VIP消费券 25 满200可用"},
    ])
    assert results == [{
        "类型": "优惠券", "名称": "88VIP消费券", "金额": "25", "使用条件": "满200",
    }]


def test_extract_bare_amount_coupon_with_ocr_star():
    results = n6_price.extract_coupons([
        {"text": "下拉领88VIP消费券 *60 满480可用"},
    ])
    assert results == [{
        "类型": "优惠券", "名称": "88VIP消费券", "金额": "60", "使用条件": "满480",
    }]


def test_collect_coupon_candidates_from_strong_price_semantics():
    candidates = n6_price.collect_coupon_candidates([], [
        {"amount": "99", "context_text": "直播间限时闪降 ¥99 以实际下单为准"},
        {"amount": "184", "context_text": "进直播间下单 领券至高叠减¥184"},
        {"amount": "599", "context_text": "到手价¥599"},
        {"amount": "599", "context_text": "官方补贴后到手价¥599"},
        {"amount": "999", "context_text": "官方补贴商品原价¥999"},
    ])
    assert candidates == [{"text": "直播间限时闪降 ¥99 以实际下单为准"}]


def test_process_prices_recovers_flash_discount_without_duplicates():
    raw_text = "直播间限时闪降 ¥99 以实际下单为准"
    result = n6_price.process_prices({
        "raw_prices": [{"amount": "99", "context_text": raw_text}],
        "raw_coupons": [{"text": raw_text}],
    })
    assert result["price_objects"] == []
    assert len(result["coupon_objects"]) == 1
    assert result["coupon_objects"][0]["名称"] == "闪降"
    assert result["coupon_objects"][0]["金额"] == "99"
    assert result["coupon_objects"][0]["使用条件"] == "无"


def test_unthresholded_discount_name_uses_first_keyword():
    results = n6_price.extract_coupons([
        {"text": "购买立减 ¥368 百亿补贴"},
        {"text": "官方限时至高补贴 ¥100"},
        {"text": "会员满减券 ¥100 满500可用"},
    ])
    assert [item["名称"] for item in results] == ["立减", "补贴", "会员满减券"]
    assert [item["使用条件"] for item in results] == ["无", "无", "满500"]


def test_shopping_credit_filtered():
    """v9 约束4: 购物金直接丢弃"""
    raw = [{"text": "购物金充值500送100"}]
    results = n6_price.extract_coupons(raw)
    assert len(results) == 0


# ===== 优惠券去重 =====

def test_dedupe_same_amount_and_condition():
    """金额和条件相同但名称不同 → 两条都保留。"""
    raw = [
        {"text": "领券减¥100"},
        {"text": "会员专属券 ¥100"},
    ]
    results = n6_price.extract_coupons(raw)
    assert len(results) == 2, f"名称不同应保留2条,实际 {len(results)}"
    assert [item["名称"] for item in results] == ["券减", "会员专属券"]


def test_dedupe_different_condition():
    """金额相同但条件不同 → 保留两条"""
    raw = [
        {"text": "会员券 ¥100"},
        {"text": "美妆品类券 ¥100 满1500使用"},
    ]
    results = n6_price.extract_coupons(raw)
    assert len(results) == 2, f"条件不同应保留2条,实际 {len(results)}"


def test_dedupe_different_amount():
    """金额不同但条件相同 → 保留两条"""
    raw = [
        {"text": "会员券 ¥100"},
        {"text": "会员券 ¥200"},
    ]
    results = n6_price.extract_coupons(raw)
    assert len(results) == 2, f"金额不同应保留2条,实际 {len(results)}"


def test_dedupe_same_all():
    """金额、条件、名称完全相同 → 保留一条"""
    raw = [
        {"text": "会员专属券 ¥100"},
        {"text": "会员专属券 ¥100"},
    ]
    results = n6_price.extract_coupons(raw)
    assert len(results) == 1


def test_dedupe_same_name_and_amount_different_condition():
    raw = [
        {"text": "会员券 ¥100"},
        {"text": "会员券 ¥100 满1500使用"},
    ]
    results = n6_price.extract_coupons(raw)
    assert len(results) == 2


def test_discount_does_not_conflict_with_currency_price():
    final_prices = [{"类型": "到手价", "金额": "9"}]
    coupons = [{"类型": "优惠券", "名称": "优惠券", "金额": "9", "使用条件": "9折"}]
    n6_price.validate_exclusivity(final_prices, coupons)
    assert final_prices == [{"类型": "到手价", "金额": "9"}]


def test_currency_coupon_still_conflicts_with_same_price():
    final_prices = [{"类型": "到手价", "金额": "100"}]
    coupons = [{"类型": "优惠券", "名称": "会员券", "金额": "100", "使用条件": "无"}]
    n6_price.validate_exclusivity(final_prices, coupons)
    assert final_prices == []


if __name__ == "__main__":
    test_is_final_price_clear()
    test_is_final_price_containing_text()
    test_is_not_final_price()
    test_is_final_price_ambiguous()
    test_extract_coupon_amount()
    test_extract_coupon_discount()
    test_clean_coupon_name()
    test_clean_coupon_condition()
    test_is_summary_coupon()
    test_extract_final_prices_empty()
    test_extract_final_prices_found()
    test_extract_final_prices_yugudizhi()
    test_extract_final_prices_min()
    test_extract_coupons()
    test_extract_coupons_filters_summary()
    test_no_amount_no_output()
    test_extract_discount_coupons()
    test_extract_bare_amount_coupon()
    test_collect_coupon_candidates_from_strong_price_semantics()
    test_process_prices_recovers_flash_discount_without_duplicates()
    test_unthresholded_discount_name_uses_first_keyword()
    test_shopping_credit_filtered()
    test_dedupe_same_amount_and_condition()
    test_dedupe_different_condition()
    test_dedupe_different_amount()
    test_dedupe_same_all()
    test_dedupe_same_name_and_amount_different_condition()
    test_discount_does_not_conflict_with_currency_price()
    test_currency_coupon_still_conflicts_with_same_price()
    test_condition_man_yuan()
    test_condition_shifu_yuan()
    test_condition_shi_man_fu()
    test_condition_every_man_space()
    test_condition_man_space()
    test_condition_no_threshold()
    test_condition_clean_pure()
    test_condition_man_jian_legacy()
    test_clean_name_man_yuan()
    test_clean_name_shifu_yuan()
    test_clean_name_shi_man_fu()
    test_clean_name_baibutie()
    test_clean_name_yuan_attached()
    test_clean_name_yuan_spaced()
    test_clean_name_yuan_no_symbol()
    test_clean_name_daoshou()
    test_clean_name_ling_prefix()
    test_clean_name_ling_prefix_350()
    test_clean_name_yuanqi_preserved()
    test_clean_name_idempotent()
    test_clean_live_time_limited()
    test_extract_coupons_live_time_limited()
    test_condition_regression()
    test_extract_coupons_integration()
    print("n6_price(v9): 全部测试通过 ✓")
