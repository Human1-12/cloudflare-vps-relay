# Cloudflare 固定 VPS 中转测试工具

同一台已有 VPS，保留原节点，再增加一条经过 Cloudflare 的测试线路。

```text
原线路：电脑 ───────────────→ 原 VLESS/REALITY 节点 → 网站
测试线：电脑 → Cloudflare Worker → 同一个原节点     → 网站
```

**目前是实验工具。接入 Cloudflare 不保证加速，也不保证通宵不掉线。先比较再决定是否长期使用。**

## 它会做什么

- 新建一个独立 Worker 和一个新的测试子域名。
- Worker 仅接受独立入口凭据，仅转发到指定的 VPS 地址和端口。
- 原有 REALITY 加密和原节点凭据仍由客户端与原 VPS 使用；不会把原节点凭据放进 Worker。
- 原节点、VPS 服务、SSH、防火墙和本机系统代理都不由部署脚本修改。
- 默认不采用公共 ProxyIP、随机出口、自动换节点或直连回退。
- 生成两个本地测试配置，分别用于原线路与中转线路。

它不提供新的落地 IP。最终出口由原 VPS 及其原有出站配置决定。一个 VPS 的 IPv4/IPv6、服务端分流和其他出站设置仍可能影响观察到的出口。

## 第一次准备

需要：

1. 已有且正常使用的 **VLESS + TCP/RAW + REALITY** 节点。
2. Cloudflare 账户及状态为 **Active** 的域名。
3. 一个全新的测试子域名，例如 `relay.example.com`，没有已有 DNS 记录或 Worker 绑定。
4. Python 3.9 及以上；无需 root，无需改 VPS。可在 VPS 登录后运行，也可以在自己的电脑运行。
5. 一枚由本人在 Cloudflare 创建的 API 令牌。最低准备权限为指定账户的 **Workers Scripts / Edit**、指定区域的 **Zone / Read** 和 **DNS / Read**。脚本用 Workers 自定义域名 API 绑定入口，不自行改其他 DNS 记录。首次使用应按 Cloudflare 当前界面核对权限名称。

**不要使用 Global API Key。不要把 API 令牌、原节点链接或 `_local` 文件夹上传 GitHub。** 令牌只在运行时隐藏输入，不写入部署记录。入口 UUID 作为 Cloudflare Secret 保存；本机部署记录含有入口 UUID，须私人保管。

## 一条命令开始

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/Human1-12/cloudflare-vps-relay/main/install.sh)
```

这会下载脚本、核对内置 SHA-256，再进入中文菜单。安装位置默认为本人主目录中的 `cf-vps-relay`。已有程序文件变化会先备份到 `_local/backups`，本地密钥不会被重置。

脚本来自本仓库。第一次建议先下载阅读；不要运行来源不明的同名脚本。

菜单选 **1：部署测试入口**，填写测试子域名、原节点地址/端口和 API 令牌；账户与区域 ID 会自动查找。Worker 名称可直接回车使用默认值。

| 提示 | 在哪里找／填什么 |
|---|---|
| Worker 名称 | 使用新的名称，例如 `cf-vps-relay` |
| 测试子域名 | 例如 `relay.example.com` |
| 原节点地址 | 与现有客户端中的服务器地址完全相同 |
| 原节点端口 | 原节点正在使用的端口 |
| API 令牌 | 隐藏输入，不会显示和保存 |

脚本会先只读检查同名资源、域名状态和 DNS 冲突，最后展示计划。输入 `DEPLOY` 才会新增公网测试入口。**不会覆盖同名 Worker，不会接管已有 DNS 记录。**

也可在本地源码目录运行：

```bash
python3 deploy.py
```

如果网络中断导致结果未知，保留 `_local/deployment.json`，先执行菜单 **查看状态**，并到 Cloudflare 核对。禁止删除状态文件后盲目重跑，避免丢失入口凭据或覆盖资源。

## 准备两条客户端测试线

1. 在现有客户端中复制原节点的 VLESS 分享链接，将其保存为自己设备上的私人文本文件，例如 `direct-link.txt`。
2. 菜单选 **2：生成两个客户端配置**，输入该文本文件的完整路径。
3. 程序生成 `_local/clients/direct.json` 和 `_local/clients/relay.json`。它们包含原节点凭据，只给自己的 Xray/v2rayN 使用。

两份配置的内部节点、REALITY 参数和用户身份一致。`relay.json` 仅在该出站中增加 `dialerProxy: cf-front`；前置出站通过 TLS WebSocket 访问 Cloudflare。Worker 对目标地址与端口做严格匹配。

### Windows / v2rayN

保留现有节点和分流规则。先用独立 Xray 进程测试，不切换正在使用的 v2rayN：

```powershell
# 在两个独立终端中启动，Xray 路径按自己的安装位置填写。
& 'C:\path\to\xray.exe' run -config '.\_local\clients\direct.json'
& 'C:\path\to\xray.exe' run -config '.\_local\clients\relay.json'
```

第一份只监听 `127.0.0.1:18081`，第二份只监听 `127.0.0.1:18082`。**关闭这两个测试进程即可停测，系统代理不变。** 若端口已被其他服务占用，先核对监听程序，再换端口并同步修改测速参数。

通过测试后，可用 v2rayN 的“自定义配置”功能分别导入，并命名为“原节点直连”和“原节点 CF 中转”。不同版本菜单可能不同。导入后必须检查实际生成的配置仍有 `dialerProxy`，并测试实际出口。

如果本机已经启用 TUN，独立启动的测试 Xray 可能被现用代理再次接管，导致超时或测试中额外多走一层代理。先核对物理网卡当前 IPv4，再通过 `--source-ip` 给**临时测试配置**绑定该源地址，例如：

```bash
python3 make_clients.py --direct-link-file direct-link.txt --source-ip 192.168.1.10
```

示例地址必须改成自己当前网卡地址。换 Wi-Fi 后要重新核对。仅看到源地址设置还不足以证明隔离成功，应核对实际连接的源地址和出口，不要为了测速关闭正在使用的正式代理。

另一种接法是导入前置 VLESS 节点，再在一个独立测试分组里把它设为“前置代理”。**前置别名必须唯一且存在。** 部分版本在前置别名无效时会跳过链式功能，因此不能仅看界面保存成功。[v2rayN 官方说明](https://github.com/2dust/v2rayN/wiki/Description-of-proxy-chain)

## 比较速度和稳定性

先启动上述两份配置，再运行：

```bash
# 一轮短连接与出口指纹比较
python3 benchmark.py

