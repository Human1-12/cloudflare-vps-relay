#!/usr/bin/env bash
# No root, no VPS service changes, no firewall changes.
set -eu
RELAY_DIR="${CF_RELAY_DIR:-$HOME/cf-vps-relay}"
RELAY_REPO="${CF_RELAY_REPO:-Human1-12/cloudflare-vps-relay}"
command -v python3 >/dev/null 2>&1 || { echo '需要 Python 3.9+；请先用系统软件包安装 python3。'; exit 1; }
python3 - "$RELAY_DIR" "$RELAY_REPO" <<'PYBOOT'
import hashlib, json, os, re, shutil, sys, tempfile
from datetime import datetime
from pathlib import Path
from urllib.request import urlopen

if sys.version_info < (3, 9):
    raise SystemExit('需要 Python 3.9 或更新版本。')
root = Path(sys.argv[1]).expanduser().resolve()
repo = sys.argv[2]
if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo):
    raise SystemExit('无效的仓库名称。')
manifest = {
    "deploy.py": "e335b9ef74d3f6b14eb3c8d033a83f24a057498d3737e13b305c54c9b4dcedb4",
    "make_clients.py": "84373f98f0c18064279173fc8ba9ac36ae2ed334ac17948bd834e7e5fcb02209",
    "benchmark.py": "45d467f2d99c7b7beca9d08789368563fcf0a2d9c48239557823df366725ef27",
    "heartbeat_probe.py": "fe6e5462d6295a6cc0ae8ab31bc0934d95dd393a529bcca756e14e45e560c99d",
    "src/worker.mjs": "a9813a491baab665b57ba4af298ca54418c32860060b37ad2d6d355c32dfd42a",
    "src/protocol.mjs": "8cce0c9e101a1417dc39e2578f89ece27336d18d964d30cdf42da2df2e5afc6b",
    "README.md": "0e138efef0af8adf010aa55b122beb02490d32c8182f2565c8c2d9a95d4e1efa",
    "LICENSE": "8359999d3abf35dd0469798435eb3668e4be04c3db2fbd4fb890b50fde6d21cc"
}
root.mkdir(parents=True, exist_ok=True, mode=0o700)
with tempfile.TemporaryDirectory(prefix='cfrelay-download-') as directory:
    stage = Path(directory)
    # Download and validate every file before updating any installed program file.
    for name, expected in manifest.items():
        with urlopen('https://raw.githubusercontent.com/' + repo + '/main/' + name, timeout=30) as response:
            content = response.read(2 * 1024 * 1024 + 1)
        if len(content) > 2 * 1024 * 1024 or hashlib.sha256(content).hexdigest() != expected:
            raise SystemExit('文件校验失败：' + name + '。未更新程序；请取得同一版本的安装脚本。')
        target = stage / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    backup = root / '_local' / 'backups' / datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    for name in manifest:
        target = root / name
        if target.exists() and target.read_bytes() != (stage / name).read_bytes():
            saved = backup / name
            saved.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            shutil.copy2(target, saved)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(stage / name, target)
print('程序文件已校验，位置：' + str(root))
print('本工具只新建 Cloudflare 测试入口，不修改 VPS 和现用节点。')
PYBOOT
cd "$RELAY_DIR"
echo '1  部署测试入口'
echo '2  生成直连/中转两份客户端配置'
echo '3  查看部署状态（不显示密钥）'
echo '4  移除本工具的测试入口'
echo '5  只看教程'
printf '请选择 [1]：'
read -r RELAY_CHOICE </dev/tty
case "${RELAY_CHOICE:-1}" in
  1) python3 deploy.py deploy </dev/tty ;;
  2) printf '原节点分享链接文本文件的完整路径：'; read -r RELAY_LINK_PATH </dev/tty
     python3 make_clients.py --direct-link-file "$RELAY_LINK_PATH" </dev/tty ;;
  3) python3 deploy.py status ;;
  4) python3 deploy.py remove </dev/tty ;;
  5) cat README.md ;;
  *) echo '已退出，未部署。' ;;
esac
