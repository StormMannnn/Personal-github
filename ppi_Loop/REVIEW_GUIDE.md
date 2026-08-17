# PPI Loop 代码 Review 指南

> 本指南用于**逐文件、逐方法、逐字段** review `ppi_loop` 包(版本 `ppi-loop-v9+`)。
> 配套:`CODE_READING_TUTORIAL.md`(按阅读顺序讲控制链)、代码内中文方法注释(本次新增)。
> 建议阅读顺序:先看本指南第 1~3 节建立全局模型,再按第 4 节逐文件对照源码,最后用第 8 节清单做 review 检查。

---

## 1. 一句话架构

`ppi_loop` 对**每张促销海报图**跑一个**有界双循环的 LangGraph 工程代理**:

```
prepare(下载+本地视觉解析)
  → extract_products(Qwen Maker 生成商品)
    → process_products(Python 过滤/归一化/词袋匹配)
      → audit_product_combinations(Kimi Checker 层1) → verify_product_combinations(Gate)
        → audit_product_coverage(Kimi 层2) → verify_product_coverage(Gate)
          → score_products(Kimi 层3) → verify_product_scores(Gate)
            → apply_product_scores → extract_benefits(Qwen Maker 生成价格/券)
              → process_benefits(Python 清洗) → audit_benefits(Kimi 权益审计) → verify_benefits(Gate)
                → assemble(组装候选) → verify_final(最终 Gate)
                    ├─ 通过 → VERIFIED(唯一进业务 Excel 的路径)
                    └─ 失败 → repair_product / repair_benefit(Qwen Patch) → 回到对应循环
                                  或 NEEDS_HUMAN / BUDGET_EXHAUSTED / INFRA_ERROR 终态
```

三大角色严格分离:

| 角色 | 实现位置 | 能做什么 | 不能做什么 |
|---|---|---|---|
| **Maker**(Qwen) | `nodes/maker.py` + `nodes/patching.py` | 生成商品/权益对象;根据 issues 提交 add/update/delete Patch | 不能评分、不能宣布验证通过、不能自己分配最终输出 |
| **Checker**(Kimi) | `nodes/checker.py` | 四层独立审计(组合/覆盖/评分/权益),只输出结构化证据与 issue | 不生成/修改业务对象,不输出其他层的字段 |
| **Gate**(Python) | `nodes/gates.py` + `product_processing.py` + `n3/n6/n7` | Schema 校验、确定性规则、路由决策、迭代计数、终态判定 | 不制造商品(Python 绝不"补商品") |

循环有界:`max_product_iterations`(默认 2)、`max_benefit_iterations`(默认 1)、`no_progress_limit`(默认 2)。

---

## 2. 终态与路由

`terminal_status` 五种终态,任何非空终态都会立即短路到 `finalize_unverified`(`graph.route_after_model` 用 truthy 检查):

| 终态 | 来源 | 业务结果 |
|---|---|---|
| `VERIFIED` | `gates.verify_final` 警告为空且 Schema 全过 | `verified_result` 进业务 Excel |
| `NEEDS_HUMAN` | 不可修复 issue / 连续无进展 / Checker 语义不稳定 / Maker 结构契约连续失败 | 空结果 + `proof_bundle` 证据包进诊断 |
| `BUDGET_EXHAUSTED` | 可修复但业务迭代次数用尽 | 同上 |
| `INVALID_INPUT` | (预留) | 同上 |
| `INFRA_ERROR` | 传输/供应商失败(401/402/超时耗尽)、图片下载失败 | 同上 |

`next_action` 是 Gate 的"意图",路由函数据此选下一条边:`coverage/score/apply_scores/assemble/repair_product/repair_benefit/verified/stop`。

---

## 3. 文件职责总表

| 文件 | 层 | 职责 |
|---|---|---|
| `main.py` | 入口 | CLI:`--source/--word-package/--output` → `run_all` → 导出 Excel |
| `runner.py` | 编排 | 图片级并发扇出 + SQLite checkpoint 断点续跑 + 诊断收集 |
| `graph.py` | 图层 | StateGraph 组装:20 节点、条件边、终态短路 |
| `state.py` | 状态 | `ImageState`(TypedDict, total=False)+ 3 个 reducer |
| `schemas.py` | 契约 | 全部 Pydantic 模型:Maker 输出、Checker 输出、Patch、最终条目 |
| `config.py` | 配置 | 全部常量与环境变量(模型/重试/并发/迭代/视觉/路径) |
| `models.py` | 端点 | AsyncOpenAI 客户端工厂 + `enable_thinking` 覆盖 |
| `common.py` | 公共 | 模型调用、契约错误分类、契约重试、多模态消息、ID 分配 |
| `nodes/maker.py` | Maker | Qwen 四个节点:商品/权益生成 + 两个修复 |
| `nodes/checker.py` | Checker | Kimi 四层审计 + 清洗 + 语义校验 + 重试 |
| `nodes/gates.py` | Gate | 五道 Gate + 无进展签名 + 终态决策 |
| `nodes/patching.py` | Gate | 事务式 Patch 应用(全量或拒绝) |
| `nodes/product_processing.py` | Gate | 商品确定性处理(过滤/归一化/词袋/评分回写) |
| `nodes/combination_utils.py` | Gate | 销售组合台账确定性校验与展开 |
| `nodes/n1_prepare.py` | 预处理 | 下载原图 + PaddleOCR-VL 视觉资产 |
| `nodes/n3_normalize.py` | 规则 | 商品归一化 + 等总量汇总删除(对账记录) |
| `nodes/n5_classify.py` | 遗留 | 主品/赠品归类(运行时不被 graph 引用,仅测试) |
| `nodes/n6_price.py` | 规则 | 价格角色判断 + 优惠券清洗 + 互斥校验 |
| `nodes/n7_assemble.py` | 规则 | 最终条目组装 + Step5 自检 + OutputItem 校验 |
| `prompts/contracts.py` | 提示词 | 全部 Maker/Checker/Patch 提示词常量(无方法) |
| `diagnostics.py` | 诊断 | state → JSONL 审计记录 + 运行汇总 |
| `compare_export.py` | 下游 | 标准列输出 + left-join 导出(部分遗留双模型对比) |
| `image_utils.py` | 工具 | 图片下载/增强/缓存/转 base64 |
| `vision/*` | 本地视觉 | PaddleOCR-VL 版面解析/OCR/ROI/预检/演示 |

---

## 4. 逐文件逐方法详解

