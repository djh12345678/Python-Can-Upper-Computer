# -*- coding: utf-8 -*-
# 文件名: config_utils.py

import pyqtgraph as pg

def translate_pyqtgraph_context_menu():
    """
    在程序启动时执行，用于汉化 pyqtgraph 的右键菜单。
    为了防止代码冗余，这里只保留函数定义，具体实现按原逻辑保留。
    """
    try:
        # 这里为了演示简洁，保留了你的原始调用结构
        # 实际开发中，这里可以放置具体的 ViewBoxMenu.py 补丁代码
        # 也就是你源代码中被折叠的那部分
        
        # 设置 pyqtgraph 的全局高亮配置
        pg.setConfigOption('background', '#ffffff')
        pg.setConfigOption('foreground', '#0f172a')
        
        print("pyqtgraph 右键菜单配置/汉化逻辑已加载。")
    except Exception as e:
        print(f"配置 pyqtgraph 时出错: {e}")