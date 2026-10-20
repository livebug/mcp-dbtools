#!/usr/bin/env python3
"""生成 SQLite 演示库 data/demo.db（三张表 + 样例数据）。

用法:
    python scripts/create_sqlite_demo.py [--path data/demo.db] [--force]

用途：本地免装数据库即可跑通 Web 控制台 / REST API / MCP 工具全链路。
数据库文件被 .gitignore 忽略，换机器重新执行本脚本即可。
"""

from __future__ import annotations

import argparse
import datetime
import random
import sqlite3
import sys
from pathlib import Path

SCHEMA = """
CREATE TABLE departments (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  location TEXT
);
CREATE TABLE employees (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  dept_id INTEGER REFERENCES departments(id),
  title TEXT,
  salary REAL,
  hired_at TEXT
);
CREATE TABLE orders (
  id INTEGER PRIMARY KEY,
  employee_id INTEGER REFERENCES employees(id),
  amount REAL NOT NULL,
  status TEXT NOT NULL,
  created_at TEXT
);
CREATE INDEX idx_orders_status ON orders(status);
"""

DEPARTMENTS = [
    (1, "研发中心", "北京"),
    (2, "数据平台部", "上海"),
    (3, "财务部", "深圳"),
    (4, "运维部", "北京"),
]

NAMES = ["张伟", "李娜", "王强", "刘洋", "陈静", "杨帆", "赵磊", "孙悦", "周涛", "吴敏", "郑凯", "冯雪"]
TITLES = ["工程师", "高级工程师", "技术专家", "数据分析师", "DBA", "产品经理", "测试工程师"]
STATUSES = ["paid", "pending", "refunded", "cancelled"]


def build(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    try:
        con.executescript(SCHEMA)
        con.executemany("INSERT INTO departments VALUES (?,?,?)", DEPARTMENTS)

        rnd = random.Random(42)  # 固定种子，保证每次生成一致
        employees = []
        for i, name in enumerate(NAMES, 1):
            employees.append(
                (
                    i,
                    name,
                    rnd.choice([1, 1, 2, 2, 3, 4]),
                    rnd.choice(TITLES),
                    8000 + rnd.randint(0, 22) * 1000,
                    f"20{rnd.randint(18, 24)}-{rnd.randint(1, 12):02d}-{rnd.randint(1, 28):02d}",
                )
            )
        con.executemany("INSERT INTO employees VALUES (?,?,?,?,?,?)", employees)

        orders = []
        for i in range(1, 41):
            day = datetime.date(2026, 1, 1) + datetime.timedelta(days=rnd.randint(0, 290))
            orders.append(
                (i, rnd.randint(1, len(NAMES)), round(rnd.uniform(80, 9800), 2), rnd.choice(STATUSES), day.isoformat())
            )
        con.executemany("INSERT INTO orders VALUES (?,?,?,?,?)", orders)
        con.commit()
    finally:
        con.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="生成 SQLite 演示库")
    parser.add_argument("--path", default="data/demo.db", help="输出路径（默认 data/demo.db）")
    parser.add_argument("--force", action="store_true", help="已存在时覆盖重建")
    args = parser.parse_args()

    path = Path(args.path)
    if path.exists() and not args.force:
        print(f"已存在，跳过: {path}（--force 可重建）")
        return 0
    if path.exists():
        path.unlink()  # --force：先删旧库，避免建表冲突

    build(path)
    con = sqlite3.connect(path)
    try:
        rows = {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("departments", "employees", "orders")}
    finally:
        con.close()
    print(f"已生成 {path}（{path.stat().st_size} bytes）: " + ", ".join(f"{k}={v}" for k, v in rows.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
