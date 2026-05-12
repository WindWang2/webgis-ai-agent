#!/usr/bin/env python3
"""
WebGIS AI Agent 管理命令行工具 (V3.2 DevEx Enhanced)

Usage:
    python manage.py init-db       初始化数据库
    python manage.py check         基础设施诊断 (Agent CNS Health Check)
    python manage.py dev           一键拉起全栈开发环境 (Backend + Worker + Frontend)
    python manage.py server        启动 FastAPI 后端
    python manage.py worker        启动 Celery Worker
"""
import sys
import os
import argparse
import asyncio
import subprocess
import time
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.status import Status

console = Console()

def cmd_init_db():
    """初始化数据库：创建所有表"""
    with console.status("[bold green]Initializing database..."):
        from app.core.database import init_db
        init_db()
    console.print("[bold green]✓[/bold green] Database initialized successfully.")

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
        except:
            console.print("[bold red]ERROR:[/bold red] Redis is not running. Please start redis-server first.")
            return

        # 2. Start Backend
        console.print("[dim]Launch: Backend Server (Port 18000)...[/dim]")
        p_server = subprocess.Popen([sys.executable, "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "18000", "--reload"])
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

def main():
    parser = argparse.ArgumentParser(
        prog="manage.py",
        description="WebGIS AI Agent management commands"
    )
    subparsers = parser.add_subparsers(dest="command")

    # init-db
    subparsers.add_parser("init-db", help="Initialize database (create all tables)")
    
    # check
    subparsers.add_parser("check", help="Infrastructure diagnostic (Agent CNS Health Check)")
    
    # dev
    subparsers.add_parser("dev", help="Start full dev stack (Backend + Worker + Frontend)")

    # server
    subparsers.add_parser("server", help="Start FastAPI backend server")

    # worker
    subparsers.add_parser("worker", help="Start Celery worker")

    args = parser.parse_args()

    if args.command == "init-db":
        cmd_init_db()
    elif args.command == "check":
        asyncio.run(check_infrastructure())
    elif args.command == "dev":
        run_dev()
    elif args.command == "server":
        subprocess.run([sys.executable, "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "18000", "--reload"])
    elif args.command == "worker":
        subprocess.run(["celery", "-A", "app.services.task_queue.celery_app", "worker", "--loglevel=info"])
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
