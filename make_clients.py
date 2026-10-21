#!/usr/bin/env python3
"""Create a direct/relay pair from one existing VLESS REALITY share link."""
import argparse
import copy
import json
import uuid
import ipaddress
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit
from deploy import front_outbound, write_private, valid_host


def parse_direct_link(link):
    uri = urlsplit(link.strip())
    query = {k: v[0] for k, v in parse_qs(uri.query).items()}
    if uri.scheme != 'vless' or not uri.username or not uri.hostname or not uri.port:
        raise ValueError('需要完整的 VLESS 分享链接')
    uuid.UUID(unquote(uri.username))
    if query.get('security') != 'reality' or query.get('type', 'tcp') not in ('tcp', 'raw'):
        raise ValueError('此版本只转换 VLESS + TCP/RAW + REALITY；其他协议保留原配置并另行核对')
    if not query.get('pbk') or not query.get('sni'):
        raise ValueError('REALITY 配置缺少 public key 或 SNI')
    if query.get('allowInsecure', '0').lower() not in ('0', 'false'):
        raise ValueError('不接受跳过证书校验的配置')
    host = valid_host(uri.hostname)
    user = {'id': unquote(uri.username), 'encryption': 'none'}
    if query.get('flow'):
        user['flow'] = query['flow']
    reality = {'serverName': query['sni'], 'fingerprint': query.get('fp', 'chrome'),
               'publicKey': query['pbk'], 'shortId': query.get('sid', '')}
    if query.get('spx'):
        reality['spiderX'] = query['spx']
    outbound = {
        'tag': 'proxy', 'protocol': 'vless',
        'settings': {'vnext': [{'address': host, 'port': uri.port, 'users': [user]}]},
        'streamSettings': {'network': 'raw', 'security': 'reality', 'realitySettings': reality,
                           'sockopt': {'domainStrategy': 'AsIs'}},
        'mux': {'enabled': False},
    }
    return outbound


def paired_configs(direct, state, source_ip=None):
    target = direct['settings']['vnext'][0]
    if target['address'].lower().rstrip('.') != state['origin_host'].lower().rstrip('.') or target['port'] != state['origin_port']:
        raise ValueError('原节点地址/端口与部署目标不一致；拒绝生成可能连错出口的配置')
    def base(port):
        return {'log': {'loglevel': 'warning'},
                'inbounds': [{'tag': 'local', 'listen': '127.0.0.1', 'port': port, 'protocol': 'http', 'settings': {}}],
                'outbounds': [copy.deepcopy(direct)],
                'routing': {'domainStrategy': 'AsIs', 'rules': [{'type': 'field', 'inboundTag': ['local'], 'outboundTag': 'proxy'}]}}
    original = base(18081)
    relay = base(18082)
    relay['outbounds'][0]['streamSettings']['sockopt']['dialerProxy'] = 'cf-front'
    relay['outbounds'].append(front_outbound(state, state['relay_uuid']))
    if source_ip:
        source_ip = str(ipaddress.ip_address(source_ip))
        original['outbounds'][0]['sendThrough'] = source_ip
        relay['outbounds'][-1]['sendThrough'] = source_ip
    return original, relay


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--direct-link-file', required=True, type=Path)
    parser.add_argument('--state', type=Path, default=Path(__file__).parent / '_local/deployment.json')
    parser.add_argument('--output', type=Path, default=Path(__file__).parent / '_local/clients')
    parser.add_argument('--source-ip', help='可选：仅给独立测试配置绑定物理网卡源IP，避免被现用TUN二次接管')
    args = parser.parse_args()
    state = json.loads(args.state.read_text(encoding='utf-8'))
    direct = parse_direct_link(args.direct_link_file.read_text(encoding='utf-8-sig'))
    a, b = paired_configs(direct, state, args.source_ip)
    write_private(args.output / 'direct.json', a)
    write_private(args.output / 'relay.json', b)
    print('两个配置已保存到：' + str(args.output.resolve()))
    print('直连测试代理端口 18081；中转测试代理端口 18082。未修改 v2rayN 或系统代理。')
    print('文件包含原节点凭据，仅在本人设备使用，禁止上传 GitHub。')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, KeyError) as exc:
        raise SystemExit('未生成：' + str(exc))
