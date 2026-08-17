"""Transactional, scope-bounded application of Qwen Maker patches."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from ..schemas import MakerPatch, RawCoupon, RawPrice, RawProduct, SalesCombination
from .combination_utils import normalize_sales_combinations
from .common import assign_ids


COLLECTIONS = {
    "product": ("raw_items", RawProduct, "prd"),
    "price": ("raw_prices", RawPrice, "prc"),
    "coupon": ("raw_coupons", RawCoupon, "cpn"),
    "combination": ("sales_combinations", SalesCombination, "cmb"),
}


def _validated_item(scope: str, value: dict[str, Any]) -> dict[str, Any]:
    # 按 scope 用对应 Pydantic Schema 校验单个对象:先剔除 item_id(由 Python 分配),再整体 dump。
    _, schema, _ = COLLECTIONS[scope]
    clean = dict(value)
    clean.pop("item_id", None)
    return schema.model_validate(clean).model_dump()


def _allowed_scopes(repair_scope: str) -> set[str]:
    # 越权边界:product 修复只能动 product/combination,benefit 修复只能动 price/coupon。
    return {"product", "combination"} if repair_scope == "product" else {"price", "coupon"}


def apply_maker_patch(
    state: dict,
    patch: MakerPatch,
    repair_scope: str,
) -> tuple[dict, bool, str]:
    # 事务式应用整个 Patch:先在 deepcopy 副本上执行全部操作(校验 scope 越权、必须关联
    # 当前 issue code、目标ID存在且不得动与 issue 无关对象),任一失败整体拒绝、绝不部分写入;
    # add/update 后逐对象再过 Schema,组合台账归一化后仍须 S1 连续,全部通过才返回新集合。
    """Apply the full patch or reject it without partial writes."""
    allowed = _allowed_scopes(repair_scope)
    issue_codes = {issue.get("code") for issue in state.get("current_issues", [])}
    current_issues = state.get("current_issues", [])
    working = {
        key: deepcopy(state.get(key, []))
        for key, _, _ in COLLECTIONS.values()
    }
    try:
        for operation in patch.operations:
            if operation.scope not in allowed:
                raise ValueError(f"修复{repair_scope}时不允许操作{operation.scope}")
            if not operation.addresses_issue_codes:
                raise ValueError("每个操作必须关联问题编码")
            unknown = set(operation.addresses_issue_codes) - issue_codes
            if unknown:
                raise ValueError(f"操作引用未知问题: {sorted(unknown)}")
            relevant_targets = {
                issue.get("target_item_id")
                for issue in current_issues
                if issue.get("code") in operation.addresses_issue_codes
                and issue.get("target_item_id")
            }

            key, _, prefix = COLLECTIONS[operation.scope]
            collection = working[key]
            by_id = {
                (item.get("combination_id") if operation.scope == "combination" else item.get("item_id")): index
                for index, item in enumerate(collection)
            }

            if operation.action == "add":
                validated = _validated_item(operation.scope, operation.changes)
                if operation.scope == "combination":
                    working[key] = collection + [validated]
                else:
                    working[key] = assign_ids(
                        collection + [validated], prefix,
                        {str(item.get("item_id")) for item in collection},
                    )
                continue

            target = operation.target_item_id
            if target not in by_id:
                raise ValueError(f"目标ID不存在: {target}")
            if relevant_targets and target not in relevant_targets:
                raise ValueError(f"不得修改与当前问题无关的对象: {target}")
            if operation.action == "delete":
                collection.pop(by_id[target])
                continue
            merged = dict(collection[by_id[target]])
            merged.update(operation.changes)
            validated = _validated_item(operation.scope, merged)
            if operation.scope != "combination":
                validated["item_id"] = target
            collection[by_id[target]] = validated

        for scope, (key, _, _) in COLLECTIONS.items():
            for item in working[key]:
                _validated_item(scope, item)
        requested_combinations = working["sales_combinations"]
        normalized_combinations = normalize_sales_combinations(requested_combinations)
        requested_ids = [item.get("combination_id") for item in requested_combinations]
        normalized_ids = [item.get("combination_id") for item in normalized_combinations]
        if len(requested_combinations) != len(normalized_combinations) or requested_ids != normalized_ids:
            raise ValueError("组合Patch不是从S1开始的连续合法台账")
        working["sales_combinations"] = normalized_combinations
    except Exception as exc:
        return {}, False, str(exc)

    return working, True, "事务式Patch已全部应用"
