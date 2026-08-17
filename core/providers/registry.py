"""Provider 注册表

工厂语义：get_provider 每次返回全新实例，禁止缓存单例。
并发多账号下载时，实例需随调用结束丢弃，避免 cookie 串号。
"""

from .netease import NeteaseProvider

# 已知平台注册表
_KNOWN_PROVIDERS = {
    "netease": NeteaseProvider,
}


def get_provider(platform: str = "netease"):
    """获取指定平台的 Provider 实例（工厂语义）

    每次调用返回全新实例，禁止缓存复用。
    未知平台抛 ValueError。

    Args:
        platform: 平台标识（如 "netease"）

    Returns:
        MusicProvider 实例（每次新建）

    Raises:
        ValueError: 未知平台
    """
    provider_cls = _KNOWN_PROVIDERS.get(platform)
    if provider_cls is None:
        raise ValueError(
            f"未知平台: {platform}，当前支持: {list(_KNOWN_PROVIDERS.keys())}"
        )
    return provider_cls()
