# 参与开发

插件使用 `plugins/<id>/src/<package>/` 标准 Python 布局。业务服务可独立调用，Penhin 适配层的工厂返回内核 `Plugin`，由宿主注入附件访问与配置；不要复制旧版 `PluginHost` 或安装器协议。

## 本地开发

在仓库根目录创建并激活 Python 3.11+ 虚拟环境，然后执行：

```sh
python -m pip install -e "./plugins/asr[zipformer,dev]"
python -m pip install ../penhin
python -m pytest plugins/asr/tests -q
python -m ruff check plugins/asr/src plugins/asr/tests
python -m ruff format --check plugins/asr/src plugins/asr/tests
python -m build plugins/asr
python scripts/check_repository.py
```

`../penhin` 指匹配的内核源码 checkout，已核对提交见[兼容性记录](docs/compatibility.md)。当前不能假定 PyPI 存在该包。没有内核时，基础服务测试仍能运行，集成模块明确跳过；交付前必须安装真实内核重跑。

设置 `PENHIN_ASR_MODEL_DIR` 后再运行 `plugins/asr/tests/test_model.py` 进行真实样本验证；使用[固定模型准备命令](plugins/asr/README.md#安装与真实音频体验)。不要把跳过真实模型测试的运行写成识别通过。

## 添加插件

媒体理解使用同一布局，开发命令见[媒体理解验证说明](plugins/media_understanding/README.md#验证)。两个插件的测试分别运行，避免跨插件测试模块同名冲突。模型推理测试必须显式配置本地模型；基础 CI 不自动下载或启动模型。

1. 在 `plugins/<id>/` 建立包与 README，在 `plugins.json` 登记，ID 使用小写字母、数字、下划线。
2. 状态使用 `planned`、`in-development` 或 `released`；进入实现阶段需有 `pyproject.toml`，声明 `penhin.plugins` 入口。
3. 分开说明宿主直接调用、Agent 工具调用和宿主交互职责。输入输出与权限边界需要有行为测试，尤其取消、超时和资源所有权。
4. 原生运行时和模型必须记录来源、固定版本、摘要和许可。模型和个人音频不提交，推理阶段不隐式下载。
5. 更新 CI、[兼容性](docs/compatibility.md)及[发行说明](docs/releases.md)。发布 wheel 不能代替宿主完整产品验收。

## 依赖锁

ASR 后端的完整安装依赖使用带 SHA-256 的 `requirements-zipformer.lock`，在干净环境用 `pip install --require-hashes -r ...` 验证。更新时运行：

```sh
python -m pip install pip-tools
python -m piptools compile plugins/asr/pyproject.toml --extra zipformer --generate-hashes --strip-extras --no-header --no-annotate --output-file plugins/asr/requirements-zipformer.lock
```

锁文件包含平台 wheel 的摘要；当前实测平台与 Python 版本必须单独记录，不能仅据锁文件声称所有平台已验证。
