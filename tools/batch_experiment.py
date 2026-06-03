#!/usr/bin/env python3
"""
Batch experiment runner for hpcc_rdma.

Features:
- Accept cc modes, methods, loss list, topology line number to modify (0=no change)
- Copy topology (if modifying) into run dir and edit specified line's last field
- Generate a config.txt per run (based on a template config)
- Run the simulation via waf and wait for completion
- Archive FCT output to output/loss<loss>/cc<mode>/<method>/

Usage: python3 tools/batch_experiment.py --losses 0.0,0.001,... --topo-line 350 --methods psn-path,gbn --ccs 1,3
"""
import argparse
import os
import random
import shlex
import shutil
import subprocess
from datetime import datetime


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
    """Replace a line that starts with key (key and value separated by space).
    If not found, append at end."""
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


DEFAULT_LOSSES = '0.0001,0.0005,0.001,0.002,0.004,0.006,0.008,0.1'
DEFAULT_TOPO_BLOCK_FIRST_LINE = 3
DEFAULT_TOPO_BLOCK_LAST_START = 3843
DEFAULT_TOPO_BLOCK_STRIDE = 256
DEFAULT_TOPO_BLOCK_SIZE = 128


METHOD_FLAGS = {
    'psn-path': {'ENABLE_PSN_PATH': 1, 'ENABLE_PATH_SWITCH': 1, 'ENABLE_PATH_AWARE_RETRANS': 1,
                 'ENABLE_RX_OOO_NACK': 0, 'ENABLE_TX_NACK_GOBACK': 0, 'ENABLE_BITMAP_RETRANS': 0, 'ENABLE_FALCON': 0},
    'gbn': {'ENABLE_PSN_PATH': 0, 'ENABLE_PATH_SWITCH': 0, 'ENABLE_PATH_AWARE_RETRANS': 0,
            'ENABLE_RX_OOO_NACK': 1, 'ENABLE_TX_NACK_GOBACK': 1, 'ENABLE_BITMAP_RETRANS': 0, 'ENABLE_FALCON': 0},
    'bitmap': {'ENABLE_PSN_PATH': 0, 'ENABLE_PATH_SWITCH': 0, 'ENABLE_PATH_AWARE_RETRANS': 0,
               'ENABLE_RX_OOO_NACK': 0, 'ENABLE_TX_NACK_GOBACK': 0, 'ENABLE_BITMAP_RETRANS': 1, 'ENABLE_FALCON': 0},
    'falcon': {'ENABLE_PSN_PATH': 0, 'ENABLE_PATH_SWITCH': 0, 'ENABLE_PATH_AWARE_RETRANS': 0,
               'ENABLE_RX_OOO_NACK': 0, 'ENABLE_TX_NACK_GOBACK': 0, 'ENABLE_BITMAP_RETRANS': 0, 'ENABLE_FALCON': 1},
}


def default_topo_lines():
    lines = []
    start = DEFAULT_TOPO_BLOCK_FIRST_LINE
    while start <= DEFAULT_TOPO_BLOCK_LAST_START:
        lines.extend(range(start, start + DEFAULT_TOPO_BLOCK_SIZE))
        start += DEFAULT_TOPO_BLOCK_STRIDE
    return lines


def ensure_output_dir(work_dir):
    output_dir = os.path.join(work_dir, 'mix', 'output')
    os.makedirs(output_dir, exist_ok=True)
    if not os.access(output_dir, os.W_OK):
        raise RuntimeError(
            f"{output_dir} is not writable. Fix its owner/permissions before running experiments.")


