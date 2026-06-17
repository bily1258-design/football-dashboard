#!/usr/bin/env python3
"""tools.py — 扣子内置工具的Termux兼容层

在扣子平台上，search_web 等是内置工具；
在Termux/本地环境，提供空实现避免import报错。
"""


def search_web(query_list=None, response_length="medium", **kwargs):
    """搜索网页（Termux兼容层：返回空结果）

    在扣子平台上会调用真实搜索API，
    在Termux上返回空列表，基本面分析会跳过搜索环节。
    """
    print(f"[tools] search_web: Termux环境，跳过搜索 ({query_list})")
    return []


def fetch_web(urls=None, response_length="long", **kwargs):
    """获取网页内容（Termux兼容层：返回空）"""
    print(f"[tools] fetch_web: Termux环境，跳过 ({urls})")
    return []
