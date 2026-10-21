#!/usr/bin/env python3
"""Compare two explicitly selected local HTTP proxies; never fall back to direct."""
import argparse
import csv
import hashlib
import json
import shutil
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

TRACE = 'https://www.cloudflare.com/cdn-cgi/trace'
DOWNLOAD = 'https://speed.cloudflare.com/__down?bytes='


def local_proxy(value):
    parsed = urlsplit(value)
    if parsed.scheme != 'http' or parsed.hostname not in ('127.0.0.1', 'localhost', '::1') or not parsed.port or parsed.username:
        raise argparse.ArgumentTypeError('只接受本机 HTTP 代理，例如 http://127.0.0.1:18081')
    return value


def probe(proxy, url, seconds=40, rate=None):
    with tempfile.TemporaryDirectory(prefix='cfrelay-probe-') as directory:
        body = Path(directory) / 'body'
        command = ['curl', '--silent', '--show-error', '--proxy', proxy, '--noproxy', '',
                   '--connect-timeout', '12', '--max-time', str(seconds), '--output', str(body), '--write-out', '%{json}', url]
        if rate:
            command.extend(['--limit-rate', rate])
        result = subprocess.run(command, capture_output=True, text=True, timeout=seconds + 10)
        try:
            metrics = json.loads(result.stdout)
        except json.JSONDecodeError:
            metrics = {}
        exit_ip = ''
        if url == TRACE and body.exists():
            for line in body.read_text(encoding='utf-8', errors='replace').splitlines():
                if line.startswith('ip='):
                    exit_ip = line[3:]
        return {
            'ok': result.returncode == 0 and metrics.get('http_code') == 200,
            'curl_exit': result.returncode,
            'http_code': metrics.get('http_code', 0),
            'seconds': metrics.get('time_total', 0),
            'first_byte_seconds': metrics.get('time_starttransfer', 0),
            'bytes_per_second': metrics.get('speed_download', 0),
            'bytes': metrics.get('size_download', 0),
            'exit_fingerprint': hashlib.sha256(exit_ip.encode()).hexdigest()[:16] if exit_ip else '',
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--direct', type=local_proxy, default='http://127.0.0.1:18081')
    parser.add_argument('--relay', type=local_proxy, default='http://127.0.0.1:18082')
    parser.add_argument('--hours', type=float, default=0, help='0=只比较一轮；8=连续采样8小时')
    parser.add_argument('--interval', type=int, default=60)
    parser.add_argument('--download-mb', type=int, default=0, help='每轮每条线路额外下载的MiB；默认0')
    parser.add_argument('--stream-minutes', type=int, default=0, help='两条线路各做一次限速长下载；会消耗流量')
    parser.add_argument('--output', type=Path, default=Path(__file__).parent / '_local/benchmark.csv')
    args = parser.parse_args()
    if not shutil.which('curl'):
        raise SystemExit('缺少 curl；先安装系统发行版提供的 curl。')
    if args.hours < 0 or args.hours > 168 or args.interval < 15 or not 0 <= args.download_mb <= 1024 or not 0 <= args.stream_minutes <= 480:
        raise SystemExit('参数超出范围：hours 0-168，interval ≥15，download-mb 0-1024，stream-minutes 0-480。')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    fields = ['utc', 'route', 'kind', 'ok', 'curl_exit', 'http_code', 'seconds', 'first_byte_seconds', 'bytes_per_second', 'bytes', 'exit_fingerprint']
    new_file = not args.output.exists()
    deadline = time.monotonic() + args.hours * 3600
    with args.output.open('a', newline='', encoding='utf-8') as file:
        writer = csv.DictWriter(file, fields)
        if new_file:
            writer.writeheader()
        def record(route, kind, result):
            writer.writerow(dict(utc=datetime.now(timezone.utc).isoformat(), route=route, kind=kind, **result))
            file.flush()
            print(route, kind, '成功' if result['ok'] else '失败', f"{result['seconds']:.2f}s", flush=True)
        while True:
            exits = []
            for route, proxy in [('direct', args.direct), ('relay', args.relay)]:
                result = probe(proxy, TRACE)
                record(route, 'short_https', result)
                exits.append(result['exit_fingerprint'])
                if args.download_mb:
                    record(route, 'download', probe(proxy, DOWNLOAD + str(args.download_mb * 1024 * 1024), 180))
            if all(exits) and exits[0] != exits[1]:
                print('观察到两个出口指纹不同，请检查出口和IPv4/IPv6；本结果不能直接证明更换了VPS。', flush=True)
            if time.monotonic() >= deadline:
                break
            time.sleep(min(args.interval, max(0, deadline - time.monotonic())))
        if args.stream_minutes:
            duration = args.stream_minutes * 60
            # 64KiB/s: up to ~1.76GiB per route for 8h. Time limits are visible results.
            size = duration * 65536 + 1048576
            print('限速下载测试开始；它是模拟连接，不代表真实AI长任务。', flush=True)
            for route, proxy in [('direct', args.direct), ('relay', args.relay)]:
                record(route, 'limited_stream', probe(proxy, DOWNLOAD + str(size), duration, '64k'))
    print('记录：' + str(args.output.resolve()))
    print('周期采样验证重复建立HTTPS连接；长下载测试与真实AI任务必须分别评估。')


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('\n已停止，已写入的采样保留。')
