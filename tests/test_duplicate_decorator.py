"""Test: no duplicate decorators in report_service.

审计 T3 注释：这是源码结构检查（AST），不是行为测试。保留是因为：
(1) 它防御的 bug（@staticmethod 重复）会让函数在 import 时就崩，
    行为测试和 AST 检查效果相同；
(2) 转行为测试需要枚举 report_service 的每个函数并调用，但其中
    PDF 生成依赖外部库（reportlab），调用成本高且不稳定。
AST 检查在这里是更务实的选择。
"""
import ast


def test_no_duplicate_staticmethod():
    """No function should have duplicate @staticmethod decorator."""
    with open("app/services/report_service.py") as f:
        source = f.read()

    tree = ast.parse(source)

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            # Check decorator list for duplicates
            decorator_names = []
            for dec in node.decorator_list:
                if isinstance(dec, ast.Name):
                    decorator_names.append(dec.id)
            if len(decorator_names) != len(set(decorator_names)):
                dupes = [n for n in decorator_names if decorator_names.count(n) > 1]
                pytest.fail(
                    f"Function '{node.name}' at line {node.lineno} has duplicate decorators: {dupes}"
                )
