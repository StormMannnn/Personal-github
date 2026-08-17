# PPI Loop Engineering Agent

## Code reading guide

若要从入口、状态机、数据契约到各节点逐步读懂整套代码，请先阅读
[`CODE_READING_TUTORIAL.md`](CODE_READING_TUTORIAL.md)。教程包含核心控制链逐行解释、
逐文件阅读顺序、单图断点调试方法、测试导航和四周学习计划。

`ppi_loop` is an independent refactor of `ppi_langgraph`. The original package is not imported
or modified. The runtime uses strict Maker–Checker separation:

- Qwen is the only Maker. It creates initial product/benefit records and repair patches.
- Kimi is a pure Checker. Product verification uses three independent calls and contracts:
  sales-combination audit, full-image product coverage/field audit, then immutable Qwen-item
  scoring. A failed earlier layer short-circuits the later layers.
- Python gates validate schemas, apply patches transactionally, control retries and declare the
  terminal state.
- Unverified candidates never enter the business Excel output.

## Environment

Python 3.11 is required. Install the platform requirement file, then provide keys through the
environment; keys are never stored in this project.

```powershell
$env:DASHSCOPE_API_KEY="..."
$env:SILICONFLOW_API_KEY="..."
$env:LANGGRAPH_STRICT_MSGPACK="true"
```

Optional controls:

```text
PPI_LOOP_STATE_DIR
PPI_LOOP_DIAGNOSTICS_DIR
PPI_IMG_CACHE_DIR
PPI_MAX_PRODUCT_ITERATIONS   default 2
PPI_MAX_BENEFIT_ITERATIONS   default 1
PPI_NO_PROGRESS_LIMIT        default 2
PPI_CONCURRENCY
PPI_CHECKER_CONCURRENCY
```

## Run

Run from the outer `ppi_Loop` directory:

```powershell
python -m ppi_loop.main `
  --source "C:\Product_PPI\source\input.xlsx" `
  --word-package "C:\Product_PPI\source\word_package\work_package_optimised.xlsx" `
  --output "C:\Product_PPI\output\ppi_loop_output.xlsx"
```

The business workbook remains compatible with the legacy standard export. Only `VERIFIED`
records populate extraction columns. Diagnostic JSONL and a run summary are written under
`output/ppi_loop_diagnostics` by default.

SQLite checkpoints are stored under `%LOCALAPPDATA%\Product_PPI\ppi_loop` on Windows and
`cache/ppi_loop` on macOS. The thread ID includes loop version, image, brand, title, labels and
label mapping. A terminal checkpoint is reused; an interrupted nonterminal checkpoint resumes
from its next LangGraph node.

## Tests

```bash
PYTHONPATH=. python -m pytest -q tests
```