> 每个方法旁边的"验证原理"是该方法的**底层实现机制**。代码内新增的中文注释与本节一一对应。

### 4.1 `graph.py`(图层)

| 方法 | 作用 | 验证/实现原理 |
|---|---|---|
| `initialize_loop` | 初始化全部循环控制与诊断字段 | 一次性写入 `loop_version` 及 ~30 个字段的初始值;只在全新 checkpoint 时执行 |
| `route_after_prepare` | prepare 后路由 | 没有 `local_image_path`(下载失败)→ `stop`,否则 `products` |
| `route_after_model` | Maker/修复节点后路由 | **任一非空 `terminal_status` → stop**(防空结果流转);否则走 success 分支 |
| `route_action` | 所有 Gate 后路由 | 直接返回 `next_action` 字符串,映射表在 `add_conditional_edges` 里 |
| `route_after_scores` | 评分回写后路由 | `benefits_extracted` 为真 → `assemble`;否则先去 `benefits` |
| `finalize_unverified` | 打包非 VERIFIED 终态 | 清空 result/verified_result,把候选快照/未解决 issue/迭代次数写入 `proof_bundle` |
| `build_graph` | 组装并编译图 | 商品循环(`repair_product → process_products`)与权益循环(`repair_benefit → process_benefits`)两条回边;支持外部 SQLite checkpointer |

### 4.2 `state.py`(状态)—— 见第 5.1 节字段大全

| 方法 | 作用 | 原理 |
|---|---|---|
| `merge_token_usage` | token 统计 reducer | 按模型名深合并,六项数值相加 |
| `merge_mapping` | 字典 reducer | 按 key 覆盖合并(用于 `*_by_layer` 诊断字段) |
| `ImageState` | 共享状态定义 | `total=False`;带 `Annotated` 的字段有追加/合并语义,其余字段**后写覆盖先写** |

### 4.3 `schemas.py`(契约)—— 见第 5.2 节字段大全

所有模型继承 `StrictModel`(`extra="forbid"`):**任何 Schema 外字段都直接报 ValidationError**,这是整个契约体系的第一道防线。方法级校验器:

| 方法 | 校验原理 |
|---|---|
| `RawPrice.amount_to_string` | field_validator(mode="before"):任何类型金额都先转字符串,再按 str 校验 |
| `ProductExtraction.combinations_must_be_explicit_and_contiguous` | model_validator(after):台账必须非空、ID 必须为 S1..Sn 连续(upper 后比对) |
| `ScoreBreakdown.total` | property:六分项求和,供 total 比对 |
| `ItemEvaluation.total_must_match` | model_validator(after):`vlm_evaluation_score` 必须等于六分项之和,否则 INVALID_CHECKER_SCORE |
| `PatchOperation.target_contract` | model_validator(after):add 不得自带 ID;update/delete 必须给 target_item_id |
| `OutputItem.valid_type` | 类型必须 ∈ {主品,赠品,到手价,优惠券} |
| `OutputItem.valid_combination` | 组合 ID 必须为 "UNK" 或 S+数字 |

### 4.4 `config.py`(配置)—— 见第 5.3 节字段大全

| 方法 | 作用 |
|---|---|
| `_env_bool` | 解析布尔环境变量(1/true/yes/on) |
| `_required_key` | 读取必须存在的环境变量,缺失直接抛错(启动即失败) |
| `endpoint_for` | 按模型名返回端点配置(两者都走 DashScope,key 均来自 `DASHSCOPE_API_KEY`) |
| `get_price_coupon_model` | 遗留兼容,返回权益 Maker 模型名 |

### 4.5 `models.py`(端点层)

| 方法 | 作用 | 原理 |
|---|---|---|
| `get_async_client` | 按 base_url 单例缓存 AsyncOpenAI 客户端 | 关闭 SSL 校验、`trust_env=False`(不读系统代理) |
| `get_extra_body` | 返回模型 extra_body,支持 `enable_thinking` 覆盖 | 防御性拷贝端点默认值;Checker 传 False 关闭思考,Maker 不传沿用默认 True |

### 4.6 `nodes/common.py`(模型调用公共层)

| 方法 | 作用 | 验证原理 |
|---|---|---|
| `ModelContractError` | 契约错误异常类 | 区分"供应商有响应但内容违约"与传输错误 |
| `is_contract_error` | 契约/基础设施错误分类器 | 小写后匹配 marker 表:pydantic 错误、JSON 解析错误、空正文、CHECKER_*/MODEL_CONTRACT_ERROR → 契约;402/429/503 等 → 基础设施 |
| `_merge_stats` | token 统计累加 | 三次重试的 usage 求和 |
| `_compact_contract_error` | 压缩错误文本 | 去多余空白 + 2000 字截断 + 前缀 Schema 名 |
| `_contract_retry_messages` | 构造结构错误反馈消息 | 在 base 消息后追加 user 消息,含 Schema 名与具体错误,要求只输出合法 JSON |
| `reasoning_tokens` | 提取思考 token | 兼容 completion_tokens_details 与顶层 reasoning_tokens 两种 usage 结构 |
| `call_json_model` | 单次模型调用 | `response_format=json_object`;json 解析→adapter 清洗→`model_validate`;JSON/Schema/清洗错误包装为 ModelContractError 后**立即返回**(不做传输重试);503/429/超时等做指数退避重试(MAX_RETRY) |
| `call_json_model_with_contract_retry` | Maker 契约重试包装 | 结构失败最多额外 `MODEL_CONTRACT_MAX_RETRY` 次,每次带错误反馈;**不消耗图迭代**;中途遇基础设施错误立即返回;token 全阶段累加 |
| `multimodal_messages` | 拼多模态消息 | 从 `local_image_path` 每次现转 base64(不持久化到 checkpoint);context 含 image_url/product_title/word_package/ocr_text + payload |
| `assign_ids` | 稳定分配 item_id | 保留已有合法 ID,空缺顺序填充 `prd_001/prc_001/cpn_001`,避免重复 |

### 4.7 `nodes/maker.py`(Qwen Maker)

