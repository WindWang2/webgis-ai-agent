#!/usr/bin/env python3
"""
WebGIS AI Agent 管理命令行工具 (V3.2 DevX Enhanced)

Usage:
    python manage.py init-db                  初始化数据库
    python manage.py create-admin <user> <email> <password>
                                             创建 admin 账号（公开注册关闭后的入口）
    python manage.py check                    基础设施诊断 (Agent CNS Health Check)
    python manage.py dev                      一键拉起全栈开发环境 (Backend + Worker + Frontend)
    python manage.py server                   启动 FastAPI 后端
    python manage.py worker                   启动 Celery Worker
"""
import sys
import os
import json
import argparse
import asyncio
import subprocess
import time
import uuid
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

# #663-A：env 加载归启动器所有。这里加载本地 .env 后，dev/server/worker
# 拉起的子进程（uvicorn / celery）经 os.environ 继承到同一份配置；app 代码
# 本身不再有 import 期副作用。
from dotenv import load_dotenv

load_dotenv()

console = Console()

def cmd_init_db():
    """初始化数据库：创建所有表"""
    with console.status("[bold green]Initializing database..."):
        from app.core.database import init_db
        init_db()
    console.print("[bold green]✓[/bold green] Database initialized successfully.")


def cmd_create_admin(username: str, email: str, password: str):
    """创建 admin 账号 —— 公开注册关闭后，运维用此命令创建初始 admin。

    会先 init_db 确保 users 表存在，再插入一条 role='admin' 的记录。
    密码用 scrypt 哈希；密码不打印到日志。
    """
    import re
    if not re.match(r"^[A-Za-z0-9_\-\.]{3,40}$", username):
        console.print("[red]非法 username：长度 3-40，允许字母/数字/_-. [/red]")
        sys.exit(2)
    if not re.match(r"^[^\s@]+@[^\s@]+\.[^\s@]+$", email):
        console.print("[red]非法 email 格式[/red]")
        sys.exit(2)
    if len(password) < 8:
        console.print("[red]password 至少 8 位[/red]")
        sys.exit(2)

    from app.core.database import init_db, SessionLocal
    from app.models.db_model import User
    from app.core.auth import hash_password
    from sqlalchemy import select, or_

    init_db()  # 幂等；确保表存在

    with SessionLocal() as db:
        # 检查唯一性
        existing = db.execute(
            select(User).where(or_(User.username == username, User.email == email))
        ).scalar_one_or_none()
        if existing is not None:
            console.print(f"[red]username 或 email 已存在 (id={existing.id})[/red]")
            sys.exit(2)
        user = User(
            id=str(uuid.uuid4()),
            username=username,
            email=email,
            password_hash=hash_password(password),
            role="admin",
            is_active=True,
        )
        db.add(user)
        db.commit()
    console.print(f"[bold green]✓[/bold green] Admin 创建成功: username={username} email={email} id={user.id}")
    console.print("[dim]使用 POST /api/v1/auth/login 获取 JWT。[/dim]")

