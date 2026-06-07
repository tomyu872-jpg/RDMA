#!/usr/bin/env python3
"""
Batch experiment runner for varying flow counts.

Features:
- Keep topology unchanged
- Run one experiment for each flow file in the web-flow directory by default
- Accept retransmission methods and CC modes
- Generate a config.txt per run from a template config
- Run simulations with multiple worker threads
- Archive FCT output to output_flow/<method>/cc<mode>/flows<count>/

Usage:
  python3 tools/batch_experiment_flowcount.py
  python3 tools/batch_experiment_flowcount.py --methods falcon,ornic --ccs 3 --workers 4
  python3 tools/batch_experiment_flowcount.py --flow-files total_flows_31.txt,total_flows_200.txt
"""
import argparse
import concurrent.futures
import glob
import os
import random
import re
import shlex
import shutil
import subprocess
from datetime import datetime


DEFAULT_METHODS = 'psn_path,mpirn,gbn,falcon,ornic'
DEFAULT_CCS = '1,3'
DEFAULT_FLOW_DIR = '配置文件/大中小流/web-flow'


METHOD_FLAGS = {
    'psn_path': {'ENABLE_PSN_PATH': 1, 'ENABLE_PATH_SWITCH': 1, 'ENABLE_PATH_AWARE_RETRANS': 1,
                 'ENABLE_RX_OOO_NACK': 0, 'ENABLE_TX_NACK_GOBACK': 0, 'ENABLE_BITMAP_RETRANS': 0,
                 'ENABLE_FALCON': 0, 'ENABLE_ORNIC': 0, 'ORNIC_BW_GBPS': 100,
                 'ORNIC_RX_SEND_DELAY_NS': 9000},
    'gbn': {'ENABLE_PSN_PATH': 0, 'ENABLE_PATH_SWITCH': 0, 'ENABLE_PATH_AWARE_RETRANS': 0,
            'ENABLE_RX_OOO_NACK': 1, 'ENABLE_TX_NACK_GOBACK': 1, 'ENABLE_BITMAP_RETRANS': 0,
            'ENABLE_FALCON': 0, 'ENABLE_ORNIC': 0, 'ORNIC_BW_GBPS': 100,
            'ORNIC_RX_SEND_DELAY_NS': 9000},
    'mpirn': {'ENABLE_PSN_PATH': 0, 'ENABLE_PATH_SWITCH': 0, 'ENABLE_PATH_AWARE_RETRANS': 0,
              'ENABLE_RX_OOO_NACK': 0, 'ENABLE_TX_NACK_GOBACK': 0, 'ENABLE_BITMAP_RETRANS': 1,
              'ENABLE_FALCON': 0, 'ENABLE_ORNIC': 0, 'ORNIC_BW_GBPS': 100,
              'ORNIC_RX_SEND_DELAY_NS': 9000},
    'falcon': {'ENABLE_PSN_PATH': 0, 'ENABLE_PATH_SWITCH': 0, 'ENABLE_PATH_AWARE_RETRANS': 0,
               'ENABLE_RX_OOO_NACK': 0, 'ENABLE_TX_NACK_GOBACK': 0, 'ENABLE_BITMAP_RETRANS': 0,
               'ENABLE_FALCON': 1, 'FALCON_RX_SEND_DELAY_NS': 0, 'ENABLE_ORNIC': 0,
               'ORNIC_BW_GBPS': 100, 'ORNIC_RX_SEND_DELAY_NS': 9000},
    'ornic': {'ENABLE_PSN_PATH': 0, 'ENABLE_PATH_SWITCH': 0, 'ENABLE_PATH_AWARE_RETRANS': 0,
              'ENABLE_RX_OOO_NACK': 0, 'ENABLE_TX_NACK_GOBACK': 0, 'ENABLE_BITMAP_RETRANS': 0,
              'ENABLE_FALCON': 0, 'ENABLE_ORNIC': 1, 'ORNIC_BW_GBPS': 100,
              'ORNIC_RX_SEND_DELAY_NS': 9000},
}

METHOD_ALIASES = {
    'psn-path': 'psn_path',
    'psn_path': 'psn_path',
    'gbn': 'gbn',
    'bitmap': 'mpirn',
    'mpirn': 'mpirn',
    'falcon': 'falcon',
    'ornic': 'ornic',
}


def unique_id():
    return datetime.now().strftime("%Y%m%d%H%M%S") + "_%04d" % random.randrange(10000)


def read_lines(path):
    with open(path, 'r') as f:
        return f.readlines()


def write_lines(path, lines):
    d = os.path.dirname(path)
    if d and not os.path.exists(d):
        os.makedirs(d)
    with open(path, 'w') as f:
        f.writelines(lines)


