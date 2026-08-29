#!/usr/bin/env python3
"""下载 JDBC 驱动到 drivers/ 目录。

用法:
    python scripts/download_drivers.py [--drivers-dir drivers]

说明:
- openGauss / GaussDB：从 Maven Central 下载官方 opengauss-jdbc 驱动。
- MySQL：从 Maven Central 下载官方 mysql-connector-j 驱动。
- TDH(Inceptor)：商业驱动，无法公开下载。请从星环(TDH)客户端安装包中取得
  inceptor-driver.jar 后手工放入 drivers/ 目录。
- DB2：商业驱动（db2jcc4.jar），可从 DB2 服务器 java 目录（如
  /opt/ibm/db2/V*/java/db2jcc4.jar）拷贝放入 drivers/ 目录。
"""

from __future__ import annotations

import argparse
import sys
import urllib.request
from pathlib import Path

OPENGAUSS_VERSION = "5.0.0-og"
OPENGAUSS_MAVEN = (
    "https://repo1.maven.org/maven2/org/opengauss/opengauss-jdbc/"
    "{version}/opengauss-jdbc-{version}.jar"
)
MYSQL_VERSION = "8.4.0"
MYSQL_MAVEN = (
    "https://repo1.maven.org/maven2/com/mysql/mysql-connector-j/"
    "{version}/mysql-connector-j-{version}.jar"
)


def download(url: str, dest: Path) -> None:
    print(f"下载 {url}")
    print(f"  -> {dest}")
    req = urllib.request.Request(url, headers={"User-Agent": "mcp-dbtools/0.1"})
    with urllib.request.urlopen(req, timeout=120) as resp, open(dest, "wb") as fh:
        fh.write(resp.read())
    print(f"  完成 ({dest.stat().st_size} bytes)")


def main() -> int:
    parser = argparse.ArgumentParser(description="下载 JDBC 驱动")
    parser.add_argument("--drivers-dir", default="drivers")
    parser.add_argument(
        "--gauss-version", default=OPENGAUSS_VERSION, help="openGauss JDBC 版本"
    )
    parser.add_argument(
        "--skip-gauss", action="store_true", help="跳过 openGauss 驱动下载"
    )
    parser.add_argument(
        "--mysql-version", default=MYSQL_VERSION, help="MySQL JDBC 驱动版本"
    )
    parser.add_argument(
        "--skip-mysql", action="store_true", help="跳过 MySQL 驱动下载"
    )
    args = parser.parse_args()

    drivers = Path(args.drivers_dir)
    drivers.mkdir(parents=True, exist_ok=True)

    if not args.skip_gauss:
        dest = drivers / f"opengauss-jdbc-{args.gauss_version}.jar"
        if dest.exists():
            print(f"已存在，跳过: {dest}")
        else:
            try:
                download(
                    OPENGAUSS_MAVEN.format(version=args.gauss_version), dest
                )
            except Exception as exc:  # noqa: BLE001
                print(f"下载 openGauss 驱动失败: {exc}", file=sys.stderr)
                return 1

    if not args.skip_mysql:
        dest = drivers / f"mysql-connector-j-{args.mysql_version}.jar"
        if dest.exists():
            print(f"已存在，跳过: {dest.name}")
        else:
            try:
                download(
                    MYSQL_MAVEN.format(version=args.mysql_version), dest
                )
            except Exception as exc:  # noqa: BLE001
                print(f"下载 MySQL 驱动失败: {exc}", file=sys.stderr)
                return 1

    # TDH 驱动检查
    tdh = drivers / "inceptor-jdbc.jar"
    if tdh.exists():
        print("检测到 TDH 驱动: inceptor-jdbc.jar")
    else:
        print(
            "\n[提示] 未检测到 TDH 驱动 inceptor-jdbc.jar。\n"
            "TDH(Inceptor) 为商业产品，请从星环 TDH 环境取得驱动（例如容器内 "
            "/usr/lib/inceptor/lib/inceptor-jdbc-*.jar）拷贝到 "
            f"{drivers}/ 目录并命名为 inceptor-jdbc.jar。\n"
            "驱动类为 org.apache.hive.jdbc.HiveDriver（或 io.transwarp.jdbc.InceptorDriver）。"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
