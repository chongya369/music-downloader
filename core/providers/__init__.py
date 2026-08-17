"""Providers 包：多平台音乐 API 统一接口

仅导出 get_provider，导入无副作用。
"""

from .registry import get_provider

__all__ = ["get_provider"]
