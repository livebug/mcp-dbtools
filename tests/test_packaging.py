"""打包与发布一致性测试。

用于防止"改了代码忘了改打包配置"的回归：

- `crypto.py` 直接使用 `cryptography`，若未在 `pyproject.toml` 声明，离线安装会缺依赖，
  `{ENC:...}` 密码解密报错（此前只靠 `mcp -> pyjwt[crypto]` 传递依赖，随时可能失效）。
- `pyproject.toml` 与 `__init__.py` 版本号不一致会导致打 tag 发布的版本对不上。
- 离线包只分发 `config/datasources.json.example`，示例落后于真实配置时内网拿不到新数据源样例。
- 离线安装脚本依赖 `python-version-required.txt` 做 Python 版本校验（离线 wheel 是 cp3xx 标签，
  不能跨 Python 版本安装），构建脚本必须生成该文件。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # Python 3.10（项目声明的下限）
    import tomli as tomllib  # type: ignore[no-redef]

import mcp_dbtools

ROOT = Path(__file__).resolve().parents[1]


def _project() -> dict:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]


def _pkg_name(requirement: str) -> str:
    """从依赖声明中取包名（去掉版本约束 / extras / 环境标记）。"""
    return re.split(r"[<>=!~\[;]", requirement, maxsplit=1)[0].strip().lower()


def test_runtime_dependencies_declared():
    deps = {_pkg_name(d) for d in _project()["dependencies"]}
    assert "cryptography" in deps, f"cryptography 必须显式声明：{sorted(deps)}"
    assert {"mcp", "jaydebeapi", "jpype1", "pydantic", "uvicorn", "starlette"} <= deps


def test_requires_python_declared():
    # mcp SDK 全部版本均要求 >=3.10（Python 3.8/3.9 无法安装依赖）
    assert _project()["requires-python"].replace(" ", "") == ">=3.10"


def test_version_consistent_with_package():
    assert _project()["version"] == mcp_dbtools.__version__


def test_datasources_example_covers_real_config():
    """离线包只分发 .example，示例必须覆盖真实配置里的全部数据源。"""
    example = json.loads(
        (ROOT / "config" / "datasources.json.example").read_text(encoding="utf-8")
    )
    real = json.loads((ROOT / "config" / "datasources.json").read_text(encoding="utf-8"))
    example_names = {ds["name"] for ds in example["datasources"]}
    real_names = {ds["name"] for ds in real["datasources"]}
    missing = real_names - example_names
    assert not missing, f"datasources.json.example 缺少数据源示例: {sorted(missing)}"


def test_offline_scripts_use_python_version_marker():
    """构建脚本生成版本标记，安装/升级脚本据此校验目标机 Python 版本。"""
    assert "python-version-required.txt" in (
        ROOT / "scripts" / "build_offline.sh"
    ).read_text(encoding="utf-8")
    for rel in (
        "deploy/install_offline.sh",
        "deploy/upgrade_offline.sh",
        "deploy/windows/install_offline.bat",
        "deploy/windows/upgrade_offline.bat",
    ):
        assert "python-version-required.txt" in (ROOT / rel).read_text(encoding="utf-8"), rel


def test_bat_and_cmd_use_crlf():
    """Windows 批处理必须 CRLF：LF 结尾的 .bat/.cmd 在部分 Windows 环境下解析异常。"""
    ga = (ROOT / ".gitattributes").read_text(encoding="utf-8")
    assert "*.bat text eol=crlf" in ga and "*.cmd text eol=crlf" in ga, ga
    files = sorted(p for p in (ROOT / "deploy" / "windows").iterdir() if p.suffix in (".bat", ".cmd"))
    assert files, "未找到任何 .bat/.cmd"
    for p in files:
        data = p.read_bytes()
        assert data.count(b"\n") == data.count(b"\r\n"), f"{p.name} 必须为 CRLF 换行"


def test_windows_bare_metal_deployment_support():
    """裸机部署：构建脚本支持 --self-contained，安装/启动脚本能从 runtime\\ 回退取运行时。"""
    build = (ROOT / "scripts" / "build_offline.sh").read_text(encoding="utf-8")
    assert "--self-contained" in build
    assert "install_only_stripped" in build, "便携 Python 必须用 stripped 版（否则大 24MB）"
    assert "api.adoptium.net" in build, "JRE 应来自 Adoptium"

    win = ROOT / "deploy" / "windows"
    install = (win / "install_offline.bat").read_text(encoding="utf-8", errors="replace")
    assert r"runtime\python\python.exe" in install, "安装脚本应能回退到包内便携 Python"
    assert r"runtime\jre" in install, "安装脚本应能回退到包内 JRE"
    assert "mcp-dbtools-service.cmd" in install, "安装时应能注册计划任务"

    for name in ("mcp-dbtools-run.cmd", "mcp-dbtools-service.cmd"):
        assert (win / name).is_file(), name
    runner = (win / "mcp-dbtools-run.cmd").read_text(encoding="utf-8", errors="replace")
    assert "JAVA_HOME" in runner, "启动器必须设置 JAVA_HOME，否则 JPype 找不到 JVM"
    assert "pythonw" in runner, "后台运行应使用 pythonw.exe（无控制台窗口）"