# 每条线路各下载20MiB，比较传输速度
python3 benchmark.py --download-mb 20

# 连续8小时，每分钟检查一次短HTTPS连接
python3 benchmark.py --hours 8 --interval 60

# 可选：每条线路分别进行30分钟限速下载；会消耗流量
python3 benchmark.py --stream-minutes 30
```

结果保存在 `_local/benchmark.csv`。请求明确使用两个本机代理，代理失败就记失败，不回退直连。CSV 只记录出口 IP 的哈希指纹，不记录完整代理链接和密钥。

还可以做更直接的持续连接测试：

```bash
python3 heartbeat_probe.py --seconds 180
```

Worker 每5秒在同一个响应里发送一个小事件，客户端记录间隔与最终完成标记。此方法不会把一次性下载后的本地缓冲误认为服务器持续发送数据。`/probe` 需要入口密钥，单次最多300秒；结果不含密钥。它仍只证明该次1-5分钟连接的表现。

必须分开看三类证据：

| 证据 | 能证明什么 | 不能证明什么 |
|---|---|---|
| `/health` 返回200 | Worker 的 HTTP 入口可达 | TCP中转、原节点认证、AI可用性 |
| 重复 HTTPS / 下载成功 | 当时能建立连接及测得速度 | 同一个长连接整夜不中断 |
| 限速长下载 / 真实长任务 | 被测试连接在该时段的表现 | 所有应用以后都不会掉线 |

限速下载测试按设计会在指定时长到达时超时，curl 退出码28应结合已传输字节与实际持续时间判断，不能直接当作意外断线。服务端也可能主动结束测试下载。测试使用公共测速站，测速站自身状态会影响结果。

**建议至少在自己实际使用的成都宽带上比较白天、晚高峰和一个通宵，再决定是否减少其他线路。** 换运营商、换时间、换 VPS 都可能改变结果。AI任务还需要应用本身支持重试、保存进度和恢复；网络自动重连并不等于任务自动恢复。

## 撤销测试入口

运行菜单 **4：移除测试入口**，或：

```bash
python3 deploy.py remove
```

它只按本地记录定位测试域名及 Worker，并再次核对域名归属，要求输入 Worker 名称确认。VPS 原节点和本机正式代理不被修改。关闭两个独立测试 Xray 进程，测试端口即停止监听。

## 已知边界

- 仅支持 TCP 前置；不代理 UDP、QUIC 或 XUDP。WebSocket 早期数据与 MUX 默认关闭。
- 本次核对的 Xray 26.6.27 已对 WebSocket 显示弃用提醒；这是实验兼容接法，后续升级客户端时须重新验证，不能视为长期维护承诺。
- 原节点地址必须保持一致；若客户端将域名提前解析为 IP，Worker 会拒绝。用本工具生成的配置保留原域名，勿自行增加 `ForceIP`。
- Workers 原始 TCP 不能连接 Cloudflare IP 段。原节点地址应解析到 VPS 自己的公网地址。原 REALITY 节点的 DNS 记录不要因本项目而改成橙云。
- Workers 免费计划有每日请求和 CPU 等限制，额度不是稳定性承诺。
- Cloudflare 更新、客户端或服务器超时、宽带波动、VPS和目标服务故障均可能断开连接。这个工具不提供可用性SLA。
- `_local` 有部署和客户端密钥，只存本人设备。API失败、终端断开或证书尚未签发时，不可宣布部署已验证。

## 开发验证

```bash
npm ci
npm test
python3 -m unittest discover -s tests -p test_tools.py -v
npm run dry-run
```

测试包含协议分片、越权目标拒绝、UDP拒绝、凭据检查和本机 Workers 运行时的双向 TCP 回声。部署包检查与本地测试都不替代真实网络验收。

## 原始文档

- [Cloudflare TCP sockets 与连接限制](https://developers.cloudflare.com/workers/runtime-apis/tcp-sockets/)
- [WebSocket 二进制帧、生命周期与关闭行为](https://developers.cloudflare.com/workers/runtime-apis/websockets/)
- [Cloudflare 网络 WebSocket 与维护断连说明](https://developers.cloudflare.com/network/websockets/)
- [Workers 免费与付费计划限制](https://developers.cloudflare.com/workers/platform/limits/)
- [Worker 模块上传和 Secret 绑定](https://developers.cloudflare.com/workers/configuration/multipart-upload-metadata/)
- [Worker 自定义域名 API](https://developers.cloudflare.com/api/resources/workers/subresources/domains/methods/update/)
- [Xray 链式出站配置](https://github.com/XTLS/Xray-docs-next/blob/main/docs/config/transports/sockopt.md)

请仅在本人拥有或获准管理的服务器和账户上使用，并遵守服务提供方的使用条款。
