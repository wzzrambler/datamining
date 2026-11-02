"""兼容原来的单任务命令；正式实现位于 src。"""
from src.crawler import main

if __name__ == '__main__':
    raise SystemExit(main())