| 方法 | 作用 | 验证原理 |
|---|---|---|
| `_contract_retry_count` | 结构重试次数统计 | `min(len(errors), MODEL_CONTRACT_MAX_RETRY)` |
| `_contract_diagnostics` | 构造两个诊断字段 | `model_contract_retry_counts` / `model_contract_errors_by_stage`(按 stage) |
| `_model_failure_update` | Maker 失败归类 | 有契约错误历史 → `NEEDS_HUMAN`;否则 `INFRA_ERROR`;均 `next_action=stop` |
| `extract_products` | 生成商品对象(图节点) | ProductExtraction 契约重试;成功后归一化组合台账、分配 prd ID、**重置全部商品侧 checker 状态**、记录 candidate_history |
| `extract_benefits` | 生成价格/券对象(图节点) | BenefitExtraction 契约重试;按已锁定台账归一化 combination_ids、分配 prc/cpn ID |
| `_repair` | 统一修复节点 | 把 `current_issues`+现有对象+repair_history 交给 Qwen 生成 MakerPatch,`apply_maker_patch` 事务式应用;**只有 Patch 生成成功才业务迭代 +1**(结构重试不消耗迭代);成功/被拒都写 repair_history |
| `repair_products` / `repair_benefits` | 图注册的两个修复节点 | 分别转 `_repair(state, "product"/"benefit")` |

### 4.8 `nodes/checker.py`(Kimi Checker,1233 行核心)

**清洗层**(校验前,只对 Checker 生效):

| 方法 | 作用 | 验证原理 |
|---|---|---|
| `_is_safe_metadata_value` | 判定"无害说明值" | 仅 str / None / 空容器算安全 |
| `_sanitize_score_totals` | 删除冗余 total/total_score | 六分项 Decimal 求和,相等才删,不等抛 CHECKER_TOTAL_MISMATCH;布尔/非数字抛 CHECKER_INVALID_TOTAL |
| `sanitize_checker_payload` | 顶层安全清洗 | ①保护字段(products/prices/coupons/coverage_checks/item_evaluations 等)出现在本层不允许位置 → CHECKER_FORBIDDEN_EXTRA;②安全元数据字段(note/summary/analysis/reasoning/...)值安全则删并记录路径,否则 CHECKER_UNSAFE_METADATA;③调用 totals 清洗。Maker 完全不接此清洗 |

**规格/对账工具**:

| 方法 | 作用 | 原理 |
|---|---|---|
| `_normalize_unit` | 单位归一化 | 毫升→ml、克→g、千克→kg、升→l |
| `_parse_largest_spec` | 从文字解析最大规格 | 正则只认 ml/g/kg/l 四种单位,取数值最大者 |
| `_item_spec_total` | 计算单条明细总量 | spec×qty(qty 取第一个数字),规格/数量/单位任一不可算返回 None |
| `_compact_text` | 文本紧凑化 | NFKC+casefold+去空白标点,用于对账匹配 |
| `_matches_reconciliation_summary` | 对账守卫 | missing 证据与 n3 已删汇总(dropped_raw_name + dropped_spec+unit 子串)匹配则判"不得再报漏品" |

**四个语义校验器**(每层输出必过的第二道防线):

| 校验器 | 校验的契约 |
|---|---|
| `_combination_audit_semantic_errors` | evidence_id 唯一;引用组合 ID 存在且不重复;purchase_option 状态机(covered 必须恰好 1 个组合、missing 不得带 ID、不得 ignored);非购买方案(gift/discount_summary/threshold/decorative)只能 ignored/invalid_assignment 且 ignored 不得带 ID;expected_main_spec_text 只能购买方案填;每个 Maker 组合必须至少被一个 covered/invalid_assignment 检查引用;issues 不得直接输出 4 种程序生成 code |
| `_coverage_audit_semantic_errors` | evidence_id 唯一;引用商品 ID 存在;covered/summary_only/duplicate_display 必须带 ID;missing/non_cosmetic 不得带 ID;missing 需证据且不得命中已删汇总;同一证据不得同时两个 status;**summary_only 强制同单位 spec×qty 精确加总**(Decimal);重复引用检测 |
| `_score_audit_semantic_errors` | 每个有效商品恰好一条评分(不多/不少/不重复/不引用幽灵 ID);qwen_matched_name 为空或词包外时 matched_name 必须 0/30;覆盖层报过错字段的项不得拿满分 |
| `_benefit_audit_semantic_errors` | 权益层确定性复现校验 | 先禁止越权(不得返回商品评分/覆盖);再逐条对照 raw/normalized 价格与券复现 issue:借助嵌套 `combo_ids`(raw 读 combination_ids、normalized 读"组合ID")、`matches_evidence`(item_id 命中或压缩文本双向包含)、`relevant_combo`(组合限定匹配);MISSING_COUPON 必须有非汇总的明确减免金额或 X 折证据且 raw/normalized 中确不存在;MISSING_NORMALIZED_PRICE 的证据必须有实际支付价语义且确未入账;PRICE_COUPON_CONFLICT 只能由 normalized 层同组合同金额真实冲突触发(raw 层重叠不算);NORMALIZATION_LOSS 必须有 raw 正确证据且 normalized 确实缺失;repairable=false 只允许用于程序可确定复现的归一化问题 |

**审计→issue 转换**(确定性,禁止 Kimi 直接写业务结论):

| 方法 | 作用 |
|---|---|
| `_combination_issues_from_audit` | missing 购买方案→MISSING_SALES_COMBINATION;invalid_assignment→INVALID_SALES_COMBINATION(每个错误组合一条);covered 方案无主品→MISSING_COMBINATION_MAIN;expected 规格与主品规格不符→WRONG_COMBINATION_MAIN_SPEC;按(code,组合,目标,证据)去重 |
| `_coverage_validation_details` | 生成诊断明细:summary_arithmetic(期望/实际/单位/valid)、existing_product_reference、reconciliation_guard |
| `_coverage_issues_from_audit` | 只从 status=="missing" 生成 MISSING_PRODUCT(repairable=True);section_hint 单字段问题降级为 warning |

**重试与失败路径**:

| 方法 | 作用 |
|---|---|
| `_semantic_retry_messages` | 反馈消息追加层专属引导(coverage/benefit 有额外规则提示) |
| `_run_checker_layer` | 统一层循环:契约失败/语义失败最多 CHECKER_SEMANTIC_MAX_RETRY 次反馈重试;每次被拒的审计完整快照存入 rejected_audits;最终失败返回语义错误+快照 |
| `_unstable_issue` | 生成 CHECKER_UNSTABLE(repairable=False) |
| `_layer_failure_update` | 语义不稳定路径:写 CHECKER_UNSTABLE + complete=False + 诊断(不设 terminal_status,由 Gate 转人工) |
| `_infra_failure_update` | 基础设施失败路径:terminal_status=INFRA_ERROR + stop |
| `audit_product_combinations` / `audit_product_coverage` / `score_products` / `audit_benefits` | 四个图节点:分别绑定 schema/validator/payload;成功时写各自 state 字段 |

