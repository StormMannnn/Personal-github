"""CLI entry point for the independent PPI loop agent."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

import numpy as np
import pandas as pd

from .compare_export import export_standard_output
from .runner import print_token_summary, run_all


def clean_dataframe(frame: pd.DataFrame) -> pd.DataFrame:
    # 数据清理:把 bus_date 列统一转成日期,并把字符串 "nan" 替换为真正的 NaN,
    # 避免 Excel 按文本读入后出现假字符串,影响后续 join 与数值判断。
    result = frame.copy()
    for column in result.columns:
        if column == "bus_date":
            result[column] = pd.to_datetime(result[column]).dt.date
        result[column] = result[column].replace("nan", np.nan)
    return result


def parse_args() -> argparse.Namespace:
    # 解析命令行参数:--source 输入Excel、--word-package 词袋Excel、--output 输出路径,
    # 三个都必填,缺任一参数会直接报错退出。
    parser = argparse.ArgumentParser(description="PPI Loop Engineering Agent")
    parser.add_argument("--source", required=True, help="输入Excel路径")
    parser.add_argument("--word-package", required=True, help="词袋Excel路径")
    parser.add_argument("--output", required=True, help="业务Excel输出路径")
    return parser.parse_args()


async def main() -> None:
    # CLI 主流程:读入两个 Excel(全部按文本读,再统一清理)→ 跑完整 PPI 循环
    # → 导出标准结果 → 打印 token 汇总;output_df 是循环的中间产物。
    args = parse_args()
    source = Path(args.source).resolve()
    word_package_path = Path(args.word_package).resolve()
    output_path = Path(args.output).resolve()
    raw_data = clean_dataframe(pd.read_excel(source, dtype="str"))
    word_package = clean_dataframe(pd.read_excel(word_package_path, dtype="str"))
    output_df, token_stats, _ = await run_all(raw_data, word_package)
    standard = export_standard_output(raw_data, output_df, output_path)
    print(f"业务结果已导出: {output_path} (行数: {len(standard)})")
    print_token_summary(token_stats)


if __name__ == "__main__":
    asyncio.run(main())

