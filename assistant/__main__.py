"""``python -m assistant`` 入口：先加载项目根 .env，再交给命令行。"""

from .cli import main
from .env import load_env

if __name__ == "__main__":
    load_env()
    raise SystemExit(main())
