# 按插件发行

每个插件独立版本、独立 wheel / sdist，标签采用 `<id>/v<version>`。当前没有发布标签、上传 Release 或签名发行；ASR `0.1.0` 仍为开发状态。

## 构建与安装

```sh
python -m build plugins/asr
python -m pip install --require-hashes -r plugins/asr/requirements-zipformer.lock
python -m pip install --no-deps plugins/asr/dist/penhin_plugin_asr-0.1.0-py3-none-any.whl
```

wheel 包含服务、Penhin 适配、模型下载工具和模型锁，不包含模型权重或录音。源码仓库根目录不是单个可安装插件。模型需通过显式 provisioning 命令准备，具体操作见 [ASR README](../plugins/asr/README.md)。Penhin 内核由宿主单独安装。

## 发布前的必要证据

媒体理解插件独立构建：`python -m build plugins/media_understanding`。先用其 `requirements-local.lock` 安装有摘要的运行依赖，再安装 wheel；具体后端/模型来源见[媒体依赖说明](../plugins/media_understanding/THIRD_PARTY.md)。模型由宿主显式准备，插件不隐式下载。媒体理解的发布门槛还包括 [#41](https://github.com/penhin/penhin-code/issues/41) 要求的首次启用、失败阻断，以及宿主原始媒体路由验收。

1. 在干净环境构建，并在源码目录之外验证 wheel 导入、entry point 与命令行；记录 wheel 摘要及对应源码提交。
2. 运行基础测试、真实 Penhin 集成及真实模型样本；记录操作系统、架构、Python、内核提交和模型摘要。
3. 校验固定依赖锁、模型锁和依赖/模型许可；依赖锁改变后重新执行真实识别。
4. 对接宿主的安装验证、授权、取消、禁用、reload 与退出；按住说话必须证明未提交时没有模型请求。
5. 完整范围验收后才创建标签和 Release、更新目录为 `released`。插件包可安装不等于 [#42](https://github.com/penhin/penhin-code/issues/42) 的端到端功能完成。

旧方案要求的 `penhin-plugin.yaml`、`penhin-artifact.json` 和固定 HMAC 信任根不是当前 Penhin 的接口。若 `penhin-code` 仍需独立 Artifact 交付，应在该宿主的适配与发行流程中落实可信验证，不能在本包伪造官方签名来替代。