### 4.9 `nodes/gates.py`(Gate 决策层)

| 方法 | 作用 | 验证原理 |
|---|---|---|
| `_issue` | 构造统一 issue | 缺省 evidence 为空、severity=fatal、confidence=1.0 |
| `_normalize_issue_text` | 文本归一化 | NFKC + casefold + 去空白和标点,**保留字母数字** |
| `_issue_meaning` | 提取 issue 语义指纹 | code/scope/组合/目标 + 排序去重的 affected_fields + 区域 + `evidence_anchor`(evidence_text 优先,空则 observed_fact) |
| `_signature_payload` / `_signature` | 无进展签名 | 只取 fatal;meanings 排序后紧凑 JSON 做 sha256(**顺序无关**) |
| `_decide` | 核心决策 | 无 fatal→成功动作(可重置无进展状态);有 fatal→与上一轮签名比对:相同→no_progress+1(≥limit→NEEDS_HUMAN);不可修复→NEEDS_HUMAN;迭代耗尽→BUDGET_EXHAUSTED;否则 repair_*。写 last_issue_meanings/no_progress_comparison 诊断 |
| `verify_product_combinations` | 组合 Gate | CHECKER_INCOMPLETE + Python 补查缺主品组合/非法组合 ID;`reset_progress=False` |
| `verify_product_coverage` | 覆盖 Gate | CHECKER_INCOMPLETE + 词袋外名称 + 完全重复商品;`reset_progress=False` |
| `verify_product_scores` | 评分 Gate | CHECKER_INCOMPLETE + 重复/非法/遗漏评分(全部 repairable=False → 失败即转人工) |
| `verify_benefits` | 权益 Gate | CHECKER_INCOMPLETE + 权益 issues 决策 |
| `assemble_candidate` | 组装候选 | 评分写回 classified_items 后调 `assemble_result`,写 `candidate_result` |
| `verify_final` | 最终 Gate | `_run_checks` 警告为空且 `validate_result` 全过 → VERIFIED;否则按警告关键词定 scope(主品/名称/数量→product,金额冲突/到手价/优惠券→coupon,其余 global);有 global issue 直接 NEEDS_HUMAN |

### 4.10 `nodes/patching.py`(事务式 Patch)

| 方法 | 作用 | 验证原理 |
|---|---|---|
| `_validated_item` | 按 scope 校验对象 | 剥离 item_id 后走对应 Pydantic 模型 |
| `_allowed_scopes` | scope 白名单 | product 修复只能动 product/combination;benefit 只能动 price/coupon |
| `apply_maker_patch` | 事务式应用 | deepcopy 副本上依次执行,七道检查:①scope 越权 ②必须关联当前 issue code ③引用未知 issue ④目标 ID 存在 ⑤不得动与 issue 无关对象 ⑥add/update 后整对象再过 Schema ⑦全部集合二次校验 + 台账 S1 连续。任一失败**整体拒绝零写入** |

### 4.11 `nodes/product_processing.py`(确定性商品处理)

| 方法 | 作用 | 原理 |
|---|---|---|
| `_filter_and_unpack` | 只过滤不制造 | 非美妆/购物金剔除;**Python 绝不拆礼盒生成商品**(漏品必须 Kimi 报、Qwen 补) |
| `_fuzzy_evidence` | 模糊匹配证据 | rapidfuzz WRatio;词袋内精确命中 100 分 |
| `process_product_candidates` | 图节点 | n3 归一化 + 等总量汇总删除(**记录 product_reconciliation_decisions**)+ 词袋匹配(无法匹配/无法识别)+ 类型赋值 |
| `apply_product_scores` | 评分回写 | 只把最终验证过的评分按 item_id 写回 normalized/classified,评分缺失则移除旧值 |
| `attach`(嵌套) | 回写辅助 | 单行 attach 逻辑复用 |

### 4.12 `nodes/combination_utils.py`(组合台账确定性工具)

| 方法 | 作用 | 原理 |
|---|---|---|
| `default_sales_combinations` | 兜底单组合台账 | S1/单一销售组合 |
| `sales_combination_contract_errors` | 台账契约错误检查 | 空台账/字段缺失/ID 不连续 |
| `normalize_sales_combinations` | 台账归一化 | 只接受 S1..Sn 连续;非法回退旧台账或默认 S1 |
| `normalize_combination_ids` | 商品归属 ID 归一化 | 单组合强制 S1;多组合只留有效 S 组;全无效返回 UNK |
| `find_missing_main_combination_ids` | 缺主品组合检测 | 兼容内部字段与中文字段 |
| `backfill_spec_gift_combination_mains` | 规格礼赠回填主品 | "50ml礼赠/100ml礼赠"等文字证据补逻辑主品 |
| `expand_by_combination` | 按组合展开条目 | 一条多组合拆多条 |
| `combination_sort_key` | S 编号排序键 | S2<S10 正确排序 |

### 4.13 `nodes/n1_prepare.py`(下载+视觉资产)

| 方法 | 作用 | 原理 |
|---|---|---|
| `build_multimodal_message` | 多模态消息构建(遗留辅助) | 现生产链走 `common.multimodal_messages` |
| `prepare_node` | 图节点 | 阻塞 IO 丢线程池;下载失败→INFRA_ERROR+stop;写 `local_image_path` + 六项视觉资产,**data_url 置空不落 checkpoint** |

### 4.14 `nodes/n3_normalize.py`(归一化+对账)

| 方法 | 作用 | 原理 |
|---|---|---|
| `strip_matching_trailing_spec` | 名称尾部规格剥离 | 名称与规格重复时去重 |
| `normalize_name` / `split_spec_unit` / `parse_qty_from_text` | 名称/规格单位/数量解析 | 确定性清洗与兜底 |
| `split_gift_detail` | 礼盒明细拆条 | (遗留,生产链不拆礼盒) |
| `normalize_item` | 单条归一化 | 规格"0"、数量"1"兜底 |
| `_positive_decimal` / `_has_explicit_multiplier` / `_gift_context_key` / `_dedupe_name` | 汇总删除辅助 | 正数解析/乘法明细判定/赠品上下文键/名称归一键 |
| `remove_equivalent_gift_summaries` | 等总量汇总删除 | 汇总(spec×1)==明细(spec×qty)且同名同上下文才删;`return_decisions=True` 时生成对账记录 |
| `get_node` / `normalize_node` | 遗留节点包装 | 当前由 product_processing 直接调用 |