def replace_config_key(lines, key, value):
    out = []
    found = False
    for ln in lines:
        parts = ln.strip().split(None, 1)
        if parts and parts[0] == key:
            out.append(f"{key} {value}\n")
            found = True
        else:
            out.append(ln)
    if not found:
        out.append(f"{key} {value}\n")
    return out


def normalize_method(method):
    normalized = METHOD_ALIASES.get(method.strip().lower())
    if normalized is None:
        known = ','.join(sorted(METHOD_ALIASES))
        raise RuntimeError(f"unknown method {method}; known methods/aliases: {known}")
    return normalized


def flow_count_from_file(flow_file):
    with open(flow_file, 'r') as f:
        first = f.readline().strip()
    try:
        return int(first)
    except ValueError:
        m = re.search(r'(\d+)', os.path.basename(flow_file))
        if not m:
            raise RuntimeError(f"cannot determine flow count from {flow_file}")
        return int(m.group(1))


def resolve_flow_files(flow_dir, flow_files):
    if flow_files:
        paths = []
        for item in flow_files.split(','):
            item = item.strip()
            if not item:
                continue
            paths.append(item if os.path.isabs(item) else os.path.join(flow_dir, item))
    else:
        paths = glob.glob(os.path.join(flow_dir, 'total_flows_*.txt'))

    if not paths:
        raise RuntimeError(f"no flow files found in {flow_dir}")

    missing = [p for p in paths if not os.path.exists(p)]
    if missing:
        raise RuntimeError("missing flow files: " + ','.join(missing))

    return sorted(paths, key=lambda p: (flow_count_from_file(p), p))


def ensure_output_dir(work_dir):
    output_dir = os.path.join(work_dir, 'mix', 'output')
    os.makedirs(output_dir, exist_ok=True)
    if not os.access(output_dir, os.W_OK):
        raise RuntimeError(
            f"{output_dir} is not writable. Fix its owner/permissions before running experiments.")


def run_task(task):
    try:
        result = run_one(
            task['template_config'],
            task['flow_file'],
            task['cc'],
            task['method'],
            task['waf_cmd'],
            task['work_dir'],
            task['archive_root'],
            dry_run=task['dry_run'],
            timeout=task['timeout'])
        result['cc'] = task['cc']
        result['method'] = task['method']
        result['flow_file'] = task['flow_file']
        result['flow_count'] = task['flow_count']
        return result
    except Exception as e:
        print(f"Error running combo cc={task['cc']} method={task['method']} "
              f"flows={task['flow_count']}: {e}")
        return {
            'status': 'error',
            'cc': task['cc'],
            'method': task['method'],
            'flow_file': task['flow_file'],
            'flow_count': task['flow_count'],
            'error': str(e),
        }


