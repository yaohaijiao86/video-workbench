# 贡献指南

欢迎提交可复现的问题和小范围改进。提 issue 时写明操作系统、Python 版本、视频容器格式、外部工具版本、复现步骤与脱敏日志；不要提交源视频、API Key、模型权重或个人数据。

提交前运行：

    python -m unittest discover -s tests -v
    python -m compileall -q src examples
    node --check src/video_workbench/static/app.js

外部工具必须保持用户自行安装、配置与下载的模式。新适配器应定义输入、输出 JSON 和错误行为，并提供可离线运行的单元测试。
