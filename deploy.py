#!/usr/bin/env python3
"""Deploy one fixed-target Cloudflare relay; Python stdlib only; no VPS changes."""
import argparse
import getpass
import ipaddress
import json
import os
import re
import sys
import uuid
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
STATE_DIR = ROOT / '_local'
API = 'https://api.cloudflare.com/client/v4'
DATE = '2026-10-21'


def write_private(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    payload = json.dumps(value, ensure_ascii=False, indent=2) + '\n'
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as stream:
        stream.write(payload)
    if os.name != 'nt':
        os.chmod(path, 0o600)


def valid_host(value):
    value = value.strip().lower().rstrip('.')
    if not re.fullmatch(r'(?=.{1,253}$)[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?', value):
        raise ValueError('请填写不含协议、路径和端口的域名或 IPv4 地址')
    if '..' in value or any(len(label) > 63 or label.startswith('-') or label.endswith('-') for label in value.split('.')):
        raise ValueError('域名格式无效')
    try:
        address = ipaddress.ip_address(value)
        if not address.is_global:
            raise ValueError('禁止把私网、回环或保留地址作为公网落地目标')
    except ValueError as exc:
        if re.fullmatch(r'[0-9.]+', value):
            raise ValueError('需要有效的公网 IPv4 地址') from exc
    if value in ('localhost', 'localhost.localdomain') or value.endswith(('.local', '.localhost', '.internal')):
        raise ValueError('不接受本地地址')
    return value


def validate_plan(plan, require_ids=True):
    for key in ('account_id', 'zone_id'):
        if not require_ids and not plan.get(key):
            continue
        if not re.fullmatch(r'[0-9a-f]{32}', plan[key]):
            raise ValueError(key + ' 应为控制台提供的 32 位 ID')
    plan['origin_host'] = valid_host(plan['origin_host'])
    plan['hostname'] = valid_host(plan['hostname'])
    if plan['origin_host'] == plan['hostname']:
        raise ValueError('中转域名不能等于原节点域名，否则会产生回环')
    if not re.fullmatch(r'[a-z][a-z0-9-]{2,62}', plan['worker_name']):
        raise ValueError('Worker 名称应为 3-63 个小写字母、数字或连字符')
    plan['origin_port'] = int(plan['origin_port'])
    if not 1 <= plan['origin_port'] <= 65535:
        raise ValueError('端口应在 1-65535 范围内')
    return plan


def resolve_zone(cf, plan):
    if plan.get('account_id') and plan.get('zone_id'):
        return plan
    if plan.get('account_id') or plan.get('zone_id'):
        raise ValueError('账户ID和区域ID须同时提供，或均交由程序查找')
    candidates = []
    for page in range(1, 21):
        zones = cf.call('GET', f'/zones?per_page=50&page={page}')
        candidates.extend(z for z in zones if plan['hostname'].endswith('.' + z['name']))
        if len(zones) < 50:
            break
    else:
        raise ValueError('可访问区域超过1000个，请用 --plan-file 明确指定区域')
    if not candidates:
        raise ValueError('令牌可访问区域中未找到该测试子域名；请检查权限和域名归属')
    candidates.sort(key=lambda zone: len(zone['name']), reverse=True)
    chosen = candidates[0]
    plan.update(account_id=chosen['account']['id'], zone_id=chosen['id'])
    return validate_plan(plan)


class Cloudflare:
    def __init__(self, token):
        self.token = token

    def call(self, method, path, body=None, content_type='application/json'):
        if body is not None and not isinstance(body, bytes):
            body = json.dumps(body).encode()
        request = Request(API + path, data=body, method=method, headers={
            'Authorization': 'Bearer ' + self.token,
            'Content-Type': content_type,
            'User-Agent': 'cloudflare-vps-relay/0.1.0',
        })
        try:
            with urlopen(request, timeout=45) as response:
                result = json.load(response)
        except HTTPError as exc:
            # Do not echo response bodies: upstream errors can contain credentials.
            raise RuntimeError(f'Cloudflare API 返回 HTTP {exc.code}；请检查令牌权限和控制台记录') from None
        except (URLError, TimeoutError):
            raise RuntimeError('Cloudflare API 连接未完成；结果可能未知，请先检查控制台，勿重复覆盖') from None
        if not result.get('success'):
            codes = [str(error.get('code', 'unknown')) for error in result.get('errors', [])]
            raise RuntimeError('Cloudflare API 拒绝请求，错误代码：' + ', '.join(codes))
        return result.get('result')


def multipart(plan, credential):
    boundary = 'cfrelay-' + uuid.uuid4().hex
    metadata = {
        'main_module': 'worker.mjs',
        'compatibility_date': DATE,
        'bindings': [
            {'type': 'plain_text', 'name': 'ORIGIN_HOST', 'text': plan['origin_host']},
            {'type': 'plain_text', 'name': 'ORIGIN_PORT', 'text': str(plan['origin_port'])},
            {'type': 'secret_text', 'name': 'RELAY_UUID', 'text': credential},
        ],
        'observability': {
            'enabled': True, 'head_sampling_rate': 0.1,
            'logs': {'enabled': True, 'invocation_logs': False},
            'traces': {'enabled': True, 'head_sampling_rate': 0.1},
        },
    }
    parts = [('metadata', 'application/json', json.dumps(metadata).encode(), None)]
    for name in ('worker.mjs', 'protocol.mjs'):
        parts.append((name, 'application/javascript+module', (ROOT / 'src' / name).read_bytes(), name))
    blocks = []
    for name, content_type, data, filename in parts:
        disposition = f'Content-Disposition: form-data; name="{name}"'
        if filename:
            disposition += f'; filename="{filename}"'
        blocks.append((f'--{boundary}\r\n{disposition}\r\nContent-Type: {content_type}\r\n\r\n').encode() + data + b'\r\n')
    blocks.append(f'--{boundary}--\r\n'.encode())
    return b''.join(blocks), 'multipart/form-data; boundary=' + boundary


def front_outbound(plan, credential):
    return {
        'tag': 'cf-front', 'protocol': 'vless',
        'settings': {'vnext': [{'address': plan['hostname'], 'port': 443,
                                'users': [{'id': credential, 'encryption': 'none'}]}]},
        'streamSettings': {
            'network': 'ws', 'security': 'tls',
            'tlsSettings': {'serverName': plan['hostname'], 'allowInsecure': False, 'fingerprint': 'chrome'},
            'wsSettings': {'path': '/relay', 'host': plan['hostname']},
        },
        'mux': {'enabled': False},
    }


def deploy(plan):
    validate_plan(plan, require_ids=False)
    state_path = STATE_DIR / 'deployment.json'
    if state_path.exists():
        raise ValueError('本地已有部署记录；请先执行 status，勿覆盖或丢失现有入口密钥')
    token = getpass.getpass('Cloudflare API 令牌（不回显、不保存）：').strip()
    if not token:
        raise ValueError('缺少 API 令牌')
    cf = Cloudflare(token)
    resolve_zone(cf, plan)
    base = '/accounts/' + plan['account_id'] + '/workers'
    scripts = cf.call('GET', base + '/scripts')
    if any(item.get('id') == plan['worker_name'] for item in scripts):
        raise ValueError('同名 Worker 已存在；为避免覆盖，请换一个名称')
    domains = cf.call('GET', base + '/domains')
    if any(item.get('hostname') == plan['hostname'] for item in domains):
        raise ValueError('此域名已绑定 Worker；请选择新的测试子域名')
    zone = cf.call('GET', '/zones/' + plan['zone_id'])
    if zone.get('account', {}).get('id') != plan['account_id']:
        raise ValueError('区域和账户不匹配')
    if zone.get('status') != 'active':
        raise ValueError('域名尚未激活，不能继续部署')
    zone_name = zone['name']
    if not plan['hostname'].endswith('.' + zone_name):
        raise ValueError('请使用该区域内的新测试子域名，不使用根域名')
    records = cf.call('GET', '/zones/' + plan['zone_id'] + '/dns_records?' + urlencode({'name': plan['hostname']}))
    if records:
        raise ValueError('测试子域名已有 DNS 记录；脚本不会替换业务记录')
    print('\n将新增：' + plan['worker_name'])
    print('入口：' + plan['hostname'] + ':443 → ' + plan['origin_host'] + ':' + str(plan['origin_port']))
    print('不修改 VPS、不改原节点、不改 SSH/防火墙。需要连接测试才能判断加速和稳定性。')
    if input('确认新增此公网测试入口，请输入 DEPLOY：').strip() != 'DEPLOY':
        print('已取消，未写入 Cloudflare。')
        return
    credential = str(uuid.uuid4())
    state = dict(plan, relay_uuid=credential, stage='prepared', domain_id=None)
    write_private(state_path, state)
    body, content_type = multipart(plan, credential)
    cf.call('PUT', base + '/scripts/' + plan['worker_name'], body, content_type)
    state['stage'] = 'script_uploaded'
    write_private(state_path, state)
    # Disable workers.dev and preview endpoints. Only the chosen hostname is exposed.
    cf.call('POST', base + '/scripts/' + plan['worker_name'] + '/subdomain', {'enabled': False, 'previews_enabled': False})
    result = cf.call('PUT', base + '/domains', {
        'hostname': plan['hostname'], 'service': plan['worker_name'],
        'zone_id': plan['zone_id'], 'zone_name': zone_name,
        'enabled': True, 'previews_enabled': False,
    })
    state.update(stage='domain_attached', domain_id=result['id'])
    write_private(state_path, state)
    write_private(STATE_DIR / 'front-outbound.json', front_outbound(plan, credential))
    print('入口已部署；证书生效可能需要等待。客户端前置配置保存在 _local/front-outbound.json。')
    print('这仅表示部署完成；尚未证明端到端可用、出口一致或通宵稳定。')


def get_state():
    return json.loads((STATE_DIR / 'deployment.json').read_text(encoding='utf-8'))


def status():
    state = get_state()
    print(json.dumps({k: state[k] for k in ('worker_name', 'hostname', 'origin_host', 'origin_port', 'stage')}, ensure_ascii=False, indent=2))
    print('密钥未显示。健康页只证明 HTTP 入口，不证明 TCP 中转或长连接稳定。')


def remove():
    state = get_state()
    cf = Cloudflare(getpass.getpass('Cloudflare API 令牌（不保存）：').strip())
    base = '/accounts/' + state['account_id'] + '/workers'
    print('仅删除本工具记录的测试 Worker 和测试域名；保留 VPS 原节点。')
    if input('请输入 Worker 名称确认删除：').strip() != state['worker_name']:
        return
    if state.get('domain_id'):
        domain = cf.call('GET', base + '/domains/' + state['domain_id'])
        if domain.get('service') != state['worker_name'] or domain.get('hostname') != state['hostname']:
            raise ValueError('域名归属已变化，拒绝删除')
        cf.call('DELETE', base + '/domains/' + state['domain_id'])
    cf.call('DELETE', base + '/scripts/' + state['worker_name'])
    state['stage'] = 'removed'
    write_private(STATE_DIR / 'deployment.json', state)
    print('测试入口已删除。本地记录保留，原 VPS 节点未修改。')


def interactive_plan():
    return validate_plan({
        'account_id': '',
        'zone_id': '',
        'worker_name': input('新 Worker 名称 [cf-vps-relay]：').strip() or 'cf-vps-relay',
        'hostname': input('新测试子域名，例如 relay.example.com：').strip(),
        'origin_host': input('原节点地址（与客户端填写的地址完全一致）：').strip(),
        'origin_port': input('原节点端口：').strip(),
    }, require_ids=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('deploy', 'plan', 'status', 'remove'), nargs='?', default='deploy')
    parser.add_argument('--plan-file', type=Path, help='非密配置 JSON 文件；令牌只通过交互输入')
    args = parser.parse_args()
    if args.action == 'status':
        status()
    elif args.action == 'remove':
        remove()
    else:
        plan = json.loads(args.plan_file.read_text(encoding='utf-8-sig')) if args.plan_file else interactive_plan()
        validate_plan(plan, require_ids=False)
        if args.action == 'plan':
            print(json.dumps(plan, ensure_ascii=False, indent=2))
            print('仅检查本地参数，没有联网或部署。')
        else:
            deploy(plan)


if __name__ == '__main__':
    try:
        main()
    except (ValueError, RuntimeError, OSError, KeyError) as exc:
        print('未完成：' + str(exc), file=sys.stderr)
        print('已有 _local/deployment.json 时请先检查状态；不要删掉记录后盲目重试。', file=sys.stderr)
        sys.exit(1)
