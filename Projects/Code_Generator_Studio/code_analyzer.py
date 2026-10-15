"""
Ultron Code Generation Studio - Code Analyzer
Parses Python code using AST to compute structural complexity, metrics, and quality insights.
"""

import ast
from typing import Dict, Any, List


class CodeMetricsVisitor(ast.NodeVisitor):
    def __init__(self):
        self.functions = []
        self.classes = []
        self.imports = []
        self.docstrings = 0
        self.decision_points = 0  # if, for, while, try, with, etc.

    def visit_FunctionDef(self, node):
        has_doc = ast.get_docstring(node) is not None
        if has_doc:
            self.docstrings += 1
        self.functions.append({
            "name": node.name,
            "lineno": node.lineno,
            "args": [arg.arg for arg in node.args.args],
            "has_docstring": has_doc
        })
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node):
        self.visit_FunctionDef(node)

    def visit_ClassDef(self, node):
        has_doc = ast.get_docstring(node) is not None
        if has_doc:
            self.docstrings += 1
        self.classes.append({
            "name": node.name,
            "lineno": node.lineno,
            "has_docstring": has_doc
        })
        self.generic_visit(node)

    def visit_Import(self, node):
        for alias in node.names:
            self.imports.append(alias.name)
        self.generic_visit(node)

    def visit_ImportFrom(self, node):
        module = node.module or ""
        for alias in node.names:
            self.imports.append(f"{module}.{alias.name}" if module else alias.name)
        self.generic_visit(node)

    def visit_If(self, node):
        self.decision_points += 1
        self.generic_visit(node)

    def visit_For(self, node):
        self.decision_points += 1
        self.generic_visit(node)

    def visit_While(self, node):
        self.decision_points += 1
        self.generic_visit(node)

    def visit_Try(self, node):
        self.decision_points += 1
        self.generic_visit(node)

    def visit_ExceptHandler(self, node):
        self.decision_points += 1
        self.generic_visit(node)


def analyze_python_code(code_string: str) -> Dict[str, Any]:
    """
    Performs static analysis on Python source code.
    """
    lines = code_string.splitlines()
    total_lines = len(lines)
    blank_lines = sum(1 for line in lines if not line.strip())
    comment_lines = sum(1 for line in lines if line.strip().startswith("#"))
    code_lines = total_lines - blank_lines - comment_lines

    try:
        tree = ast.parse(code_string)
        module_doc = ast.get_docstring(tree) is not None
        
        visitor = CodeMetricsVisitor()
        visitor.visit(tree)

        total_defs = len(visitor.functions) + len(visitor.classes)
        doc_coverage = (
            round(((visitor.docstrings + (1 if module_doc else 0)) / (total_defs + 1)) * 100, 1)
            if total_defs > 0 else (100.0 if module_doc else 0.0)
        )

        # Estimated cyclomatic score base is decision_points + 1
        cyclomatic_score = visitor.decision_points + 1

        if cyclomatic_score <= 5:
            complexity_level = "Low (Clean & Linear)"
            complexity_color = "success"
        elif cyclomatic_score <= 12:
            complexity_level = "Moderate (Standard Structure)"
            complexity_color = "warning"
        else:
            complexity_level = "High (Consider Modularizing)"
            complexity_color = "danger"

        return {
            "valid_syntax": True,
            "total_lines": total_lines,
            "code_lines": code_lines,
            "comment_lines": comment_lines,
            "blank_lines": blank_lines,
            "functions_count": len(visitor.functions),
            "functions": visitor.functions,
            "classes_count": len(visitor.classes),
            "classes": visitor.classes,
            "imports_count": len(visitor.imports),
            "imports": visitor.imports,
            "docstring_coverage_pct": doc_coverage,
            "cyclomatic_score": cyclomatic_score,
            "complexity_level": complexity_level,
            "complexity_color": complexity_color,
            "error": None
        }
    except SyntaxError as e:
        return {
            "valid_syntax": False,
            "total_lines": total_lines,
            "code_lines": code_lines,
            "comment_lines": comment_lines,
            "blank_lines": blank_lines,
            "functions_count": 0,
            "classes_count": 0,
            "imports_count": 0,
            "docstring_coverage_pct": 0,
            "cyclomatic_score": 0,
            "complexity_level": "Syntax Error",
            "complexity_color": "danger",
            "error": f"SyntaxError at line {e.lineno}: {e.msg}"
        }
    except Exception as ex:
        return {
            "valid_syntax": False,
            "total_lines": total_lines,
            "error": str(ex)
        }
