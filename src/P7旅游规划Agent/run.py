import argparse

import uvicorn


def main():
    cli=argparse.ArgumentParser(description='启动 P7_1 行迹 API')
    cli.add_argument('--port',type=int,default=8017)
    args=cli.parse_args()
    uvicorn.run('p7.api:app',host='127.0.0.1',port=args.port,workers=1,proxy_headers=False)


if __name__=='__main__':
    main()
