"""compare_export — 标准协作结果与历史双模型对比导出。

保留从 notebook cells 10-14 迁移的兼容函数。
"""

import pandas as pd
import numpy as np
from pathlib import Path

from openpyxl.styles import Alignment, Font
from openpyxl.utils import get_column_letter


STANDARD_BASE_COLS = [
    'row_id', 'bus_date', 'brand_name_product', 'item_id',
    'product_title', 'image_url', 'label', 'type',
    'RSP', 'RSP with PWP', 'Actual Price', 'PPI',
]
STANDARD_MODEL_COLS = [
    '组合ID', '类型', '名称', '规格', '数量', '金额', '使用条件',
]
STANDARD_REVIEW_COLS = [
    '类型准确', '到手价', '主品名称', '主品规格', '主品数量', '主品条数',
    '赠品名称', '赠品规格', '赠品数量', '赠品条数', 'PPI准确性', '备注',
    'RapidFuzz匹配分数', 'VLM测评分数',
]
STANDARD_TAIL_COLS = ['图片复杂度', '复杂点', 'Prompt版本', '使用模型']
STANDARD_OUTPUT_COLS = (
    STANDARD_BASE_COLS
    + STANDARD_MODEL_COLS
    + STANDARD_REVIEW_COLS
    + STANDARD_TAIL_COLS
)
_BLANK_SOURCE_METRICS = ['RSP', 'RSP with PWP', 'Actual Price', 'PPI']
_MODEL_SCORE_COLS = ['RapidFuzz匹配分数', 'VLM测评分数']


def build_standard_output(raw_data: pd.DataFrame,
                          output_df: pd.DataFrame) -> pd.DataFrame:
    # 组装标准导出列:以原表为基,按 image_url left-join 模型提取结果,
    # 补齐缺失列与空值,保证列序和列名与标准模板完全一致。
    """将单一多模型协作结果 left-join 回输入原表。

    内部 output_df 仍保留单位等完整字段；本函数只构造最终标准导出列。
    """
    base = raw_data.copy()
    base.columns = base.columns.astype(str).str.strip()
    base = base.loc[:, ~base.columns.str.match(r'^Unnamed:')].copy()

    for col in STANDARD_BASE_COLS:
        if col not in base.columns:
            base[col] = np.nan
    base = base[STANDARD_BASE_COLS].copy()
    base[_BLANK_SOURCE_METRICS] = np.nan
    base['_source_order'] = np.arange(len(base))
    base['_join_url'] = base['image_url'].fillna('').astype(str).str.strip()

    extracted_cols = STANDARD_MODEL_COLS + _MODEL_SCORE_COLS
    extracted = output_df.copy()
    for col in ['主图URL'] + extracted_cols:
        if col not in extracted.columns:
            extracted[col] = np.nan
    extracted = extracted[['主图URL'] + extracted_cols].copy()
    extracted['_join_url'] = extracted['主图URL'].fillna('').astype(str).str.strip()
    extracted = extracted[extracted['_join_url'] != ''].copy()
    extracted['_item_order'] = extracted.groupby('_join_url', sort=False).cumcount()
    extracted = extracted.drop(columns=['主图URL'])

    result = base.merge(extracted, on='_join_url', how='left', sort=False)
    result['_item_order'] = result['_item_order'].fillna(-1)
    result = result.sort_values(
        ['_source_order', '_item_order'], kind='stable'
    ).reset_index(drop=True)

    for col in STANDARD_REVIEW_COLS:
        if col not in result.columns:
            result[col] = np.nan
    for col in STANDARD_TAIL_COLS:
        result[col] = np.nan

    return result[STANDARD_OUTPUT_COLS]


def export_standard_output(raw_data: pd.DataFrame,
                           output_df: pd.DataFrame,
                           output_path: str | Path) -> pd.DataFrame:
    # 标准导出:先 build_standard_output,再写 Excel;前两行做分组表头
    # (基础列/模型列/检验列/尾列),冻结前两行,同时返回结果 DataFrame。
    """导出参考旧版 left-join 结构的两行分组表头 Excel。"""
    result = build_standard_output(raw_data, output_df)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with pd.ExcelWriter(output_path, engine='openpyxl') as writer:
        result.to_excel(
            writer, sheet_name='Sheet1', index=False, header=False, startrow=2
        )
        sheet = writer.sheets['Sheet1']

        base_end = len(STANDARD_BASE_COLS)
        model_start = base_end + 1
        model_end = base_end + len(STANDARD_MODEL_COLS)
        review_start = model_end + 1
        review_end = model_end + len(STANDARD_REVIEW_COLS)
        tail_start = review_end + 1

        for col_idx, name in enumerate(STANDARD_BASE_COLS, start=1):
            sheet.cell(1, col_idx, name)
            sheet.merge_cells(
                start_row=1, start_column=col_idx,
                end_row=2, end_column=col_idx,
            )

        sheet.cell(1, model_start, 'PPI_LANGGRAPH')
        sheet.merge_cells(
            start_row=1, start_column=model_start,
            end_row=1, end_column=model_end,
        )
        for offset, name in enumerate(STANDARD_MODEL_COLS):
            sheet.cell(2, model_start + offset, name)

        sheet.cell(1, review_start, 'PPI_LANGGRAPH - 结果检验')
        sheet.merge_cells(
            start_row=1, start_column=review_start,
            end_row=1, end_column=review_end,
        )
        for offset, name in enumerate(STANDARD_REVIEW_COLS):
            sheet.cell(2, review_start + offset, name)

        for offset, name in enumerate(STANDARD_TAIL_COLS):
            col_idx = tail_start + offset
            sheet.cell(1, col_idx, name)
            sheet.merge_cells(
                start_row=1, start_column=col_idx,
                end_row=2, end_column=col_idx,
            )

        header_font = Font(bold=True)
        header_alignment = Alignment(horizontal='center', vertical='center')
        for row in sheet.iter_rows(
            min_row=1, max_row=2, min_col=1, max_col=len(STANDARD_OUTPUT_COLS)
        ):
            for cell in row:
                cell.font = header_font
                cell.alignment = header_alignment

        sheet.row_dimensions[2].height = 27
        sheet.freeze_panes = 'A3'

        # 与参考文件一致，不强制调整数据列宽。
        for col_idx in range(1, len(STANDARD_OUTPUT_COLS) + 1):
            sheet.column_dimensions[get_column_letter(col_idx)].bestFit = False

    return result


