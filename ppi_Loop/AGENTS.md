# PPI Loop Agent Instructions

## 适用范围

本文件适用于整个 `ppi_Loop/` 项目。该项目是从 `ppi_langgraph` 独立重构而来的 Loop Engineering 实现，用于从美妆商品标题图和促销海报中提取销售组合、主品、赠品、规格、数量、到手价和优惠券，并输出 Excel。

当前行为版本：`ppi-loop-v10`。

## 安全规则

- 禁止批量删除文件或目录。
- 禁止使用 `rm -rf`、`rmdir /s`、`Remove-Item -Recurse`、`del /s` 等递归批量删除命令。
- 删除时只能一次处理一个明确的文件路径；如确需批量删除，停止操作并请用户手动处理。
- 不得提交 API Key、Token、密码、Cookie、证书或业务敏感数据。
- 模型密钥必须通过环境变量提供；当前运行代码只需要 `DASHSCOPE_API_KEY`。
- 未经用户明确要求，不要发送真实模型请求、运行高成本图片批处理或修改业务输出文件。

## 核心架构

```text
图片与 OCR
→ Qwen 商品 Maker
→ Python 商品归一化
→ Kimi 销售组合审计
→ Kimi 商品覆盖审计
→ Kimi 商品评分
→ Qwen 权益 Maker
→ Python 权益清洗
→ Kimi 权益审计
→ 最终组装与 Gate
→ VERIFIED / NEEDS_HUMAN / BUDGET_EXHAUSTED / INFRA_ERROR
```

### 不可破坏的职责边界

- **Qwen 是唯一 Maker**：负责生成商品、权益和修复 Patch。
- **Kimi 是纯 Checker**：只输出结构化证据、问题和评分，不得生成或修改最终业务对象。
- 商品 Checker 必须保持三次独立调用：销售组合、商品覆盖、商品评分。
- **Python Gate 是最终裁决层**：负责 Schema、确定性规则、事务式 Patch、循环预算、路由和终态。
- Python 可以清洗、校验、过滤和路由，但不得凭空制造商品。
- 只有 `VERIFIED` 结果可以写入业务提取列；其他终态只能保留候选快照和诊断证据。

## 修改前必须先做的事

除非用户明确要求立即实施，否则先进行只读分析：

1. 从最新诊断 JSONL 确认失败节点和终态。
2. 追踪该图片在 `ImageState` 中的字段变化。
3. 对照 Maker Prompt、Checker Prompt、Schema、Python 规则和 Gate。
4. 明确冲突规则的唯一事实来源和最终裁决权。
5. 说明根因后再提出修改方案。

禁止只针对一张图片追加提示词或 Gate 特判。若问题来自契约不一致，应先统一契约，再修改代码。

## 推荐阅读顺序

1. `REVIEW_GUIDE.md`：完整架构、字段和 Review 风险。
2. `ppi_loop/main.py`：CLI 输入与输出入口。
3. `ppi_loop/runner.py`：批处理、并发、checkpoint 和诊断。
4. `ppi_loop/graph.py`：节点顺序和条件路由。
5. `ppi_loop/state.py`：全局状态及 reducer。
6. `ppi_loop/schemas.py`：Maker、Checker、Patch 和输出契约。
7. `ppi_loop/nodes/maker.py`：Qwen 生成与修复。
8. `ppi_loop/nodes/product_processing.py`：商品确定性处理。
9. `ppi_loop/nodes/checker.py`：Kimi 四层审计和语义校验。
10. `ppi_loop/nodes/gates.py`：问题签名、循环决策和终态。
11. `ppi_loop/nodes/patching.py`：事务式 Patch。
12. `ppi_loop/nodes/n6_price.py`：价格与优惠券清洗。
13. `ppi_loop/nodes/n7_assemble.py`：最终组装与自检。
14. `ppi_loop/prompts/contracts.py`：各模型任务边界。
15. `ppi_loop/diagnostics.py` 与 `tests/`：行为证据和回归保护。

## 修改规则

- 保持现有 Maker–Checker–Gate 边界，不把 Checker 的结论直接写入业务对象。
- 模型输出字段变化时，必须同步检查 Prompt、Pydantic Schema、State、语义校验、诊断和测试。
- 新增 Gate issue 时，必须确认：
  - `scope`、`repairable` 和 `severity` 是否正确；
  - `next_action` 在 `graph.py` 中有对应路由；
  - 是否会错误消耗商品或权益修复预算。
- 修改无进展逻辑时，应比较问题的实际语义和证据，不能只比较 issue code。
- 修改 Checker 容错时，只能清理白名单内的无害说明字段；跨层业务字段必须继续拒绝。
- Patch 必须保持事务式：任一操作失败时整批拒绝，不允许部分写入。
- 若行为变化会影响 checkpoint 结果，必须同步提升 `ppi_loop/config.py` 中的 `LOOP_VERSION`，避免复用旧终态。
- 不要顺手重构无关模块，不要修改 `ppi_langgraph` 或 `ppi_langgraph_test`。

## 验证要求

代码修改后必须执行：

```bash
PYTHONPATH=. python -m pytest -q tests
```

当前基线为 `194 passed`。完成前还应检查：

- 测试退出码为 0；
- 没有新增未解释的 warning 或失败；
- Git diff 只包含本次任务范围；
- 未提交 `.DS_Store`、`__pycache__`、`.pytest_cache`、`.pyc`、SQLite checkpoint、诊断输出或真实业务数据；
- 涉及真实图片行为时，自动化测试通过后仍需单独进行真实图片回归，不能用单元测试代替模型回归。

## 已知重点风险

- 名称非空要求、词袋合法性、Qwen Patch 和最终 Gate 之间仍可能存在契约冲突。
- `verify_product_scores` 当前没有 `repair_product` 路由；新增可修复评分 issue 前必须补齐路由。
- Checker 安全清洗、无进展签名、汇总规格算术和对账守卫都有明确适用边界，修改前先阅读 `REVIEW_GUIDE.md`。
- checkpoint 与 `LOOP_VERSION` 强关联；忘记升版本可能导致新代码看似没有生效。

## 输出与沟通

- 先报告证据和根因，再报告建议。
- 明确区分代码问题、模型契约问题、业务规则问题和基础设施问题。
- 不把 `BUDGET_EXHAUSTED` 描述成 Token 超限；它表示业务修复轮次耗尽。
- 不宣称修复完成，除非已经运行对应验证并检查输出。
