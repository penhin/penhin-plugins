import pytest

from penhin_knowledge import AccessScope, KnowledgeService, KnowledgeSource


@pytest.fixture
async def service(tmp_path):
    async with KnowledgeService(tmp_path / "index.sqlite3") as service:
        yield service


@pytest.fixture
def source():
    return KnowledgeSource.from_text(
        "guide",
        "部署说明",
        "# 部署\n数据库备份每天凌晨执行。\nAPI配置需要重启服务。\n",
        uri="docs/deploy.md",
    )


@pytest.fixture
def scope():
    return AccessScope("project", ("guide",))
