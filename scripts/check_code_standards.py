"""检查 OpenSpec 提案要求的代码规模限制。"""

from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAX_FILE_LINES = 800
MAX_FUNCTION_LINES = 80
PYTHON_DIRS = [ROOT]
ARB_DIR = ROOT / "frontend" / "lib" / "l10n"


def _python_files() -> list[Path]:
    """返回需要遵守代码规模限制的 Python 源文件。"""
    files: list[Path] = []
    for source_dir in PYTHON_DIRS:
        if source_dir.exists():
            files.extend(
                path for path in source_dir.rglob("*.py") if "__pycache__" not in path.parts
            )
    return sorted(files)


def _line_count(path: Path) -> int:
    """使用简单换行切分统计源文件行数。"""
    return len(path.read_text(encoding="utf-8").splitlines())


def _function_spans(path: Path) -> list[tuple[str, int]]:
    """通过 Python AST 行号元数据计算函数跨度。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    spans: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.end_lineno:
            spans.append((node.name, node.end_lineno - node.lineno + 1))
    return spans


def _arb_keys(path: Path) -> set[str]:
    """读取 ARB 文件并返回消息 key 集合（排除 @ 前缀的元数据）。"""
    if not path.exists():
        return set()
    data = json.loads(path.read_text(encoding="utf-8"))
    return {key for key in data if not key.startswith("@") and key != "@@locale"}


def _check_arb_keys() -> list[str]:
    """校验 zh 和 en ARB 的 key 集合完全一致。"""
    zh = _arb_keys(ARB_DIR / "app_zh.arb")
    en = _arb_keys(ARB_DIR / "app_en.arb")
    if zh == en:
        return []
    only_zh = sorted(zh - en)
    only_en = sorted(en - zh)
    failures: list[str] = []
    if only_zh:
        failures.append(f"app_zh.arb 有但 app_en.arb 缺失的 key: {only_zh}")
    if only_en:
        failures.append(f"app_en.arb 有但 app_zh.arb 缺失的 key: {only_en}")
    return failures


def main() -> int:
    """运行全部代码规范检查，并打印可操作的失败信息。"""
    failures: list[str] = []
    for path in _python_files():
        lines = _line_count(path)
        if lines > MAX_FILE_LINES:
            failures.append(f"{path}: {lines} lines exceeds {MAX_FILE_LINES}")
        for name, span in _function_spans(path):
            if span > MAX_FUNCTION_LINES:
                failures.append(f"{path}:{name} spans {span} lines exceeds {MAX_FUNCTION_LINES}")
    failures.extend(_check_arb_keys())
    if failures:
        print("\n".join(failures))
        return 1
    print("Code standards passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
