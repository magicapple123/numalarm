"""numalarm 核心拨打逻辑层。

- state_detector：进程与窗口状态检测
- qq_controller：唤起面板 / 搜索好友 / 聊天窗控制（纯 UI 模拟）
- call_executor：拨打流程编排与执行（互斥、防抖、统一状态码）
"""
