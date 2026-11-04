# P7 部署指南

这份文档只做一件事：把项目 7 的旅游规划页面放到一台服务器上，给别人一个能打开的地址。
从零开始照抄即可，命令都写在下面。

先按需要选一种，不用全做：

| 方式 | 适合 | 需要 | 能做什么 |
|---|---|---|---|
| **A. 静态托管** | 只想给老师看效果 | 任意静态空间 | 七条真实结果的完整界面，按关键词匹配 |
| **B. 本机 / 内网** | 自己演示、同一局域网看 | 一台装 Python 的机器 | 自由提问，规则模式 |
| **C. 公网服务** | 老师随时打开、能自由提问 | 云服务器（+ 域名更好） | 同上，加口令、限流、HTTPS |

下文的服务器路径统一用 `/opt/datamining`，域名统一用 `p7.example.com`，照着换即可。

---

## A. 静态托管（5 分钟，不需要服务器）

整个 `src/P7旅游规划Agent/web/` 目录就是站点：`index.html`、`app.js`、`styles.css`、`offline.json`。
页面启动时会请求接口，失败就读同目录的 `offline.json`，自动进入离线回放。

做法：把这四个文件传到任意静态空间（对象存储、Nginx 静态目录、GitHub Pages 都行），
打开 `index.html` 的地址即可。`offline.json` 必须和 `index.html` 同目录。
不要直接双击 `web/index.html`：`file://` 下浏览器不允许页面读同目录 JSON，
那种情况请改用双击 `outputs/P7/行迹-离线预览.html`。

如果要重新生成这份数据（上游结果变了）：

```bash
python tools/P7/build_offline.py
```

局限：只有七条预置结果，不能回答新问题。需要自由提问就用 C。

---

## B. 本机 / 内网

```bash
python -m venv .venv
.venv/bin/pip install "fastapi>=0.115,<1" "uvicorn>=0.30,<1"
.venv/bin/python src/P7旅游规划Agent/serve.py
```

默认只监听 `127.0.0.1:8000`，自动开浏览器。要让同一局域网的人访问：

```bash
.venv/bin/python src/P7旅游规划Agent/serve.py --host 0.0.0.0
```

内网也没有必要设口令；真要设就加 `--token 至少十二位`。

---

## C. 公网服务

下面以 Ubuntu 22.04 / 24.04 为例。1 核 1G 足够，规则模式不调模型，内存占用约 200MB。

### C0. 先确认三件事

1. 服务器能 SSH 登录，有 sudo 权限。
2. 云控制台的安全组只放行 **22、80、443**。**不要**放行 8000——
   uvicorn 只监听本机，外部一律走 Nginx。
3. 有域名最好（HTTPS 需要）；没有域名也能用 IP 访问，见 C8 的备选。

### C1. 取代码

P7 运行只需要三个 JSON，不必拉整个仓库（完整仓库约 500MB，大头是 `data/` 原始语料）：

```bash
sudo mkdir -p /opt/datamining && sudo chown "$USER" /opt/datamining
git clone --filter=blob:none --sparse https://github.com/wzzrambler/datamining.git /opt/datamining
cd /opt/datamining
git sparse-checkout set src/P7旅游规划Agent processed/P2实体识别 processed/P6知识图谱/cleaned-v1 outputs/P7 deploy tools/P7
```

拉下来约 30MB。仓库是私有的就先用有权限的账号登录，或换成 SSH 地址。

不想用 sparse 就把第二行改成普通 `git clone`，代价是工作区约 500MB，clone 也慢得多。

### C2. 装依赖

```bash
sudo apt update
sudo apt install -y python3-venv nginx
cd /opt/datamining
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install "fastapi>=0.115,<1" "uvicorn>=0.30,<1"
```

要用大模型决策才装 `openai`，规则模式不需要：

```bash
.venv/bin/pip install "openai>=2.0,<3"
```

### C3. 自检

```bash
cd /opt/datamining
.venv/bin/python src/P7旅游规划Agent/serve.py --check
```

全部 `OK` 或 `WARN` 才能继续。`WARN` 里“模型密钥未配置”是正常的，规则模式照常跑。
出现 `FAIL` 按提示补：缺数据文件就回 C1 补 `sparse-checkout`，缺 fastapi 就回 C2。

### C4. 建运行用户

