"""Teste de fumo: o ambiente tem Python 3.12+ e as dependências principais."""

import importlib
import sys

import pytest


def test_python_version():
    assert sys.version_info >= (3, 12)


@pytest.mark.parametrize("module", ["pandas", "requests", "duckdb", "pyarrow", "statsmodels"])
def test_dependency_imports(module):
    importlib.import_module(module)