### 4.15 `nodes/n5_classify.py`(遗留)

| 方法 | 作用 | 注意 |
|---|---|---|
| `classify_gift_type` / `unpack_gift_box` / `classify_items` / `get_node` | 主品/赠品归类与礼盒拆解 | **运行时已不被 graph 引用,仅测试引用**;分类逻辑已内联进 product_processing |

### 4.16 `nodes/n6_price.py`(价格/券清洗)

| 方法 | 作用 | 原理 |
|---|---|---|
| `is_final_price` | 到手价判定 | 到手价关键词 vs 优惠关键词,显式优惠语义优先 |
| `extract_final_prices` | 金额角色判定 | 到手价与优惠互斥;多候选取最低 |
| `extract_coupon_amount` / `extract_coupon_discount` | 券金额/折扣提取 | 金额与"X折"分别解析 |
| `clean_coupon_name` / `clean_coupon_condition` | 券名称/门槛清洗 | 去噪、补"无" |
| `is_summary_coupon` | 优惠汇总排除 | 至高叠减/合计类不输出为券 |
| `extract_coupon_condition_text` | 门槛提取 | 满X/实付满X 等 |
| `_normalize_amount` / `_build_coupon_object` | 金额规范化/券对象构建 | 金额或折扣不可得→整条不输出(v9 收紧) |
| `extract_coupons` | 券清洗主流程 | 购物金过滤+汇总排除+展开+构建+去重 |
| `collect_coupon_candidates` | 候选合并 | Qwen 券 + 价格文本中强优惠语义候选 |
| `deduplicate_coupons` | 去重 | (组合,名称,金额,条件)四元组全同才去重 |
| `validate_exclusivity` | 互斥校验 | 与券金额冲突的到手价条目移除(券优先) |
| `process_prices` | 图节点 | raw_prices/raw_coupons → price_objects/coupon_objects |
| `get_node` / `node` | 遗留包装 | |

### 4.17 `nodes/n7_assemble.py`(组装+自检)

| 方法 | 作用 | 原理 |
|---|---|---|
| `_map_product_name` | label 映射 | label→label_output,保留哨兵值 |
| `_name_sort_key` / `_numeric_sort_key` | 排序键 | 名称按拼音/数字按数值排序 |
| `assemble_result` | 最终组装 | 主品/赠品→到手价→优惠券;规格兜底"0"、数量兜底"1";产品行不输出使用条件;按组合/类型/名称/规格/数量排序 |
| `_run_checks` | Step5 自检(16 条中程序可验部分) | 组合 ID 合法/单组合禁 UNK/缺主品/名称在词包/金额互斥/多到手价/数量≥1/类型枚举 |
| `validate_result` | OutputItem 校验 | 非法条目丢弃并写 errors |
| `get_node` / `assemble_node` | 遗留节点包装 | 当前由 gates 直接调用 |

### 4.18 `runner.py`(编排+持久化)

| 方法 | 作用 | 原理 |
|---|---|---|
| `stable_thread_id` | 稳定线程 ID | loop_version+url+品牌+标题+词袋+映射的 sha256;同输入同 checkpoint |
| `process_one_image` | 单图执行 | 先查 checkpoint:终态复用、`snapshot.next` 存在则断点续跑、否则新跑;只把 VERIFIED 结果输出;返回诊断记录 |
| `_brand_word_package` | 品牌词袋/映射 | label→label_output 冲突即抛错 |
| `run_all` | 整批扇出 | Semaphore 限流、品牌分组、as_completed 收集、token 汇总、写诊断 JSONL+汇总 |
| `print_token_summary` | token 打印 | 按模型分别打印 |

### 4.19 `diagnostics.py` / `main.py` / `compare_export.py` / `image_utils.py` / `models.py`

| 方法 | 作用 |
|---|---|
| `diagnostic_record` | state 全量审计字段 → 一条 JSONL 记录(含全部 checker 层字段、rejected_audits、对账、签名比较) |
| `write_diagnostics` | 写 jsonl + 终态计数汇总 |
| `clean_dataframe` | bus_date 转日期、"nan" 字符串转 NaN |
| `parse_args` / `main` | CLI 解析与主流程 |
| `build_standard_output` / `export_standard_output` | 标准 37 列结果 + left-join 原表导出 |
| `merge_dual_model` / `left_join_with_source` / `export` | 遗留双模型对比导出(兼容保留) |
| `preprocess_image` / `cleanup_old_cache` / `download_one` / `local_to_data_url` | 增强(短边放大/锐化/提对比)/7 天缓存清理/Referer 防盗链+md5 缓存下载/转 base64 |

### 4.20 `vision/`(本地视觉子包)

| 文件/方法 | 作用 | 原理 |
|---|---|---|
| `pipeline.prepare_visual_assets` | n1 调用的入口 | PaddleOCR-VL 版面解析→OCR→(3×3 二次 OCR)→ROI 裁剪→降级诊断 |
| `pipeline._crop_exact` | ROI 精确裁剪 | 按 box 坐标裁剪+最小短边放大 |
| `ocr.py` 各方法 | 推理封装 | 模型加载/版面解析/纯 OCR/分块去重合并 |
| `runtime.py` | 运行环境 | 设备选择(Windows GPU 优先,失败回退 CPU) |
| `preflight.py` | 预检 | 环境/依赖/模型可用性报告 |
| `demo.py` | 演示 | 单图本地跑一遍并打印 |

---

## 5. 字段大全

### 5.1 `ImageState` 全字段(state.py)

**① 输入字段**(run 时传入,不随图变化):

| 字段 | 类型 | 含义 |
|---|---|---|
| `image_url` | str | 主图 URL(也作输出 key) |
| `product_title` | str | 商品标题辅助 |
| `word_package` | list[str] | 当前品牌标准商品名称词袋 |
| `label_mapping` | dict[str,str] | label→label_output 输出名映射 |
| `loop_version` | str | 行为版本,进入 thread_id 隔离旧 checkpoint |

**② n1 视觉字段**:

| 字段 | 含义 |
|---|---|
| `local_image_path` | 本地原图路径(checkpoint 只存路径) |
| `data_url` | base64 原图(每次调用前现转,checkpoint 恒为空) |
| `ocr_blocks` / `ocr_markdown` / `ocr_merged_text` / `ocr_tile_results` / `visual_regions` / `vision_diagnostics` | 版面块/版面 Markdown/合并 OCR 文本/3×3 分块结果/ROI 路径/视觉诊断 |