```bash
sudo useradd --system --no-create-home --home /opt/datamining --shell /usr/sbin/nologin p7
sudo chown -R p7:p7 /opt/datamining
```

### C5. 设访问口令

```bash
sudo mkdir -p /etc/p7
python3 -c "import secrets; print('P7_ACCESS_TOKEN=' + secrets.token_urlsafe(24))" | sudo tee /etc/p7/p7.env
sudo chmod 600 /etc/p7/p7.env
sudo cat /etc/p7/p7.env
```

把输出的那串口令记下来，浏览器第一次打开会弹窗要。口令少于 12 位服务会拒绝启动。

同一个文件里可以加可选项（不写就用默认值）：

```ini
P7_ACCESS_TOKEN=上面生成的那串
P7_MAX_CONCURRENCY=4
P7_RATE_LIMIT=60
P7_RATE_WINDOW=60
# P7_CORS_ORIGINS=https://你的前端域名    # 前后端同源时不用填
# OPENAI_API_KEY=...                     # 配了才会出现"大模型决策"
# OPENAI_BASE_URL=https://api.deepseek.com/v1
# OPENAI_MODEL=deepseek-chat
```

设完口令，按公网模式再查一次：

```bash
cd /opt/datamining
.venv/bin/python src/P7旅游规划Agent/serve.py --check --public \
  --token "$(sudo grep P7_ACCESS_TOKEN /etc/p7/p7.env | cut -d= -f2)"
```

“访问口令 已设置”“CORS 同源”都应是 `OK`，再继续。

### C6. 先手工跑一遍

```bash
cd /opt/datamining
TOKEN=$(sudo grep P7_ACCESS_TOKEN /etc/p7/p7.env | cut -d= -f2)
sudo -u p7 env P7_ACCESS_TOKEN="$TOKEN" \
  .venv/bin/python src/P7旅游规划Agent/serve.py --public --host 127.0.0.1 --port 8000 --no-browser
```

`/etc/p7/p7.env` 是 600 且属主 root，p7 用户读不到，所以这里用 `sudo` 取出再传进去；
systemd 是由 root 读该文件的，不受影响。

看到“行迹 · 旅游规划 Agent / 模式 公网 / 口令 已启用”就对了。**保持这个终端**，
另开一个窗口验证：

```bash
# 不带口令，应该是 401
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/api/health

# 带口令，应该是 {"ok":true,...}
TOKEN=$(sudo grep P7_ACCESS_TOKEN /etc/p7/p7.env | cut -d= -f2)
curl -s -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8000/api/health

# SSE：应该先出现 event: meta，再逐条出现 turn
curl -sN -H "Authorization: Bearer $TOKEN" \
  "http://127.0.0.1:8000/api/plan/stream?query=%E5%B8%A6%E7%88%B8%E5%A6%88%E5%8E%BB%E4%B9%9D%E5%AF%A8%E6%B2%9F%E7%8E%A9%E4%B8%80%E5%A4%A9&mode=rule" | head -20
```

三条都对，按 `Ctrl+C` 停掉，进 C7。

### C7. 用 systemd 常驻

```bash
sudo cp /opt/datamining/deploy/p7.service /etc/systemd/system/p7.service
sudo systemctl daemon-reload
sudo systemctl enable --now p7
sudo systemctl status p7 --no-pager
sudo journalctl -u p7 -n 30 --no-pager
```

服务名就是 `p7`。改完代码或改了 `/etc/p7/p7.env` 都要 `sudo systemctl restart p7`。

### C8. Nginx 与 HTTPS

有域名：

```bash
sudo cp /opt/datamining/deploy/nginx-p7.conf /etc/nginx/sites-available/p7
sudo sed -i 's/p7.example.com/你的域名/' /etc/nginx/sites-available/p7
sudo ln -sf /etc/nginx/sites-available/p7 /etc/nginx/sites-enabled/p7
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx
sudo ufw allow OpenSSH && sudo ufw allow 'Nginx Full'    # 没启用 ufw 可跳过
```

先把域名 A 记录指到服务器 IP，再申请证书：

```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d 你的域名
```

certbot 会问邮箱、同意条款、要不要跳转 HTTPS，按提示选。完成后访问 `https://你的域名`。

没有域名，只用 IP：

