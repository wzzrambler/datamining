# -*- coding: utf-8 -*-
"""项目 7 的启动器：本机直用与公网部署走同一个服务，只是约束不同。

    python src/P7旅游规划Agent/serve.py                 # 本机：127.0.0.1，无口令，自动开浏览器
    python src/P7旅游规划Agent/serve.py --public        # 公网：0.0.0.0，强制口令，CORS 同源
    python src/P7旅游规划Agent/serve.py --check         # 只做自检，不启服务
    python src/P7旅游规划Agent/serve.py --token abc...  # 指定口令（两种模式都可用）

本机模式不读环境里的 P7_ACCESS_TOKEN，免得部署用的口令把本地调试也拦住；
要测口令流程就显式传 --token。公网模式必须给口令，少于 12 位直接拒绝启动。

不需要服务也能看：tools/P7/build_offline.py 把七个场景的结果内联成一个
HTML，双击即可打开，见 outputs/P7/行迹-离线预览.html。
"""
from __future__ import annotations

import argparse
import os
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
MIN_TOKEN = 12
PORT_TRIES = 12

NO_TOKEN_HINT = (
    '公网模式需要一个访问口令。先生成一个，再启动：\n'
    "  python -c \"import secrets;print(secrets.token_urlsafe(24))\"\n"
    '  P7_ACCESS_TOKEN=<上一步的输出> python src/P7旅游规划Agent/serve.py --public\n'
    '没有口令的服务不要放到公网：任何人都能直接消耗模型额度。'
)

PROXY_SNIPPET = """\
server {
    listen 80;
    server_name your.domain;
    location / {
        proxy_pass http://127.0.0.1:%(port)d;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_buffering off;          # SSE 逐轮推送必须关掉缓冲
        proxy_read_timeout 300s;
    }
}"""


class ConfigError(SystemExit):
    """启动参数不满足要求，打印原因后退出（码 2）。"""

    def __init__(self, message: str):
        super().__init__('启动失败：' + message)


def port_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True


def pick_port(host: str, preferred: int = 8000, tries: int = PORT_TRIES) -> int:
    """从 preferred 起找一个能绑的端口，避免 8000 被占时直接报错。"""
    for offset in range(tries):
        candidate = preferred + offset
        if port_free(host, candidate):
            return candidate
    raise ConfigError('%s 的 %d—%d 端口都被占用，用 --port 指定一个空闲端口'
                      % (host, preferred, preferred + tries - 1))


def resolve_mode(public: bool, token: str | None = None, host: str | None = None,
                 port: int | None = None, open_browser: bool | None = None) -> dict:
    """把命令行参数收敛成一份启动配置。纯函数，便于测试。"""
    supplied = (token or '').strip()
    if public:
        env_token = os.environ.get('P7_ACCESS_TOKEN', '').strip()
        secret = supplied or env_token
        if not secret:
            raise ConfigError(NO_TOKEN_HINT)
        if len(secret) < MIN_TOKEN:
            raise ConfigError('公网口令至少 %d 位，当前只有 %d 位' % (MIN_TOKEN, len(secret)))
        cors = (os.environ.get('P7_CORS_ORIGINS') or '').strip()
        if cors == '*':
            cors = ''            # 同源部署不需要跨域；设成 * 等于把口令之外的边界也撤了
        return {
            'public': True,
            'host': host or '0.0.0.0',
            'port': int(port or os.environ.get('P7_PORT') or 8000),
            'token': secret,
            'cors': cors,
            'open_browser': bool(open_browser),
            'label': '公网',
        }
    return {
        'public': False,
        'host': host or '127.0.0.1',
        'port': int(port or os.environ.get('P7_PORT') or 8000),
        'token': supplied,          # 本机默认不启用口令，显式传才生效
        'cors': (os.environ.get('P7_CORS_ORIGINS') or '*').strip(),
        'open_browser': True if open_browser is None else bool(open_browser),
        'label': '本机',
    }


def apply_env(config: dict) -> None:
    """api.py 在导入时读环境变量，所以必须先写环境再 import。"""
    os.environ['P7_ACCESS_TOKEN'] = config['token']
    os.environ['P7_CORS_ORIGINS'] = config['cors']
    os.environ['P7_HOST'] = config['host']
    os.environ['P7_PORT'] = str(config['port'])


def _data_files() -> list[tuple[str, Path]]:
    return [
        ('P2 实体', ROOT / 'processed/P2实体识别/entities.json'),
        ('P6 路线', ROOT / 'processed/P6知识图谱/cleaned-v1/routes.json'),
        ('P6 条件建议', ROOT / 'processed/P6知识图谱/cleaned-v1/conditions.json'),
        ('P5 批次目录', ROOT / 'processed/P5条件建议抽取/runs'),
        ('前端页面', HERE / 'web/index.html'),
        ('前端脚本', HERE / 'web/app.js'),
        ('前端样式', HERE / 'web/styles.css'),
    ]