async def check_infrastructure():
    """基础设施诊断"""
    console.print(Panel.fit("[bold blue]Agent CNS Infrastructure Diagnostic[/bold blue]"))
    
    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("Component", style="dim")
    table.add_column("Status")
    table.add_column("Detail")

    # 1. Database Check
    try:
        from app.core.database import Engine
        from sqlalchemy import text
        with Engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        table.add_row("Database", "[green]Healthy[/green]", "SQLAlchemy connection OK")
    except Exception as e:
        table.add_row("Database", "[red]Failed[/red]", str(e))

    # 2. Redis Check
    try:
        from app.core.config import settings
        import redis
        r = redis.from_url(settings.REDIS_URL, socket_connect_timeout=2)
        r.ping()
        r.close()
        table.add_row("Redis", "[green]Healthy[/green]", f"Connected to {settings.REDIS_URL}")
    except Exception as e:
        table.add_row("Redis", "[red]Failed[/red]", f"Ensure redis-server is running. ({e})")

    # 3. LLM API Check
    try:
        from app.core.config import settings
        import httpx
        # 只检查连通性，不发送真实请求
        with httpx.Client(timeout=5.0) as client:
            resp = client.get(settings.LLM_BASE_URL, follow_redirects=True)
            if resp.status_code in (200, 401, 404, 405): # 401/404/405 are often fine for base URL
                table.add_row("LLM API", "[green]Healthy[/green]", f"Endpoint {settings.LLM_BASE_URL} reachable")
            else:
                table.add_row("LLM API", "[yellow]Warning[/yellow]", f"Status {resp.status_code}")
    except Exception as e:
        table.add_row("LLM API", "[red]Failed[/red]", str(e))

    # 4. Celery Worker Check
    try:
        # 尝试通过 shell 命令检查 worker
        res = subprocess.run(
            ["celery", "-A", "app.services.task_queue.celery_app", "inspect", "ping"],
            capture_output=True, text=True, timeout=5
        )
        if "pong" in res.stdout.lower():
            table.add_row("Celery Worker", "[green]Online[/green]", "Worker is responsive")
        else:
            table.add_row("Celery Worker", "[yellow]Offline[/yellow]", "No active workers found. Run 'python manage.py worker'")
    except Exception:
        table.add_row("Celery Worker", "[yellow]Unknown[/yellow]", "Could not verify worker status")

    console.print(table)

def run_dev():
    """一键启动开发环境"""
    console.print("[bold cyan]Starting WebGIS AI Agent Dev Stack...[/bold cyan]")
    
    processes = []
    try:
        # 1. Start Redis check (not starting it, just warning)
        from app.core.config import settings
        import redis
        try:
            r = redis.from_url(settings.REDIS_URL, socket_connect_timeout=1)
            r.ping()
            r.close()
        except Exception:
            # Catch broad redis/network exceptions, but let KeyboardInterrupt/SystemExit
            # propagate so the user can ctrl-C a dev server start.
            console.print("[bold red]ERROR:[/bold red] Redis is not running. Please start redis-server first.")
            return

        # 2. Start Backend
        console.print("[dim]Launch: Backend Server (Port 18000)...[/dim]")
        p_server = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "18000", "--reload"])
        processes.append(p_server)

        # 3. Start Worker
        console.print("[dim]Launch: Celery Worker...[/dim]")
        p_worker = subprocess.Popen(["celery", "-A", "app.services.task_queue.celery_app", "worker", "--loglevel=info"])
        processes.append(p_worker)

        # 4. Start Frontend
        frontend_dir = os.path.join(os.getcwd(), "frontend")
        if os.path.exists(frontend_dir):
            console.print("[dim]Launch: Next.js Frontend (Port 3000)...[/dim]")
            # Use shell=True for npm on some systems, but try direct first
            p_frontend = subprocess.Popen(["npm", "run", "dev"], cwd=frontend_dir)
            processes.append(p_frontend)
        else:
            console.print("[yellow]Warning:[/yellow] frontend directory not found, skipping.")

        console.print("\n[bold green]Stack is up![/bold green] Press Ctrl+C to stop all services.\n")
        
        while True:
            time.sleep(1)
            # Check if any process died
            for p in processes:
                if p.poll() is not None:
                    console.print(f"[bold red]Process {p.pid} exited with code {p.returncode}[/bold red]")
                    raise KeyboardInterrupt

    except KeyboardInterrupt:
        console.print("\n[bold yellow]Shutting down...[/bold yellow]")
        for p in processes:
            p.terminate()
        for p in processes:
            p.wait()
        console.print("[bold green]All services stopped.[/bold green]")