```bash
sudo nano /etc/systemd/system/p7.service   # 把 ExecStart 里的 --host 127.0.0.1 改成 --host 0.0.0.0
sudo systemctl daemon-reload
sudo systemctl restart p7
sudo ufw allow 8000/tcp                    # 云安全组也要放行 8000
```

然后访问 `http://服务器IP:8000`。这条路的流量是明文，口令也明文传输，只适合临时演示；
能上域名就上域名。

### C9. 验收清单

按顺序打勾，全过再交付：

- [ ] 你实际使用的地址能打开（域名或 `http://IP:8000`），右上角显示“在线 · 规则模式”（配了密钥则“双模式”）
- [ ] 点一个示例，右侧出现行程卡；决策过程是**逐条冒出来**的，不是最后一次性出现
- [ ] 复制和下载 .md 都能用
- [ ] 手机上用 4G 能打开（不是只在家里 Wi-Fi 能用）
- [ ] `curl` 不带口令返回 401
- [ ] `sudo reboot` 之后，不登服务器直接访问页面仍然可用
- [ ] `sudo journalctl -u p7 -n 50` 里没有反复重启或异常堆栈

### C10. 以后更新

```bash
cd /opt/datamining
sudo -u p7 git pull
sudo systemctl restart p7
sudo systemctl status p7 --no-pager
```

上游数据变了（`processed/` 重新生成过），再补一步重建离线包：

```bash
cd /opt/datamining
sudo -u p7 .venv/bin/python tools/P7/build_offline.py
```

停用服务：`sudo systemctl disable --now p7`。

---

## 常见问题

| 现象 | 原因 | 处理 |
|---|---|---|
| 502 Bad Gateway | 服务没起、端口不对 | `systemctl status p7`、`journalctl -u p7 -n 50`，确认监听 8000 |
| 打开一直“连接中” | 反代没转发或后端崩了 | 先在服务器上 `curl 127.0.0.1:8000/api/health` |
| 输完口令还是 401 | 口令不一致，或浏览器存了旧口令 | 点右上角“口令”重设；核对 `/etc/p7/p7.env` |
| 结果一次性全出现 | Nginx 开了响应缓冲 | 确认 `proxy_buffering off` 与 `X-Accel-Buffering no` 生效 |
| `ModuleNotFoundError: fastapi` | systemd 用了系统 Python | `ExecStart` 必须是 `/opt/datamining/.venv/bin/python` |
| 端口被占 | 旧的测试进程没退 | `ss -ltnp \| grep 8000`，杀掉或换 `--port` |
| 页面空白 / 502 | 后端没起，或 sparse 漏了 `src/P7旅游规划Agent` | `curl 127.0.0.1:8000/api/health`；`git sparse-checkout list` 核对 |
| 启动报“公网模式需要一个访问口令” | `/etc/p7/p7.env` 没被读到 | 确认 `EnvironmentFile` 路径、权限 600、属主 root |
| 限流不准 / 重启后计数清零 | 进程内计数 | 正常现象；保持单 worker，别加 `--workers` |

## 安全与成本

- **口令必须设**。`--public` 已经强制，少于 12 位会拒绝启动。口令只放 `/etc/p7/p7.env`，不要提交到仓库。
- **不要开多 worker**。限流和并发是进程内计数，多开会各算各的，等于没限流。
- **不要配模型密钥也能用**。规则模式零调用、零费用；配了密钥后公网任何人都能消耗额度，
  要么设供应商的额度上限，要么不配。
- **日志含用户输入**。请求参数在 `?query=` 里，会进 Nginx 访问日志；介意就把 `access_log` 关掉。
- **限流按真实 IP**。`deploy/nginx-p7.conf` 用 `$remote_addr` 覆盖 `X-Forwarded-For`，
  客户端自己伪造这个头不会改变限流键；如果再加一层 CDN，需要改成信任 CDN 的固定回源段。
- **8000 端口不要对公网开放**。反代场景下 uvicorn 只监听 `127.0.0.1`，外部只能走 443。
- 数据只读，服务不写业务文件；`processed/` 换版本后重启即可。

## 不用服务器

`outputs/P7/行迹-离线预览.html` 是单文件，双击就能看七条真实结果。
发给老师、放进 U 盘、挂到任意静态空间都行，不依赖 Python、不依赖网络。
