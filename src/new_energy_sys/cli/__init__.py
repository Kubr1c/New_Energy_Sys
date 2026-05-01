"""CLI 命令行入口包。

模块设计原则：
- 每个 CLI 模块对应项目流水线的一个 Stage
- 统一采用 argparse 解析参数，load_config 加载运行时配置
- 所有产物落盘至 processed_dir，格式为 CSV/JSON/Markdown

本模块对应项目的命令行工具层，注册所有子命令。
"""