def cmd_osm_ingest(pbf, themes, force, limit, flush_rows, idx):
    """一次性 OSM PBF → 主题 GPKG 预处理（本地资源查询的数据底座）。"""
    from pathlib import Path
    from app.services.local_osm import THEME_SPECS, default_pbf_path, ingest_pbf

    pbf_path = Path(pbf).expanduser() if pbf else default_pbf_path()
    if not pbf_path or not pbf_path.exists():
        console.print("[bold red]未找到 china-*.osm.pbf[/bold red] "
                      "（--pbf 指定路径，或将其放在 LOCAL_GEODATA_DIR 下）")
        sys.exit(1)
    theme_list = [t.strip() for t in themes.split(",") if t.strip()]
    unknown = [t for t in theme_list if t not in THEME_SPECS]
    if unknown:
        console.print(f"[bold red]未知主题: {unknown}（可选 {list(THEME_SPECS)}）[/bold red]")
        sys.exit(1)

    console.print(Panel.fit(
        f"[bold]OSM 预处理[/bold]\nPBF: {pbf_path}\n主题: {theme_list}"
        + (f"\n调试 limit: {limit}" if limit else ""),
        title="local-geodata"))
    from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeElapsedColumn

    with Progress(
        SpinnerColumn(), TextColumn("[progress.description]{task.description}"),
        BarColumn(), TextColumn("{task.completed:,} rows"),
        TimeElapsedColumn(), console=console,
    ) as progress:
        tasks = {t: progress.add_task(t, total=None) for t in theme_list}

        def _cb(theme: str, delta: int) -> None:
            if theme in tasks:
                progress.advance(tasks[theme], delta)

        result = ingest_pbf(
            pbf_path, theme_list, force=force,
            limit_objects=limit, flush_rows=flush_rows, progress_cb=_cb, idx=idx,
        )
    console.print_json(json.dumps(result, ensure_ascii=False, default=str))


def cmd_osm_status():
    """列出本地 OSM 主题库状态（是否已 ingest、行数）。"""
    import json as _json
    from app.services.local_osm import catalog
    console.print_json(_json.dumps(catalog(), ensure_ascii=False, default=str))


def cmd_yearbook_ingest(panel_only, force, years):
    """中国县域统计年鉴（乡镇卷 + 县域面板）→ yearbook.sqlite。"""
    from app.services import local_yearbook

    if not panel_only:
        console.print(Panel.fit(
            "[bold]年鉴预处理[/bold]\n乡镇卷 zip → SQLite（与 district.shp 做 adcode 连接）",
            title="local-geodata"))
        stats = local_yearbook.ingest_yearbook(
            years=[int(y) for y in years.split(",")] if years else None,
            force=force,
            progress_cb=lambda year, rows: console.print(
                f"  出版年 [cyan]{year}[/cyan]: {rows:,} 乡镇行"),
        )
        console.print_json(json.dumps(stats, ensure_ascii=False, default=str))
    panel = local_yearbook.ingest_county_panel(force=force)
    console.print_json(json.dumps(panel, ensure_ascii=False, default=str))


def cmd_yearbook_status():
    """年鉴库状态：年份覆盖、行数、行政区连接率、指标词表。"""
    import json as _json
    from app.services.local_yearbook import yearbook_catalog
    console.print_json(_json.dumps(yearbook_catalog(), ensure_ascii=False, default=str))


def cmd_gd_poi_ingest(force, provinces):
    """高德 POI zip 系列 → gd_pois.gpkg（GCJ-02 → WGS84）。"""
    from rich.progress import (BarColumn, Progress, SpinnerColumn,
                               TextColumn, TimeElapsedColumn)

    from app.services.local_poi import ingest_gd_poi

    console.print(Panel.fit(
        "[bold]高德 POI 预处理[/bold]\nlocation(GCJ-02) → WGS84 点库；"
        "「乡镇级地名」回填年鉴乡镇中心点", title="local-geodata"))
    with Progress(
        SpinnerColumn(), TextColumn("[progress.description]{task.description}"),
        BarColumn(), TextColumn("{task.completed:,} rows"),
        TimeElapsedColumn(), console=console,
    ) as progress:
        current = {}

        def _cb(pcode: str, rows: int) -> None:
            task = current.get(pcode)
            if task is None:
                task = current[pcode] = progress.add_task(pcode, total=None)
            progress.update(task, completed=rows)

        stats = ingest_gd_poi(
            force=force,
            provinces=provinces.split(",") if provinces else None,
            progress_cb=_cb,
        )
    console.print_json(json.dumps(stats, ensure_ascii=False, default=str))


