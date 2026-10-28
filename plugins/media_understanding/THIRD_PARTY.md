# 依赖与模型来源

插件源码为 MIT。`requirements-local.lock` 固定本地解码及可选 HTTP 后端的直接/传递依赖，并包含包文件 SHA-256；不打包模型权重或第三方二进制到本插件 wheel。

| 直接依赖 | 固定版本 | 来源 | 包自身许可 |
| --- | --- | --- | --- |
| Pillow | 13.0.0 | [Pillow](https://github.com/python-pillow/Pillow) | MIT-CMU |
| pypdfium2 | 5.14.0 | [pypdfium2](https://github.com/pypdfium2-team/pypdfium2) | BSD-3-Clause / Apache-2.0；附带 PDFium 构建依赖许可 |
| PyAV | 19.0.2 | [PyAV](https://github.com/PyAV-Org/PyAV) | BSD-3-Clause；FFmpeg/codec 许可取决于所用构建 |
| HTTPX | 0.28.1 | [HTTPX](https://github.com/encode/httpx) | BSD-3-Clause |

这里记录的是已安装包的许可元数据；再分发原生依赖时还需保留其 wheel 中的许可证和依赖构建声明。尤其 PDFium、FFmpeg 及编解码器不能统一按本插件 MIT 许可处理。安装器从上游 wheel 取得这些依赖，本仓库不复制或重新标记其版权。

Ollama 服务与视觉模型由宿主另行安装和管理。插件不绑定具体模型；`model` 必须显式指定，可用 `expected_digest` 固定本机模型摘要。宿主需记录模型来源、版本/摘要、许可证、运行平台及真实样本质量结果，才可声称具体模型配置完成验收。

更新依赖锁：

```sh
python -m piptools compile plugins/media_understanding/pyproject.toml --extra local --extra ollama --generate-hashes --strip-extras --no-header --no-annotate --output-file plugins/media_understanding/requirements-local.lock
```
