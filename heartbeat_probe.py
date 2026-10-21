#!/usr/bin/env python3
"""Measure actual periodic data on the same HTTPS connection through each route."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import time
from pathlib import Path
from urllib.request import ProxyHandler, Request, build_opener
from benchmark import local_proxy


def run_route(proxy, state, seconds):
    started = time.monotonic()
    records = []
    result = {'ok': False, 'events': 0, 'seconds': 0, 'max_gap_seconds': 0, 'server_completed': False}
    try:
        # Explicit proxy handler: an unreachable local proxy raises an error; there is no direct retry.
        opener = build_opener(ProxyHandler({'http': proxy, 'https': proxy}))
        request = Request('https://' + state['hostname'] + '/probe?seconds=' + str(seconds),
                          headers={'X-Relay-Probe': state['relay_uuid'], 'Accept': 'application/x-ndjson',
                                   'User-Agent': 'Cloudflare-VPS-Relay-Test/0.1.0'})
        with opener.open(request, timeout=25) as response:
            for line in response:
                event = json.loads(line)
                records.append((time.monotonic() - started, event))
        gaps = [records[i][0] - records[i - 1][0] for i in range(1, len(records))]
        elapsed = time.monotonic() - started
        completed = bool(records and records[-1][1].get('done'))
        result.update(ok=completed and elapsed >= seconds - 1, events=len(records), seconds=round(elapsed, 3),
                      max_gap_seconds=round(max(gaps, default=0), 3), server_completed=completed)
    except Exception as exc:
        result['error_type'] = type(exc).__name__
        result['http_code'] = getattr(exc, 'code', None)
        result['seconds'] = round(time.monotonic() - started, 3)
        result['events'] = len(records)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', type=Path, default=Path(__file__).parent / '_local/deployment.json')
    parser.add_argument('--seconds', type=int, default=180)
    parser.add_argument('--direct', type=local_proxy, default='http://127.0.0.1:18081')
    parser.add_argument('--relay', type=local_proxy, default='http://127.0.0.1:18082')
    parser.add_argument('--output', type=Path, default=Path(__file__).parent / '_local/heartbeat-result.json')
    args = parser.parse_args()
    if not 1 <= args.seconds <= 300:
        raise SystemExit('单次测试1-300秒；它不构成通宵稳定性证明。')
    state = json.loads(args.state.read_text(encoding='utf-8'))
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = {name:pool.submit(run_route, proxy, state, args.seconds) for name,proxy in [('direct',args.direct),('relay',args.relay)]}
        result = {name:job.result() for name,job in jobs.items()}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False))


if __name__ == '__main__': main()