def main():
    parser = argparse.ArgumentParser(
        prog="manage.py",
        description="WebGIS AI Agent management commands"
    )
    subparsers = parser.add_subparsers(dest="command")

    # init-db
    subparsers.add_parser("init-db", help="Initialize database (create all tables)")

    # create-admin
    p_ca = subparsers.add_parser("create-admin", help="Create an admin account (entry point when public register is disabled)")
    p_ca.add_argument("username")
    p_ca.add_argument("email")
    p_ca.add_argument("password")

    # check
    subparsers.add_parser("check", help="Infrastructure diagnostic (Agent CNS Health Check)")

    # dev
    subparsers.add_parser("dev", help="Start full dev stack (Backend + Worker + Frontend)")

    # server
    subparsers.add_parser("server", help="Start FastAPI backend server")

    # worker
    subparsers.add_parser("worker", help="Start Celery worker")

    # osm-ingest
    p_oi = subparsers.add_parser("osm-ingest", help="One-off OSM PBF -> themed GPKG preprocessing for local resource queries")
    p_oi.add_argument("--pbf", default=None, help="china-*.osm.pbf 路径（缺省自动在 LOCAL_GEODATA_DIR 下发现）")
    p_oi.add_argument("--themes", default="pois,roads,railways,waterways", help="逗号分隔主题（可选: pois/roads/railways/waterways）")
    p_oi.add_argument("--force", action="store_true", help="覆盖已存在的主题 GPKG（默认跳过）")
    p_oi.add_argument("--limit", type=int, default=0, help="调试：仅扫描前 N 个对象")
    p_oi.add_argument("--flush-rows", type=int, default=100_000, help="分块写库行数")
    p_oi.add_argument("--idx", default="dense_file_array", choices=["dense_file_array", "sparse_file_array", "flex_mem", "dense_mem_array"], help="节点位置索引（默认磁盘索引防 OOM；小数据集可换 dense_mem_array 提速）")

    # osm-status
    subparsers.add_parser("osm-status", help="List local OSM theme catalog and row counts")

    # yearbook-ingest
    p_yb = subparsers.add_parser("yearbook-ingest", help="中国县域统计年鉴（乡镇卷+县域面板）→ yearbook.sqlite（含行政区 adcode 连接）")
    p_yb.add_argument("--panel-only", action="store_true", help="只导入县域面板（跳过乡镇卷 zip）")
    p_yb.add_argument("--force", action="store_true", help="重导已导入年份（默认按年幂等跳过）")
    p_yb.add_argument("--years", default=None, help="逗号分隔出版年，如 2014,2015（默认全部）")

    # yearbook-status
    subparsers.add_parser("yearbook-status", help="年鉴库状态：年份/行数/连接率/指标词表")

    # gd-poi-ingest
    p_gp = subparsers.add_parser("gd-poi-ingest", help="高德全国 POI zip → gd_pois.gpkg（GCJ-02→WGS84，含乡镇中心点回填；支持 xlsx/csv 成员）")
    p_gp.add_argument("--force", action="store_true", help="重建（默认按省幂等跳过；配合 --provinces 时只刷新指定省，其余省数据不动）")
    p_gp.add_argument("--provinces", default=None, help="逗号分隔省级 adcode，如 510000,540000（默认全部）")

    args = parser.parse_args()

    if args.command == "init-db":
        cmd_init_db()
    elif args.command == "create-admin":
        cmd_create_admin(args.username, args.email, args.password)
    elif args.command == "check":
        asyncio.run(check_infrastructure())
    elif args.command == "dev":
        run_dev()
    elif args.command == "server":
        subprocess.run([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "18000", "--reload"])
    elif args.command == "worker":
        subprocess.run(["celery", "-A", "app.services.task_queue.celery_app", "worker", "--loglevel=info"])
    elif args.command == "osm-ingest":
        cmd_osm_ingest(args.pbf, args.themes, args.force, args.limit, args.flush_rows, args.idx)
    elif args.command == "osm-status":
        cmd_osm_status()
    elif args.command == "yearbook-ingest":
        cmd_yearbook_ingest(args.panel_only, args.force, args.years)
    elif args.command == "yearbook-status":
        cmd_yearbook_status()
    elif args.command == "gd-poi-ingest":
        cmd_gd_poi_ingest(args.force, args.provinces)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