**③ 商品管线**:

| 字段 | 含义 | 生产者 |
|---|---|---|
| `sales_combinations` | 锁定销售组合台账 S1..Sn | extract_products / Patch |
| `raw_items` | Qwen 原始商品候选(prd_xxx) | extract_products |
| `filtered_items` | 过滤后商品 | process_product_candidates |
| `normalized_items` | 归一化商品(规格/数量兜底、模糊匹配证据) | 同上 |
| `classified_items` | 含类型/名称的商品条目 | 同上 |
| `product_reconciliation_decisions` | n3 删除等总量汇总行的对账记录 | 同上 |

**④ 权益管线**:

| 字段 | 含义 |
|---|---|
| `raw_prices` / `raw_coupons` | Qwen 原始金额/券候选(prc/cpn ID) |
| `price_objects` / `coupon_objects` | Python 清洗后的到手价/优惠券对象 |

**⑤ Checker 分层字段**(商品三层 + 权益一层):

| 字段 | 含义 |
|---|---|
| `combination_checks` | 层1 组合证据台账(Kimi 原样) |
| `combination_checker_issues` / `combination_checker_complete` | 层1 生成的 issue / 是否完成 |
| `coverage_checks` / `coverage_validation_details` | 层2 覆盖证据台账 / 汇总算术与守卫诊断 |
| `coverage_checker_issues` / `coverage_checker_complete` | 层2 issue / 完成标志 |
| `scorer_issues` / `scorer_complete` / `latest_item_evaluations` | 层3 issue(成功时恒空)/完成标志/最新评分 |
| `checker_issues` / `checker_complete` | 权益层 issue / 完成标志 |
| `checker_retry_count` / `checker_semantic_errors` | 权益层重试次数/语义错误(遗留扁平版) |

**⑥ 循环控制字段**:

| 字段 | 含义 |
|---|---|
| `current_issues` | 当前 Gate 选定的 fatal issues(交给修复节点) |
| `product_iteration` / `benefit_iteration` | 业务迭代计数(只由成功生成的 Patch +1) |
| `max_product_iterations` / `max_benefit_iterations` | 迭代预算 |
| `last_issue_signature` / `last_issue_meanings` / `no_progress_count` / `no_progress_comparison` / `no_progress_limit` | 无进展检测:上一轮签名/语义指纹/连续计数/对比明细/阈值 |
| `next_action` | Gate 意图(coverage/score/apply_scores/assemble/repair_product/repair_benefit/verified/stop) |
| `terminal_status` | 终态(非空即短路) |

**⑦ 输出字段**:

| 字段 | 含义 |
|---|---|
| `candidate_result` | 组装候选(仅内部流转) |
| `verified_result` / `result` | VERIFIED 最终条目(进业务 Excel) |
| `proof_bundle` | 非 VERIFIED 终态的证据包(schema_valid/unresolved_issues/快照/迭代) |

**⑧ reducer 字段(特殊合并语义)**:

| 字段 | reducer | 语义 |
|---|---|---|
| `issue_history` / `repair_history` / `item_evaluation_history` / `candidate_history` / `errors` | `operator.add` | 每节点返回的列表**追加**到已有列表(审计轨迹) |
| `checker_retry_counts` / `checker_semantic_errors_by_layer` / `checker_sanitized_fields_by_layer` / `checker_rejected_audits_by_layer` / `model_contract_retry_counts` / `model_contract_errors_by_stage` | `merge_mapping` | 按 key(layer/stage)覆盖合并 |
| `token_usage` | `merge_token_usage` | 按模型累加 |

其余字段无 reducer:节点返回即**整体覆盖**旧值。

### 5.2 Schema 契约字段(schemas.py)

**Maker 输出契约**:

| 模型 | 字段 | 校验 |
|---|---|---|
| `SalesCombination` | combination_id / purchase_condition_text(非空) / section_hint? / evidence_text(非空) | extra=forbid |
| `RawProduct` | raw_name / qwen_matched_name?(词袋原文或 null) / combination_ids / spec_text? / qty_text? / is_cosmetic / is_shopping_credit / role(主品|赠品) / gift_label? / is_gift_box / box_contents? / is_sample / section_hint? | extra=forbid |
| `RawPrice` | amount(强制转 str) / context_text / combination_ids | |
| `RawCoupon` | text / combination_ids | |
| `ProductExtraction` | sales_combinations + products | **台账 S1..Sn 连续、非空** |
| `BenefitExtraction` | prices + coupons | |

**Checker 输出契约**:

| 模型 | 字段 | 关键校验 |
|---|---|---|
| `VerificationIssue` | code / scope(product|price|coupon|combination|global) / severity(warning|fatal) / combination_id? / target_item_id? / evidence_text / evidence_region? / observed_fact / repair_instruction / confidence(0..1) / repairable / affected_fields(枚举列表) | |
| `CoverageCheck` | evidence_id / evidence_text / evidence_region? / object_description / status(covered|missing|ambiguous|summary_only|non_cosmetic|duplicate_display) / matched_item_ids / confidence | 状态-引用约束由语义校验器管 |
| `CombinationEvidenceCheck` | 同上结构 + evidence_kind(purchase_option|gift_summary|discount_summary|threshold|decorative_display) + matched_combination_ids + expected_main_spec_text? | 状态机由语义校验器管 |
| `ScoreBreakdown` | raw_name(0-20) / matched_name(0-30) / role(0-15) / specification(0-20) / quantity(0-10) / existence_and_grouping(0-5) | 各维有上限 |
| `ItemEvaluation` | target_item_id / score_breakdown / vlm_evaluation_score(0-100) / reason | **总分=六项和** |
| `CombinationAudit` | **combination_checks(必填)** + issues + audit_complete | |
| `ProductCoverageAudit` | coverage_checks + field_issues + audit_complete | |
| `ProductScoreAudit` | item_evaluations + audit_complete | |
| `CheckerAudit` | issues + coverage_checks + item_evaluations + audit_complete | 权益层两个列表必须为空 |

**Patch 契约**:

| 模型 | 字段 | 校验 |
|---|---|---|
| `PatchOperation` | action(add|update|delete) / scope(product|price|coupon|combination) / target_item_id? / changes(自由 dict,按 scope 二次校验) / evidence / addresses_issue_codes | add 不得带 ID;update/delete 必须带 |
| `MakerPatch` | operations | |

