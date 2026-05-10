#!/usr/bin/env python3
"""
WebGIS AI Agent 管理命令行工具

Usage:
    python manage.py init-db       初始化数据库（创建所有表）
    python manage.py --help        显示帮助
"""
import sys
import argparse


def cmd_init_db():
    """初始化数据库：创建所有表"""
    from app.core.database import init_db
    init_db()
    print("Database initialized successfully.")


def main():
    parser = argparse.ArgumentParser(
        prog="manage.py",
        description="WebGIS AI Agent management commands"
    )
    subparsers = parser.add_subparsers(dest="command")

    # init-db
    subparsers.add_parser("init-db", help="Initialize database (create all tables)")

    args = parser.parse_args()

    if args.command == "init-db":
        cmd_init_db()
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