def check(port: int = 8000, public: bool = False) -> list[tuple[str, str, str]]:
    """逐项自检，返回 (级别, 项目, 说明)。级别：OK / WARN / FAIL。"""
    rows: list[tuple[str, str, str]] = []

    version = '.'.join(str(n) for n in sys.version_info[:3])
    if sys.version_info >= (3, 10):
        rows.append(('OK', 'Python', version))
    else:
        rows.append(('FAIL', 'Python', '%s，需要 3.10 以上' % version))

    for module, hint in (('fastapi', 'pip install fastapi'), ('uvicorn', 'pip install uvicorn')):
        try:
            imported = __import__(module)
            rows.append(('OK', module, getattr(imported, '__version__', '已安装')))
        except Exception:
            rows.append(('FAIL', module, '未安装，执行 %s' % hint))

    for name, path in _data_files():
        rows.append(('OK', name, str(path.relative_to(ROOT))) if path.exists()
                    else ('FAIL', name, '缺少 %s，先跑上游流水线' % path.relative_to(ROOT)))

    if port_free('127.0.0.1', port):
        rows.append(('OK', '端口 %d' % port, '空闲'))
    else:
        rows.append(('WARN', '端口 %d' % port, '已被占用，启动时会自动顺延'))

    if os.environ.get('OPENAI_API_KEY'):
        rows.append(('OK', '模型密钥', '已配置，可用大模型模式'))
    else:
        rows.append(('WARN', '模型密钥', '未配置，只能用规则模式（不影响演示）'))

    offline = ROOT / 'outputs/P7/行迹-离线预览.html'
    rows.append(('OK', '离线单页', str(offline.relative_to(ROOT))) if offline.exists()
                else ('WARN', '离线单页', '未生成，执行 python tools/P7/build_offline.py'))

    if public:
        token = os.environ.get('P7_ACCESS_TOKEN', '').strip()
        if len(token) >= MIN_TOKEN:
            rows.append(('OK', '访问口令', '已设置，%d 位' % len(token)))
        elif token:
            rows.append(('FAIL', '访问口令', '只有 %d 位，至少 %d 位' % (len(token), MIN_TOKEN)))
        else:
            rows.append(('FAIL', '访问口令', '未设置，公网模式必须设置'))
        cors = (os.environ.get('P7_CORS_ORIGINS') or '').strip()
        if cors in ('', '*'):
            rows.append(('WARN', 'CORS', '同源或未限制；跨域前端才需要显式填域名'))
        else:
            rows.append(('OK', 'CORS', cors))

    return rows


def print_check(rows: list[tuple[str, str, str]]) -> bool:
    width = max(len(name) for _, name, _ in rows)
    for level, name, detail in rows:
        print('[%s] %s  %s' % (level.ljust(4), name.ljust(width), detail))
    failed = sum(1 for level, _, _ in rows if level == 'FAIL')
    warned = sum(1 for level, _, _ in rows if level == 'WARN')
    print('\n%d 项通过，%d 项警告，%d 项失败' % (
        len(rows) - failed - warned, warned, failed))
    return failed == 0


def _lan_hint(port: int) -> str:
    """猜一个局域网地址用于提示，不实际发包。"""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(('223.5.5.5', 80))
            ip = sock.getsockname()[0]
        return 'http://%s:%d' % (ip, port)
    except OSError:
        return ''


def banner(config: dict) -> None:
    url = 'http://%s:%d' % ('127.0.0.1' if config['host'] in ('0.0.0.0', '::') else config['host'],
                            config['port'])
    print('\n行迹 · 旅游规划 Agent')
    print('  模式    %s' % config['label'])
    print('  本机    %s' % url)
    if config['public']:
        lan = _lan_hint(config['port'])
        if lan:
            print('  局域网  %s' % lan)
        print('  口令    已启用（%d 位）' % len(config['token']))
        print('  跨域    %s' % (config['cors'] or '同源'))
        print('\n公网还需要三步：')
        print('  1. 云安全组放行 %d 端口（或只放行 80/443 由反代转发）' % config['port'])
        print('  2. 用 Nginx/Caddy 加 HTTPS，SSE 要关掉响应缓冲：')
        print('\n'.join('     ' + line for line in
                        (PROXY_SNIPPET % {'port': config['port']}).splitlines()))
        print('  3. 保持单 worker：限流与并发计数是进程内内存态，多开会各算各的')
    else:
        print('  口令    未启用（仅本机可访问；要测口令加 --token）')
    print('  停止    Ctrl+C\n')


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description='项目 7 Web 服务：本机直用或公网部署',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='不启服务也能看：python tools/P7/build_offline.py 生成单文件预览')
    parser.add_argument('--public', action='store_true', help='公网模式：监听 0.0.0.0，强制口令')
    parser.add_argument('--host', help='覆盖监听地址')
    parser.add_argument('--port', type=int, default=None,
                        help='监听端口，默认 8000；被占用时自动顺延')
    parser.add_argument('--token', help='访问口令；公网模式至少 12 位')
    parser.add_argument('--no-browser', action='store_true', help='本机模式不自动开浏览器')
    parser.add_argument('--check', action='store_true', help='只做环境自检')
    args = parser.parse_args(argv)

    if args.check:
        return 0 if print_check(check(args.port or 8000, args.public)) else 1

    config = resolve_mode(args.public, token=args.token, host=args.host,
                          port=args.port, open_browser=False if args.no_browser else None)
    if config['public']:
        os.environ['P7_ACCESS_TOKEN'] = config['token']
        os.environ['P7_CORS_ORIGINS'] = config['cors']
    if args.port is None:               # 用户没指定端口才自动顺延
        config['port'] = pick_port(config['host'], config['port'])
    apply_env(config)

    import uvicorn                      # 延迟导入，--check 在缺依赖时也能给出可读输出
    import api

    banner(config)
    url = 'http://127.0.0.1:%d' % config['port']
    if config['open_browser']:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()

    uvicorn.run(api.app, host=config['host'], port=config['port'], log_level='info',
                proxy_headers=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