def run_one(template_config_path, flow_file, cc_mode, method, waf_cmd, work_dir,
            archive_root, dry_run=False, timeout=3600):
    runid = unique_id()
    run_dir = os.path.join(work_dir, 'mix', 'output', runid)
    os.makedirs(run_dir, exist_ok=True)
    flow_count = flow_count_from_file(flow_file)

    cfg_lines = read_lines(template_config_path)

    cfg_lines = replace_config_key(cfg_lines, 'FLOW_FILE', flow_file)
    cfg_lines = replace_config_key(cfg_lines, 'FLOW_INPUT_FILE', f"mix/output/{runid}/{runid}_in.txt")
    cfg_lines = replace_config_key(cfg_lines, 'CNP_OUTPUT_FILE', f"mix/output/{runid}/{runid}_out_cnp.txt")
    cfg_lines = replace_config_key(cfg_lines, 'FCT_OUTPUT_FILE', f"mix/output/{runid}/{runid}_out_fct.txt")
    cfg_lines = replace_config_key(cfg_lines, 'PFC_OUTPUT_FILE', f"mix/output/{runid}/{runid}_out_pfc.txt")
    cfg_lines = replace_config_key(cfg_lines, 'QLEN_MON_FILE', f"mix/output/{runid}/{runid}_out_qlen.txt")
    cfg_lines = replace_config_key(cfg_lines, 'VOQ_MON_FILE', f"mix/output/{runid}/{runid}_out_voq.txt")
    cfg_lines = replace_config_key(cfg_lines, 'VOQ_MON_DETAIL_FILE', f"mix/output/{runid}/{runid}_out_voq_per_dst.txt")
    cfg_lines = replace_config_key(cfg_lines, 'UPLINK_MON_FILE', f"mix/output/{runid}/{runid}_out_uplink.txt")
    cfg_lines = replace_config_key(cfg_lines, 'CONN_MON_FILE', f"mix/output/{runid}/{runid}_out_conn.txt")
    cfg_lines = replace_config_key(cfg_lines, 'EST_ERROR_MON_FILE', f"mix/output/{runid}/{runid}_out_est_error.txt")

    cfg_lines = replace_config_key(cfg_lines, 'CC_MODE', cc_mode)

    flags = METHOD_FLAGS.get(method)
    if flags is None:
        raise RuntimeError(f"unknown method {method}")
    for k, v in flags.items():
        cfg_lines = replace_config_key(cfg_lines, k, v)

    cfg_path = os.path.join(run_dir, 'config.txt')
    write_lines(cfg_path, cfg_lines)

    log_path = os.path.join(run_dir, 'config.log')
    run_arg = f"scratch/network-load-balance {cfg_path}"
    run_cmd_display = f"{waf_cmd} --run '{run_arg}' > {log_path} 2>&1"
    run_args = shlex.split(waf_cmd) + ['--run', run_arg]
    print(f"Run {runid}: cc={cc_mode} method={method} flows={flow_count} -> {run_cmd_display}")
    if dry_run:
        return {'runid': runid, 'status': 'dry-run', 'config': cfg_path,
                'log': log_path, 'archived': None}

    status = 'ok'
    rc = None
    try:
        with open(log_path, 'w') as log_file:
            rc = subprocess.call(run_args, stdout=log_file, stderr=subprocess.STDOUT, timeout=timeout)
    except subprocess.TimeoutExpired:
        status = 'timeout'
        print(f"Warning: run {runid} timed out after {timeout}s")

    if rc not in (0, None):
        status = 'failed'
        print(f"Warning: run {runid} exited with rc={rc}")

    fct_file = os.path.join(run_dir, f"{runid}_out_fct.txt")
    archived = None
    if os.path.exists(fct_file) and os.path.getsize(fct_file) > 0 and status == 'ok':
        archive_dir = os.path.join(archive_root, method, f"cc{cc_mode}", f"flows{flow_count}")
        os.makedirs(archive_dir, exist_ok=True)
        archived = os.path.join(archive_dir, f"{runid}.txt")
        shutil.copy(fct_file, archived)
    elif status == 'ok':
        status = 'empty-fct' if os.path.exists(fct_file) else 'missing-fct'
        print(f"Warning: FCT file is {status.replace('-', ' ')} for run {runid}")
    else:
        print(f"Warning: skip archive for run {runid} because status={status}")

    return {'runid': runid, 'status': status, 'rc': rc, 'fct': fct_file,
            'archived': archived, 'log': log_path}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--flow-dir', default=DEFAULT_FLOW_DIR)
    parser.add_argument('--flow-files', default='',
                        help='comma separated flow files; relative paths are resolved under --flow-dir')
    parser.add_argument('--template-config', default='mix/output/1/config.txt')
    parser.add_argument('--methods', default=DEFAULT_METHODS,
                        help='comma separated retransmission methods: psn_path,mpirn,gbn,falcon,ornic')
    parser.add_argument('--ccs', default=DEFAULT_CCS, help='comma separated CC modes')
    parser.add_argument('--work-dir', default='.')
    parser.add_argument('--archive-root', default='output_flow')
    parser.add_argument('--waf-cmd', default='python2 ./waf')
    parser.add_argument('--timeout', type=int, default=3600)
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()

    flow_files = resolve_flow_files(args.flow_dir, args.flow_files)
    methods = [normalize_method(s) for s in args.methods.split(',') if s.strip() != '']
    ccs = [s.strip() for s in args.ccs.split(',') if s.strip() != '']

    ensure_output_dir(args.work_dir)

    tasks = []
    for cc in ccs:
        for method in methods:
            for flow_file in flow_files:
                tasks.append({
                    'template_config': args.template_config,
                    'flow_file': flow_file,
                    'flow_count': flow_count_from_file(flow_file),
                    'cc': cc,
                    'method': method,
                    'waf_cmd': args.waf_cmd,
                    'work_dir': args.work_dir,
                    'archive_root': args.archive_root,
                    'dry_run': args.dry_run,
                    'timeout': args.timeout,
                })

    results = []
    workers = max(1, args.workers)
    print(f"Starting {len(tasks)} experiment tasks with {workers} worker threads")
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(run_task, task) for task in tasks]
        for future in concurrent.futures.as_completed(futures):
            try:
                results.append(future.result())
            except Exception as e:
                print(f"Error collecting task result: {e}")
                results.append({'status': 'error', 'error': str(e)})

    print('\nSummary:')
    for r in results:
        print(r)


if __name__ == '__main__':
    main()
