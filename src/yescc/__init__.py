"""YesCC（Yes Compiler Collection）。

合法代码走原工具链；出错或自然语言输入时，在预算内修复并交真实工具链验证。
"""

from importlib.metadata import version

__version__ = version("yescc")

__all__ = ["__version__"]