**最终输出契约**:

| 模型 | 字段 | 校验 |
|---|---|---|
| `OutputItem` | 组合ID / 类型 / 名称? / 规格? / 单位? / 数量? / 金额? / 使用条件? / RapidFuzz匹配分数? / VLM测评分数? | extra=allow;类型 4 枚举;组合 ID 合法 |

### 5.3 配置字段(config.py)

| 常量 | 默认 | 环境变量 | 用途 |
|---|---|---|---|
| `LOOP_VERSION` | ppi-loop-v9 | - | 行为版本,进 thread_id |
| `KIMI_MODEL` / `QWEN_MODEL` | kimi-k2.6 / qwen3.7-plus | PPI_CHECKER_MODEL / PPI_MAKER_MODEL | Checker/Maker 模型 |
| `MAX_TOKENS` / `TEMPERATURE` / `TOP_P` | 16384 / 0 / 0.9 | PPI_MAX_TOKENS | 调用参数 |
| `MAX_RETRY` | 3 | PPI_MAX_RETRY | 传输退避重试次数 |
| `CHECKER_SEMANTIC_MAX_RETRY` | 2 | PPI_CHECKER_SEMANTIC_MAX_RETRY | Checker 语义重试次数 |
| `MODEL_CONTRACT_MAX_RETRY` | 2 | PPI_MODEL_CONTRACT_MAX_RETRY | Maker 结构重试次数 |
| `BACKOFF_BASE` | 2 | PPI_BACKOFF_BASE | 指数退避基数 |
| `RETRYABLE_HINTS` | 503/429/50508/超时等 | - | 传输重试识别词 |
| `CONCURRENCY` / `CHECKER_CONCURRENCY` | 10 / 10 | PPI_CONCURRENCY / PPI_CHECKER_CONCURRENCY | 图片/Checker 并发 |
| `MAX_PRODUCT_ITERATIONS` / `MAX_BENEFIT_ITERATIONS` / `NO_PROGRESS_LIMIT` | 2 / 1 / 2 | 同名环境变量 | 循环预算/无进展阈值 |
| `STATE_DIR` / `CHECKPOINT_DB` / `DIAGNOSTICS_DIR` | cache/ppi_loop / checkpoints.sqlite / output/ppi_loop_diagnostics | PPI_LOOP_STATE_DIR / PPI_LOOP_DIAGNOSTICS_DIR | 持久化与诊断 |
| `IMG_CACHE_*` / `CACHE_MAX_DAYS` | cache/img_cache… / 7 | PPI_IMG_CACHE_DIR / PPI_CACHE_MAX_DAYS | 图片三级缓存 |
| `MIN_SHORT_SIDE` / `SHARPNESS_FACTOR` / `CONTRAST_FACTOR` | 1600 / 1.3 / 1.15 | - | 图片增强 |
| `VISION_*`(约 12 项) | 见文件 | PPI_VISION_* | PaddleOCR-VL 行为(开关/设备/分块/ROI/并发) |
| `FUZZY_MATCH_THRESHOLD` | 60 | - | rapidfuzz 阈值 |

### 5.4 业务输出字段(输出 Excel 的 11 列)

`主图URL、组合ID、类型(主品|赠品|到手价|优惠券)、名称、规格、单位、数量、金额、使用条件、RapidFuzz匹配分数、VLM测评分数`。只有 `VERIFIED` 的条目进输出;`compare_export.export_standard_output` 再 left-join 回原表 37 列。

---

## 6. 验证原理专题(底层机制)

### 6.1 四道防线(对每个模型输出的验证顺序)

```
① Schema 契约(extra=forbid + validator)      → 结构层,错则契约错误
② sanitize_checker_payload(仅 Checker)        → 容忍层,只删白名单无害字段
③ 语义校验器(每层一个)                        → 语义层,跨字段/跨对象一致性
④ 语义重试(反馈错误重问,上限内)              → 恢复层
   失败 → CHECKER_UNSTABLE → NEEDS_HUMAN      → 兜底层,绝不静默接受
```

### 6.2 契约错误 vs 基础设施错误(`common.is_contract_error`)

- **契约错误**(本地可判定):JSON 解析失败、Pydantic 校验失败、空正文、CHECKER_* 清洗拒绝、MODEL_CONTRACT_ERROR → 可反馈重试,失败转人工
- **基础设施错误**(远程):402 余额、401 鉴权、429/503 限流、超时 → 传输层退避重试,耗尽转 INFRA_ERROR
- 关键设计:契约错误**不做传输退避**(重试同请求无意义);基础设施错误**不做契约反馈**(重问解决不了钱的问题)

### 6.3 Maker 契约重试(`call_json_model_with_contract_retry`)

- 只包 Maker(商品/权益/Patch 四个调用点);Checker 用自己的语义重试,两层**不嵌套**
- 重试发生在**节点内部**,不经过图的循环边 → 不消耗业务迭代次数
- 每次重试追加一条 user 消息:Schema 名 + 具体结构错误(≤2000 字符)

### 6.4 无进展签名(gates.py,v8 语义指纹)

- 归一化:NFKC(全角转半角)+ casefold + 去空白和标点,**保留字母数字**
- 指纹:`code/scope/组合ID/目标ID/affected_fields(排序)/evidence_region/evidence_anchor(evidence_text 优先,无则 observed_fact)`
- 全部 fatal issue 的指纹**排序后**哈希 → 顺序无关、措辞漂移(仅 observed_fact)无关
- 相同指纹 → no_progress+1;达到 `no_progress_limit` → NEEDS_HUMAN
- 已知边界:evidence_text/region 的措辞变化仍会改变指纹;A→B→A 振荡每步都算"新问题",由迭代预算兜底

### 6.5 事务式 Patch(patching.py)

七道检查(见 4.10)全部通过才**一次性**提交;任何一步失败返回空更新,原始 state 零污染。核心是 `deepcopy` 副本 + 全量重校验。

### 6.6 汇总算术与对账守卫(checker.py + n3)

- `summary_only` 必须:引用真实商品 ID、被引用明细 spec×qty 全部可算、单位唯一且等于汇总单位、Decimal 精确相等
- n3 删除等总量汇总行时写 `product_reconciliation_decisions`;Coverage 层若把已删汇总判为 missing → 语义校验直接拒绝并重试

