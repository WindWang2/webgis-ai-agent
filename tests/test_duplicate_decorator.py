"""Test: no duplicate decorators in report_service."""
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
