"""``python -m app.extensions_platform`` 入口（转发到 cli.main）。

保持极薄：argparse 解析与全部 lazy import 都在 cli.py。
"""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