def merge_dual_model(output_df: pd.DataFrame,
                     model_key_a: str = "kimi",
                     model_key_b: str = "qwen") -> pd.DataFrame:
    # 遗留双模型对比:把含 model 列的 output_df 按 kimi/qwen 拆两份,
    # 以图URL+行号外连接,两模型的 item 列并排输出;新流程已被标准导出取代。
    """双模型横向对比 merge。

    输入: output_df 含 "model" 列(值如 "kimi-k2.6"/"qwen3.7-plus")
    输出: 每张图一行,两个模型的 item_cols 并排(kimi_xxx / qwen_xxx)
    """
    df = output_df.copy()
    df.columns = df.columns.astype(str).str.strip()
    df['主图URL'] = df['主图URL'].astype(str).str.strip()
    df['model'] = df['model'].astype(str).str.strip()

    item_cols = ['类型', '名称', '规格', '单位', '数量', '金额', '使用条件']
    item_cols = [col for col in item_cols if col in df.columns]

    # 模型 key 分类
    df['model_key'] = np.select(
        [
            df['model'].str.contains('kimi', case=False, na=False),
            df['model'].str.contains('qwen', case=False, na=False),
        ],
        [model_key_a, model_key_b],
        default='other',
    )

    df = df[df['model_key'].isin([model_key_a, model_key_b])].copy()

    df_a = df[df['model_key'].eq(model_key_a)][['主图URL'] + item_cols].copy()
    df_b = df[df['model_key'].eq(model_key_b)][['主图URL'] + item_cols].copy()

    df_a['_row_no'] = df_a.groupby('主图URL').cumcount()
    df_b['_row_no'] = df_b.groupby('主图URL').cumcount()

    df_a = df_a.rename(columns={col: f'{model_key_a}_{col}' for col in item_cols})
    df_b = df_b.rename(columns={col: f'{model_key_b}_{col}' for col in item_cols})

    merged = pd.merge(df_a, df_b, on=['主图URL', '_row_no'], how='outer')
    merged = merged.sort_values(['主图URL', '_row_no']).reset_index(drop=True)

    final_cols = ['主图URL'] + \
        [f'{model_key_a}_{col}' for col in item_cols] + \
        [f'{model_key_b}_{col}' for col in item_cols]
    merged = merged[final_cols]

    return merged


def left_join_with_source(merged_df: pd.DataFrame,
                          source_path: str,
                          sheet_name: int | str = 0) -> pd.DataFrame:
    # 遗留兼容:把双模型对比结果按 image_url ↔ 主图URL left-join 回原表 Excel,
    # 返回带原始上下文行的完整 DataFrame。
    """left-join 原表。

    Args:
        merged_df: 双模型对比 merge 后的 DataFrame
        source_path: 原始数据 Excel 路径
    """
    base = pd.read_excel(source_path, sheet_name=sheet_name, header=0)
    base.columns = base.columns.astype(str).str.strip()
    merged_df.columns = merged_df.columns.astype(str).str.strip()

    base['image_url'] = base['image_url'].astype(str).str.strip()
    merged_df['主图URL'] = merged_df['主图URL'].astype(str).str.strip()

    result = base.merge(merged_df, left_on='image_url', right_on='主图URL', how='left')
    result = result.drop(columns=['主图URL'])
    return result


def export(output_df: pd.DataFrame,
           merged_path: str | None = None,
           left_join_source: str | None = None,
           left_join_output: str | None = None) -> None:
    # 遗留兼容入口:按需导出双模型对比(merged_path)与 left-join 原表结果;
    # 新流程由 export_standard_output 取代,本函数仅为老调用方保留。
    """导出: 双模型对比 + left-join 原表。

    Args:
        output_df: runner 输出的原始 DataFrame
        merged_path: 双模型对比结果导出路径(可选)
        left_join_source: 原表路径(optional)
        left_join_output: left-join 结果导出路径(optional)
    """
    if merged_path:
        merged = merge_dual_model(output_df)
        Path(merged_path).parent.mkdir(parents=True, exist_ok=True)
        merged.to_excel(merged_path, index=False)
        print(f"双模型对比已导出: {merged_path}")

    if left_join_source and left_join_output:
        merged = merge_dual_model(output_df)
        result = left_join_with_source(merged, left_join_source)
        Path(left_join_output).parent.mkdir(parents=True, exist_ok=True)
        result.to_excel(left_join_output, index=False)
        print(f"Left-join 已导出: {left_join_output}")
