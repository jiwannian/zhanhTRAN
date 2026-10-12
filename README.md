# zhanhTRAN

<img src="icons/icon-128.png" width="64" height="64" alt="zhanhTRAN">

Zotero 自定义模型翻译插件。使用本地 Python 解析 PDF、扫描件和图片，通过用户配置的供应商翻译，并输出保持源布局的译文与左右并排双语文件。

## 功能

- 多供应商：名称、Base URL、API 密钥、模型、目标语言；支持 OpenAI 兼容与 Anthropic 原生协议。
- 本地 PDF 文本、OCR、图片文字与表格解析，结构化翻译和公式保护。
- 译文、双语可独立开关；双语可选择原文左或译文左。
- PDF 输出仍为 PDF；图片保留原扩展名。不创建笔记或额外 Markdown/HTML 附件。
- 点击翻译直接开始，可移动进度窗、取消任务、中断最多自动重试 5 次。
- 输出保存在源文件旁并加入相同父条目和文库。个人文库使用链接附件；群组文库导入文件，Zotero 管理的附件副本不保证仍位于源目录。

## 安装

从 [Releases](https://github.com/jiwannian/zhanhTRAN/releases) 下载 XPI，在 Zotero 的“工具 → 插件”中选择“从文件安装”。声明兼容 Zotero 9–10；已有隔离环境验证为 Zotero 10.0.6，Zotero 9 尚未实机验证。插件采用手动更新，不后台更新。

安装 Python 3.10 或更高版本，建议为解析器建立独立环境：

```powershell
py -3.10 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Linux/macOS：

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

在 zhanhTRAN 设置中填写该环境 Python 可执行文件的**绝对路径**，配置供应商、Base URL（不是完整接口路径）、模型和密钥，保存后进行联通测试。默认地址 `https://provider.invalid` 是不可用模板，必须替换。公开版只使用自己的设置命名空间，不自动读取其他插件的配置。

选中 PDF 或图片附件，或含此类附件的条目，通过右键菜单或工具菜单选择 zhanhTRAN 翻译。源文件不会被覆盖；同名结果已存在时会使用安全的输出路径。

## 隐私与边界

解析/OCR 在本地执行；提取的文本会发送至所选模型供应商。密钥使用 Zotero 登录管理器保存，不放入明文偏好或安装包。请自行确认供应商的数据政策与使用权限，敏感文档不要发送给未经批准的服务。

不附带 Python 环境或模型服务，不承诺完全离线。OCR、复杂表格和公式存在识别边界；保留布局并不保证字体或每个像素相同。普通段落空间不足时仅借用紧邻安全空白；仍放不下会停止导出，而不是截断内容或交付相互覆盖的 PDF。

## 开发与测试

需要 Node.js 22+、Python 3.10+：

```sh
npm ci
npx playwright install chromium
python -m pip install -r requirements-dev.txt
npm test
python -m coverage run --source=src -m unittest discover -s tests -p '*_test.py'
python -m coverage report --fail-under=80
python tools/package.py
```

JS/Python 桥接测试使用 `ZHANHTRAN_PYTHON` 指定已安装依赖的 Python，默认 `python`。OCR 测试需要 CJK 字体：Windows 默认查找微软雅黑，Linux 查找文泉驿正黑；也可设置 `ZHANHTRAN_TEST_FONT`。Ubuntu 可安装 `fonts-wqy-zenhei`。`ZHANHTRAN_CHROME` 可指定用于 UI 测试的 Chromium/Chrome，否则使用 Playwright Chromium。

测试只使用合成输入及模拟供应商。单元/集成和浏览器测试不是真实供应商翻译质量或 Zotero 原生端到端测试的替代品。构建结果为 `dist/zhanhTRAN-1.0.1.xpi`，相同源码与 Python 压缩运行环境可重现相同包字节。

Windows 原生端到端验收可在构建后运行 `python tools/run-native.py`，默认查找标准安装路径的 Zotero，也可用 `ZHANHTRAN_ZOTERO` 指定。它创建独立 profile、独立文库和回环模拟供应商，不安装到日常配置。运行可能打开测试窗口，结果与截图仅保存在被 Git 忽略的 `qa/`、`logs/`。

## 许可证

项目采用 [GNU AGPL v3](LICENSE)。第三方 parser 保留 MIT 许可证；Python 依赖由使用者安装，其许可证见 [第三方声明](THIRD_PARTY_NOTICES.md)。分发修改版时请遵守相应的源码提供及许可证义务。
