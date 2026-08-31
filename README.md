# JSSH V1.6 R6 RAG

面向 JSSH 具身手环 V1.6 R6 的本地、只读研发知识检索工具。首期只索引
`C:\work1\JSZN\ESP32_S3` 中 Git 跟踪的 `1.6_R6` 文本资料，回答保留版本、
源提交、文件哈希、证据等级和行号引用。

## 安全边界

- 不访问串口，不烧录、不修改配置、不控制设备。
- 不自动回退到 V1.6 或其他硬件版本。
- 构建、仿真、Host、QEMU 和源码检查不等于真机验收。
- 没有目标版本证据时明确拒答。

## 运行

```powershell
python -m pip install -e .
jssh-rag index --version 1.6_R6
jssh-rag search --version 1.6_R6 --query "ADS1298 DRDY如何连接"
jssh-rag ask --version 1.6_R6 --query "当前R6是否已经完成真机验收"
```

本地数据库默认写入 `data/jssh_rag.sqlite3`，该目录不会进入 Git。