def run_one(template_config_path, topo_path, topo_lines, cc_mode, method, loss, waf_cmd, work_dir, archive_root, dry_run=False, timeout=3600):
    runid = unique_id()
    run_dir = os.path.join(work_dir, 'mix', 'output', runid)
    os.makedirs(run_dir, exist_ok=True)

    # topology handling
    topo_target = None
    if topo_lines and len(topo_lines) > 0:
        topo_orig_lines = read_lines(topo_path)
        topo_mod_lines = topo_orig_lines.copy()
        for topo_line in topo_lines:
            idx = topo_line - 1
            if idx < 0 or idx >= len(topo_mod_lines):
                raise RuntimeError(f"topo_line {topo_line} out of range (1..{len(topo_mod_lines)})")
            parts = topo_mod_lines[idx].rstrip('\n').split()
            if len(parts) < 5:
                raise RuntimeError(f"topology line {topo_line} doesn't look like a link line: {topo_mod_lines[idx]}")
            parts[-1] = str(loss)
            topo_mod_lines[idx] = ' '.join(parts) + '\n'
        topo_target = os.path.join(run_dir, os.path.basename(topo_path))
        write_lines(topo_target, topo_mod_lines)
    else:
        # no change: reference original topology file
        topo_target = topo_path

    # config: read template and replace keys
    cfg_lines = read_lines(template_config_path)

    # set file paths to use this run dir
    cfg_lines = replace_config_key(cfg_lines, 'TOPOLOGY_FILE', topo_target)
    cfg_lines = replace_config_key(cfg_lines, 'FLOW_FILE', 'config/1_flow.txt')
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

    # set method flags
    flags = METHOD_FLAGS.get(method)
    if flags is None:
        raise RuntimeError(f"unknown method {method}")
    for k, v in flags.items():
        cfg_lines = replace_config_key(cfg_lines, k, v)

    # write config
    cfg_path = os.path.join(run_dir, 'config.txt')
    write_lines(cfg_path, cfg_lines)

    # run simulation
    log_path = os.path.join(run_dir, 'config.log')
    run_arg = f"scratch/network-load-balance {cfg_path}"
    run_cmd_display = f"{waf_cmd} --run '{run_arg}' > {log_path} 2>&1"
    run_args = shlex.split(waf_cmd) + ['--run', run_arg]
    print(f"Run {runid}: cc={cc_mode} method={method} loss={loss} -> {run_cmd_display}")
    if dry_run:
        return {'runid': runid, 'status': 'dry-run', 'config': cfg_path, 'log': log_path, 'archived': None}

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
        # archive
        loss_dir = os.path.join(archive_root, f"loss{loss}")
        cc_dir = os.path.join(loss_dir, f"cc{cc_mode}")
        method_dir = os.path.join(cc_dir, method)
        os.makedirs(method_dir, exist_ok=True)
        archived = os.path.join(method_dir, f"{runid}.txt")
        shutil.copy(fct_file, archived)
    elif status == 'ok':
        status = 'empty-fct' if os.path.exists(fct_file) else 'missing-fct'
        print(f"Warning: FCT file is {status.replace('-', ' ')} for run {runid}")
    else:
        print(f"Warning: skip archive for run {runid} because status={status}")

    return {'runid': runid, 'status': status, 'rc': rc, 'fct': fct_file, 'archived': archived, 'log': log_path}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--topo-line', type=int, default=0, help='(deprecated) single topology line number to modify (1-based)')
    parser.add_argument('--topo-lines', type=str, default='', help='comma-separated topology line numbers to modify (1-based); empty uses the built-in 32-line blocks')
    parser.add_argument('--topo-file', default='config/1_topology.txt')
    parser.add_argument('--template-config', default='mix/output/1/config.txt')
    parser.add_argument('--losses', default=DEFAULT_LOSSES,
                        help='comma separated loss values')
    parser.add_argument('--methods', default='psn-path,gbn,bitmap,falcon')
    parser.add_argument('--ccs', default='1,3', help='comma separated CC modes (numbers)')
    parser.add_argument('--work-dir', default='.')
    parser.add_argument('--archive-root', default='output')
    parser.add_argument('--waf-cmd', default='python2 ./waf')
    parser.add_argument('--timeout', type=int, default=3600, help='seconds before marking one run as timeout')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()

    losses = [s.strip() for s in args.losses.split(',') if s.strip()!='']
    methods = [s.strip() for s in args.methods.split(',') if s.strip()!='']
    ccs = [s.strip() for s in args.ccs.split(',') if s.strip()!='']
    # parse topo lines
    topo_lines = []
    if args.topo_lines:
        for s in args.topo_lines.split(','):
            s = s.strip()
            if not s:
                continue
            topo_lines.append(int(s))
    elif args.topo_line and args.topo_line > 0:
        topo_lines = [args.topo_line]
    else:
        topo_lines = default_topo_lines()

    ensure_output_dir(args.work_dir)

    results = []
    for cc in ccs:
        for method in methods:
            for loss in losses:
                try:
                    r = run_one(args.template_config, args.topo_file, topo_lines, cc, method, loss, args.waf_cmd, args.work_dir, args.archive_root, dry_run=args.dry_run, timeout=args.timeout)
                    results.append(r)
                except Exception as e:
                    print(f"Error running combo cc={cc} method={method} loss={loss}: {e}")
                    results.append({'status': 'error', 'cc': cc, 'method': method, 'loss': loss, 'error': str(e)})

    print('\nSummary:')
    for r in results:
        print(r)


if __name__ == '__main__':
    main()
