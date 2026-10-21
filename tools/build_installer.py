#!/usr/bin/env python3
"""Regenerate install.sh after changing any distributed tool file."""
import hashlib
import json
from pathlib import Path

root = Path(__file__).resolve().parents[1]
files = ['deploy.py', 'make_clients.py', 'benchmark.py', 'heartbeat_probe.py', 'src/worker.mjs', 'src/protocol.mjs', 'README.md', 'LICENSE']
manifest = {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in files}
script = r'''#!/usr/bin/env bash
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
manifest = __MANIFEST__
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
'''
script = script.replace('__MANIFEST__', json.dumps(manifest, ensure_ascii=True, indent=4))
(root / 'install.sh').write_text(script, encoding='utf-8', newline='\n')
print('install.sh regenerated with SHA-256 checks for', len(files), 'files')
