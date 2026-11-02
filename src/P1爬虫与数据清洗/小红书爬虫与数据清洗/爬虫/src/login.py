"""仅人工登录并保存浏览器状态：python -m src.login。

导入本模块不会打开浏览器。不会复制其他账号的 Cookie，也不导出明文凭据。
"""
import argparse
from . import crawler


def login(profile=crawler.ACTIVE_PROFILE, channel='msedge'):
    """打开可见浏览器，用户确认后保存状态并退出；返回 0 成功、2 失败。

    成功表示用户已确认且页面检查通过，并非服务器认证有效性的保证。
    与采集共用运行锁、停止锁、预算和等待，不自动清除或重置这些状态。
    """
    return crawler.main(['--login-only', '--profile', profile, '--channel', channel])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', default=crawler.ACTIVE_PROFILE,
                        help='默认使用当前新账号目录 check_20261030')
    parser.add_argument('--channel', choices=['msedge', 'chrome', 'chromium'], default='msedge')
    args = parser.parse_args(argv)
    return login(profile=args.profile, channel=args.channel)


if __name__ == '__main__':
    raise SystemExit(main())
