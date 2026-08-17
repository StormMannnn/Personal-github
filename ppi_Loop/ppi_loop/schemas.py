"""Strict contracts for maker output, checker evidence, patches and final rows."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    # 契约基类:extra="forbid" 拒绝多余字段,模型输出多一个键就校验失败,倒逼 Maker 严格按契约输出。
    model_config = ConfigDict(extra="forbid")


class SalesCombination(StrictModel):
    # 销售组合契约:组合 ID、购买条件原文、区域提示与证据文本。
    combination_id: str
    purchase_condition_text: str
    section_hint: str | None = None
    evidence_text: str


class RawProduct(StrictModel):
    # 原始商品契约:名称、匹配的组合 ID、规格数量、主品/赠品角色、赠品/小样等属性。
    raw_name: str
    qwen_matched_name: str | None = None
    combination_ids: list[str] = Field(default_factory=list)
    spec_text: str | None = None
    qty_text: str | None = None
    is_cosmetic: bool
    is_shopping_credit: bool = False
    role: Literal["主品", "赠品"]
    gift_label: str | None = None
    is_gift_box: bool = False
    box_contents: str | None = None
    is_sample: bool = False
    section_hint: str | None = None


class RawPrice(StrictModel):
    # 原始价格契约:金额(校验前统一转字符串)+ 上下文文本 + 关联组合 ID。
    amount: str
    context_text: str
    combination_ids: list[str] = Field(default_factory=list)

    @field_validator("amount", mode="before")
    @classmethod
    def amount_to_string(cls, value: object) -> str:
        # 金额字段校验前回调:把数字等任意入参强转成字符串,保证后续按文本统一处理。
        return str(value)


class RawCoupon(StrictModel):
    # 原始优惠券契约:券面文本 + 关联组合 ID。
    text: str
    combination_ids: list[str] = Field(default_factory=list)


class ProductExtraction(StrictModel):
    # Maker 商品抽取输出契约:一组销售组合 + 一组原始商品。
    sales_combinations: list[SalesCombination] = Field(default_factory=list)
    products: list[RawProduct] = Field(default_factory=list)

    @model_validator(mode="after")
    def combinations_must_be_explicit_and_contiguous(self) -> "ProductExtraction":
        # 校验原理:组合 ID 必须显式给出,且从 S1 开始连续编号(不允许跳号/重排/缺号)。
        # 至少要输出一条组合,否则抛 INVALID_SALES_COMBINATIONS 被门卫拦住。
        ids = [item.combination_id.strip().upper() for item in self.sales_combinations]
        expected = [f"S{index}" for index in range(1, len(ids) + 1)]
        if not ids:
            raise ValueError("INVALID_SALES_COMBINATIONS: 至少输出S1")
        if ids != expected:
            raise ValueError(
                f"INVALID_SALES_COMBINATIONS: 组合ID必须连续为{expected}，实际为{ids}"
            )
        return self


class BenefitExtraction(StrictModel):
    # Maker 权益抽取输出契约:一组价格 + 一组优惠券。
    prices: list[RawPrice] = Field(default_factory=list)
    coupons: list[RawCoupon] = Field(default_factory=list)


IssueScope = Literal["product", "price", "coupon", "combination", "global"]
IssueSeverity = Literal["warning", "fatal"]
AffectedField = Literal[
    "combination", "raw_name", "qwen_matched_name", "role", "spec_text",
    "qty_text", "gift_label", "is_cosmetic", "is_gift_box", "box_contents",
    "is_sample", "section_hint",
]
CoverageStatus = Literal[
    "covered", "missing", "ambiguous", "summary_only", "non_cosmetic",
    "duplicate_display",
]
CombinationEvidenceKind = Literal[
    "purchase_option", "gift_summary", "discount_summary", "threshold",
    "decorative_display",
]
CombinationEvidenceStatus = Literal[
    "covered", "missing", "ignored", "invalid_assignment",
]


class VerificationIssue(StrictModel):
    # 检查器问题契约:问题编码/作用域/严重度/证据/修复指令/置信度,repairable 标记能否自动修复。
    code: str
    scope: IssueScope
    severity: IssueSeverity
    combination_id: str | None = None
    target_item_id: str | None = None
    evidence_text: str = ""
    evidence_region: str | None = None
    observed_fact: str
    repair_instruction: str
    confidence: float = Field(ge=0, le=1)
    repairable: bool = True
    affected_fields: list[AffectedField] = Field(default_factory=list)


class CoverageCheck(StrictModel):
    # 视觉覆盖检查契约:图上每条证据对象对应一个状态(covered/missing 等)及命中的商品 ID。
    evidence_id: str
    evidence_text: str = ""
    evidence_region: str | None = None
    object_description: str
    status: CoverageStatus
    matched_item_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)


class CombinationEvidenceCheck(StrictModel):
    # 组合证据检查契约:evidence_kind 区分五类证据(购买选项/赠品摘要/优惠摘要/门槛/装饰展示),
    # status 四态(covered/missing/ignored/invalid_assignment)记录该证据是否支撑组合。
    evidence_id: str
    evidence_text: str = ""
    evidence_region: str | None = None
    object_description: str
    evidence_kind: CombinationEvidenceKind
    status: CombinationEvidenceStatus
    matched_combination_ids: list[str] = Field(default_factory=list)
    expected_main_spec_text: str | None = None
    confidence: float = Field(ge=0, le=1)


class ScoreBreakdown(StrictModel):
    # 六维打分明细契约:各维度有上限(raw_name≤20、matched_name≤30、role≤15、spec≤20、qty≤10、grouping≤5)。
    raw_name: int = Field(ge=0, le=20)
    matched_name: int = Field(ge=0, le=30)
    role: int = Field(ge=0, le=15)
    specification: int = Field(ge=0, le=20)
    quantity: int = Field(ge=0, le=10)
    existence_and_grouping: int = Field(ge=0, le=5)

    @property
    def total(self) -> int:
        # 六项分数求和,作为 ItemEvaluation 总分一致性校验的依据。
        return sum((
            self.raw_name, self.matched_name, self.role,
            self.specification, self.quantity, self.existence_and_grouping,
        ))


class ItemEvaluation(StrictModel):
    # 单商品最终评测契约:目标商品 ID + 六维打分明细 + VLM 总分 + 理由。
    target_item_id: str
    score_breakdown: ScoreBreakdown
    vlm_evaluation_score: int = Field(ge=0, le=100)
    reason: str

    @model_validator(mode="after")
    def total_must_match(self) -> "ItemEvaluation":
        # 校验原理:VLM 总分必须等于六维明细之和,不一致就抛 INVALID_CHECKER_SCORE,防打分自相矛盾。
        if self.vlm_evaluation_score != self.score_breakdown.total:
            raise ValueError(
                f"INVALID_CHECKER_SCORE: {self.vlm_evaluation_score} != "
                f"{self.score_breakdown.total}"
            )
        return self


class CombinationAudit(StrictModel):
    # Checker 第一层输出契约:只报销售组合相关的证据与问题。
    """Kimi layer 1: sales-combination findings only."""

    combination_checks: list[CombinationEvidenceCheck]
    issues: list[VerificationIssue] = Field(default_factory=list)
    audit_complete: bool


class ProductCoverageAudit(StrictModel):
    # Checker 第二层输出契约:只报视觉覆盖检查与 Qwen 字段问题。
    """Kimi layer 2: visual coverage and Qwen field findings only."""

    coverage_checks: list[CoverageCheck] = Field(default_factory=list)
    field_issues: list[VerificationIssue] = Field(default_factory=list)
    audit_complete: bool


class ProductScoreAudit(StrictModel):
    # Checker 第三层输出契约:只报不可变的 Qwen 商品逐条评分。
    """Kimi layer 3: immutable Qwen-item evaluations only."""

    item_evaluations: list[ItemEvaluation] = Field(default_factory=list)
    audit_complete: bool


class CheckerAudit(StrictModel):
    # 权益检查器(Benefit checker)独立响应契约,与商品分层审计分开保存。
    """Benefit-checker response retained separately from the product layers."""

    issues: list[VerificationIssue] = Field(default_factory=list)
    coverage_checks: list[CoverageCheck] = Field(default_factory=list)
    item_evaluations: list[ItemEvaluation] = Field(default_factory=list)
    audit_complete: bool


PatchAction = Literal["add", "update", "delete"]
PatchScope = Literal["product", "price", "coupon", "combination"]


class PatchOperation(StrictModel):
    # 修复补丁单条操作契约:动作(add/update/delete)+ 作用域 + 目标 ID + 变更内容 + 证据与问题码。
    action: PatchAction
    scope: PatchScope
    target_item_id: str | None = None
    changes: dict[str, Any] = Field(default_factory=dict)
    evidence: list[str] = Field(default_factory=list)
    addresses_issue_codes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def target_contract(self) -> "PatchOperation":
        # 目标 ID 契约校验:add 不得自报 ID(由系统分配),update/delete 必须指定 target_item_id。
        if self.action == "add" and self.target_item_id is not None:
            raise ValueError("add操作不得自行指定ID")
        if self.action in {"update", "delete"} and not self.target_item_id:
            raise ValueError(f"{self.action}操作必须指定target_item_id")
        return self


class MakerPatch(StrictModel):
    # Maker 修复输出契约:一组补丁操作,按序应用到对应作用域。
    operations: list[PatchOperation] = Field(default_factory=list)


class RepairRecord(StrictModel):
    # 修复记录契约:作用域(product/benefit)、迭代轮次、是否被采纳、操作列表与原因。
    scope: Literal["product", "benefit"]
    iteration: int
    accepted: bool
    operations: list[dict[str, Any]] = Field(default_factory=list)
    reason: str


VALID_OUTPUT_TYPES = {"主品", "赠品", "到手价", "优惠券"}


class OutputItem(BaseModel):
    # 最终输出行契约(extra=allow 宽松模式):字段即 Excel 表头,含组合 ID/类型/名称/规格/金额/分数。
    model_config = ConfigDict(extra="allow")
    组合ID: str
    类型: str
    名称: str | None = None
    规格: str | None = None
    单位: str | None = None
    数量: str | None = None
    金额: str | None = None
    使用条件: str | None = None
    RapidFuzz匹配分数: float | None = None
    VLM测评分数: int | None = None

    @field_validator("类型")
    @classmethod
    def valid_type(cls, value: str) -> str:
        # 类型校验:只能是 主品/赠品/到手价/优惠券 四类之一,否则抛"非法类型"。
        if value not in VALID_OUTPUT_TYPES:
            raise ValueError(f"非法类型: {value}")
        return value

    @field_validator("组合ID")
    @classmethod
    def valid_combination(cls, value: str) -> str:
        # 组合 ID 校验:只允许 UNK 或 S+纯数字 两种写法,其它格式一律拒绝。
        if value == "UNK" or (value.startswith("S") and value[1:].isdigit()):
            return value
        raise ValueError(f"非法组合ID: {value}")


TERMINAL_STATUSES = {
    "VERIFIED", "NEEDS_HUMAN", "BUDGET_EXHAUSTED", "INVALID_INPUT", "INFRA_ERROR",
}