### 6.7 组合台账契约(schemas + combination_utils + checker 层1)

- Maker 输出:S1..Sn 必须连续、非空(model validator)
- 层1 状态机:purchase_option 是唯一合法"建组"证据;gift_summary/discount_summary/threshold/decorative_display 只能 ignored(未建组)或 invalid_assignment(被错误建组)
- 每个 Maker 组合必须被至少一个 covered/invalid_assignment 检查引用(防止"只给笼统结论")

### 6.8 终态短路与 checkpoint

- 任何节点写出非空 `terminal_status`,下一路由立即 stop → `finalize_unverified`
- `stable_thread_id` 含 `loop_version`:行为变更必须升版本,否则旧 checkpoint 会被复用,新代码不生效
- 终端 checkpoint 直接复用;中断 checkpoint 从 `snapshot.next` 续跑

### 6.9 权益层确定性复现(问题 5 的权益侧收口)

权益 Checker 的 issue 不能自由发挥,每个 issue 都要能被 Python **确定性复现**:
- 证据定位:`matches_evidence` 用 item_id 或压缩文本双向包含,把 issue 对到具体 raw 记录
- 汇总排除:证据命中 `is_summary_coupon`(至高叠减/合计类)的 MISSING_* 一律拒绝
- 金额/折扣核验:MISSING_COUPON 必须有 `extract_coupon_amount/extract_coupon_discount` 可解析的明确值,且该值不在 raw_coupons、不在 normalized_coupons
- 角色冲突只认 normalized 层同组合同金额的重叠(raw 层证据池天然重叠,不算冲突)
- NORMALIZATION_LOSS 要求 raw 证据正确 + normalized 确实缺失(缺金额或缺券任一成立)
- `repairable=false` 只允许"程序可确定复现的归一化问题",否则报错要求改为 true

---

## 7. 单图完整生命周期(数据流)

1. **run**:`process_one_image` 用稳定 thread_id 查 checkpoint(终态复用/续跑/新跑)
2. **initialize**:写入全部循环控制字段初始值
3. **prepare**:下载原图 + PaddleOCR-VL 资产 → `local_image_path` + OCR/ROI 字段
4. **extract_products**(Qwen):`sales_combinations` + `raw_items`
5. **process_products**(Python):`filtered_items`/`normalized_items`/`classified_items` + `product_reconciliation_decisions`
6. **audit_product_combinations**(Kimi):`combination_checks` → `combination_checker_issues`
7. **verify_product_combinations**(Gate):无 fatal → coverage;有 → repair_product 或终态
8. **audit_product_coverage**(Kimi):`coverage_checks` → `coverage_checker_issues`(含 MISSING_PRODUCT)
9. **verify_product_coverage**(Gate):无 fatal → score;有 → repair_product 或终态
10. **score_products**(Kimi):`latest_item_evaluations`
11. **verify_product_scores**(Gate):无 fatal → apply_scores;有 → 终态(评分问题不可修复)
12. **apply_product_scores**(Python):评分写回 classified
13. **extract_benefits**(Qwen):`raw_prices`/`raw_coupons`
14. **process_benefits**(Python):`price_objects`/`coupon_objects`
15. **audit_benefits**(Kimi) → **verify_benefits**(Gate):失败 repair_benefit 或终态
16. **assemble**(Gate):`candidate_result`
17. **verify_final**(Gate):VERIFIED → 输出;否则 repair/转人工
18. **finalize_unverified**:非 VERIFIED 终态打包 `proof_bundle`

---

## 8. Review 检查清单(历次 review 发现的要点,人工复核时逐条对照)

- [ ] `n5_classify.py` 是遗留死代码(仅测试引用),确认无回归风险
- [ ] `verify_product_scores` 路由映射无 repair 分支:当前所有 fatal 都 repairable=False 保证走 stop,若未来加可修复 issue 会运行时报错
- [ ] sanitize 删除安全元数据字段前未检查该字段是否属于 schema 合法字段(当前四个 Checker schema 无同名冲突,未来加字段有被静默丢弃风险)
- [ ] `_SELF_CONTRADICTION_MARKERS` 的"但是/然而"可能误伤合法单事实;收益与假阳性需用 `checker_rejected_audits_by_layer` 数据权衡
- [ ] 无进展签名对 evidence_text/region 措辞漂移仍敏感;A→B→A 振荡靠迭代预算兜底
- [ ] `normalize_sales_combinations` 对"空条件但 ID 合法"的台账仍静默回退默认 S1(声称"非法台账不进导出"的残留缺口)
- [ ] 对账守卫 `_matches_reconciliation_summary` 是名称+规格子串匹配,不看区域,存在同名同规格真实漏品被误伤的可能
- [ ] 汇总算术单位白名单只有 ml/g/kg/l;支/瓶/片等单位的汇总会解析失败
- [ ] 顶层 `total`、数字/布尔元数据、嵌套元数据仍会被严格拒绝(与清洗声称的范围一致,但值得监控 Kimi 实际输出习惯)
- [ ] checkpoint 续跑依赖本地图片缓存(7 天清理);`multimodal_messages` 在 try 外读文件,缓存被清会导致该图崩溃
- [ ] README 提到 SILICONFLOW_API_KEY,但代码只用 DASHSCOPE_API_KEY(文档不一致)
- [ ] `route_after_model` 用 truthy 检查隐式约定"非空 terminal_status 即终态",新增代码需遵守

---

## 9. 测试导航(tests/,当前 194 用例)

| 测试文件 | 覆盖内容 |
|---|---|
| `test_graph_loop.py` | 整图循环拓扑与路由 |
| `test_checker_semantics.py` | 四层语义校验器(组合/覆盖/评分/权益确定性复现)+ 语义重试不消耗迭代 + rejected_audits 快照 |
| `test_checker_schema_tolerance.py` | 安全清洗/总数校验/保护字段/权益重试转人工 |
| `test_maker_contract_retry.py` | Maker 结构重试/分类/迭代不消耗 |
| `test_gates.py` | Gate 决策/无进展签名语义/诊断字段 |
| `test_contracts_and_patching.py` | Patch 契约与事务式应用 |
| `test_combinations.py` | 组合台账归一化/主品回填 |
| `test_normalize.py` / `test_classify.py` / `test_price.py` | n3/n5/n6 规则 |
| `test_compare_export.py` / `test_persistence.py` / `test_vision.py` | 导出/checkpoint/视觉 |

运行:`PYTHONPATH=. python -m pytest -q tests`
