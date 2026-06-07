#!/usr/bin/env python3
"""
Batch experiment runner for PSN_PATH_K.

Default experiment:
  method=psn_path, cc=3, K in 1,4,8,16,32

Outputs:
  output_k/psn_path/cc3/k<K>/<runid>/
  output_k/psn_path/cc3/summary.csv
"""
import argparse
import concurrent.futures
import csv
import os
import random
import re
import shlex
import shutil
import subprocess
from datetime import datetime
from statistics import mean


DEFAULT_KS = "1,4,8,16,32"
DEFAULT_METHOD = "psn_path"
DEFAULT_CC = "3"

METHOD_FLAGS = {
    "psn_path": {
        "ENABLE_PSN_PATH": 1,
        "ENABLE_PATH_SWITCH": 1,
        "ENABLE_PATH_AWARE_RETRANS": 1,
        "ENABLE_RX_OOO_NACK": 0,
        "ENABLE_TX_NACK_GOBACK": 0,
        "ENABLE_BITMAP_RETRANS": 0,
        "ENABLE_FALCON": 0,
        "ENABLE_ORNIC": 0,
    },
}

RUN_OUTPUT_KEYS = {
    "FLOW_INPUT_FILE": "{runid}_in.txt",
    "CNP_OUTPUT_FILE": "{runid}_out_cnp.txt",
    "FCT_OUTPUT_FILE": "{runid}_out_fct.txt",
    "PFC_OUTPUT_FILE": "{runid}_out_pfc.txt",
    "QLEN_MON_FILE": "{runid}_out_qlen.txt",
    "VOQ_MON_FILE": "{runid}_out_voq.txt",
    "VOQ_MON_DETAIL_FILE": "{runid}_out_voq_per_dst.txt",
    "UPLINK_MON_FILE": "{runid}_out_uplink.txt",
    "CONN_MON_FILE": "{runid}_out_conn.txt",
    "EST_ERROR_MON_FILE": "{runid}_out_est_error.txt",
    "BURST_LOSS_LOG_FILE": "{runid}_out_burst_loss.txt",
    "BURST_GOODPUT_OUTPUT_FILE": "{runid}_out_burst_goodput.txt",
    "BURST_REDUNDANCY_OUTPUT_FILE": "{runid}_out_burst_redundancy.txt",
}


def unique_id(k_value):
    ts = datetime.now().strftime("%Y%m%d%H%M%S")
    return "k{}_{}_{}".format(k_value, ts, random.randrange(10000))


def read_lines(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.readlines()


def write_lines(path, lines):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.writelines(lines)


def replace_config_key(lines, key, value):
    out = []
    found = False
    for line in lines:
        parts = line.strip().split(None, 1)
        if parts and parts[0] == key:
            out.append("{} {}\n".format(key, value))
            found = True
        else:
            out.append(line)
    if not found:
        out.append("{} {}\n".format(key, value))
    return out


def parse_int_list(value):
    return [int(item.strip()) for item in value.split(",") if item.strip()]


def numeric_rows(path):
    rows = []
    if not path or not os.path.exists(path):
        return rows
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                rows.append([float(x) for x in stripped.split()])
            except ValueError:
                continue
    return rows


def percentile(values, pct):
    if not values:
        return ""
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * pct
    lo = int(rank)
    hi = min(lo + 1, len(ordered) - 1)
    frac = rank - lo
    return ordered[lo] * (1.0 - frac) + ordered[hi] * frac


def summarize_fct(path):
    rows = numeric_rows(path)
    fcts_us = []
    slowdowns = []
    for row in rows:
        if len(row) < 8:
            continue
        fct_ns = row[6]
        base_ns = row[7]
        fcts_us.append(fct_ns / 1000.0)
        if base_ns > 0:
            slowdowns.append(max(1.0, fct_ns / base_ns))
    return {
        "flow_count": len(fcts_us),
        "fct_avg_us": mean(fcts_us) if fcts_us else "",
        "fct_p50_us": percentile(fcts_us, 0.50),
        "fct_p95_us": percentile(fcts_us, 0.95),
        "fct_p99_us": percentile(fcts_us, 0.99),
        "slowdown_avg": mean(slowdowns) if slowdowns else "",
        "slowdown_p99": percentile(slowdowns, 0.99),
    }


def summarize_goodput(path):
    rows = numeric_rows(path)
    values = []
    for row in rows:
        if len(row) <= 2:
            continue
        values.extend(v for v in row[2:] if v > 0)
    return {
        "goodput_avg_gbps": mean(values) if values else "",
        "goodput_p50_gbps": percentile(values, 0.50),
        "goodput_p95_gbps": percentile(values, 0.95),
    }


def count_pattern(path, pattern):
    if not path or not os.path.exists(path):
        return 0
    count = 0
    regex = re.compile(pattern)
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            if regex.search(line):
                count += 1
    return count


def summarize_logs(sender_log, config_log):
    return {
        "path_switch_count": count_pattern(config_log, r"\[PATH_SWITCH\].*changed=1"),
        "retrans_count": count_pattern(sender_log, r"^\[RETX\]"),
    }


def build_config(args, runid, run_dir, k_value):
    lines = read_lines(args.template_config)
    rel_run_dir = os.path.join("mix", "output", runid)

    if args.topology_file:
        lines = replace_config_key(lines, "TOPOLOGY_FILE", args.topology_file)
    if args.flow_file:
        lines = replace_config_key(lines, "FLOW_FILE", args.flow_file)

    for key, filename_template in RUN_OUTPUT_KEYS.items():
        lines = replace_config_key(
            lines, key, os.path.join(rel_run_dir, filename_template.format(runid=runid))
        )

    lines = replace_config_key(lines, "CC_MODE", args.cc)
    lines = replace_config_key(lines, "PSN_PATH_K", k_value)
    lines = replace_config_key(lines, "HPCC_TRACE", 1 if args.trace else 0)
    if args.random_seed:
        lines = replace_config_key(lines, "RANDOM_SEED", args.random_seed)

    for key, value in METHOD_FLAGS[args.method].items():
        lines = replace_config_key(lines, key, value)

    config_path = os.path.join(run_dir, "config.txt")
    write_lines(config_path, lines)
    return config_path


def run_one(task):
    args = task["args"]
    k_value = task["k"]
    runid = unique_id(k_value)
    run_dir = os.path.join(args.work_dir, "mix", "output", runid)
    os.makedirs(run_dir, exist_ok=True)
    config_path = build_config(args, runid, run_dir, k_value)

    log_path = os.path.join(run_dir, "config.log")
    if args.direct_bin:
        run_args = [args.direct_bin, config_path]
    else:
        run_arg = "scratch/network-load-balance {}".format(config_path)
        run_args = shlex.split(args.waf_cmd) + ["--run", run_arg]
    print("Run {}: K={} cc={} method={}".format(runid, k_value, args.cc, args.method))

    status = "ok"
    rc = None
    if args.dry_run:
        status = "dry-run"
    else:
        try:
            env = os.environ.copy()
            if args.ld_library_path:
                existing = env.get("LD_LIBRARY_PATH", "")
                env["LD_LIBRARY_PATH"] = (
                    args.ld_library_path if not existing else args.ld_library_path + os.pathsep + existing
                )
            with open(log_path, "w", encoding="utf-8") as log_file:
                rc = subprocess.call(
                    run_args,
                    cwd=args.work_dir,
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                    timeout=args.timeout,
                    env=env,
                )
        except subprocess.TimeoutExpired:
            status = "timeout"
        if rc not in (0, None):
            status = "failed"

    fct_file = os.path.join(run_dir, "{}_out_fct.txt".format(runid))
    goodput_file = os.path.join(run_dir, "{}_out_burst_goodput.txt".format(runid))
    sender_log = os.path.join(run_dir, "sender.log")

    result = {
        "k": k_value,
        "runid": runid,
        "status": status,
        "rc": "" if rc is None else rc,
        "run_dir": run_dir,
        "config": config_path,
        "log": log_path,
        "fct_file": fct_file,
        "goodput_file": goodput_file,
        "sender_log": sender_log,
    }
    result.update(summarize_fct(fct_file))
    result.update(summarize_goodput(goodput_file))
    result.update(summarize_logs(sender_log, log_path))

    if status == "ok":
        archive_dir = os.path.join(
            args.archive_root, args.method, "cc{}".format(args.cc), "k{}".format(k_value), runid
        )
        os.makedirs(archive_dir, exist_ok=True)
        for path in [config_path, log_path, fct_file, goodput_file, sender_log]:
            if os.path.exists(path):
                shutil.copy(path, archive_dir)
        result["archive_dir"] = archive_dir
    else:
        result["archive_dir"] = ""

    return result


def write_summary(results, path):
    fieldnames = [
        "k",
        "status",
        "runid",
        "flow_count",
        "fct_avg_us",
        "fct_p50_us",
        "fct_p95_us",
        "fct_p99_us",
        "slowdown_avg",
        "slowdown_p99",
        "goodput_avg_gbps",
        "goodput_p50_gbps",
        "goodput_p95_gbps",
        "path_switch_count",
        "retrans_count",
        "archive_dir",
        "log",
    ]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for result in sorted(results, key=lambda r: r["k"]):
            writer.writerow({key: result.get(key, "") for key in fieldnames})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ks", default=DEFAULT_KS)
    parser.add_argument("--method", default=DEFAULT_METHOD, choices=sorted(METHOD_FLAGS))
    parser.add_argument("--cc", default=DEFAULT_CC)
    parser.add_argument("--template-config", default="mix/output/1/config.txt")
    parser.add_argument("--topology-file", default="")
    parser.add_argument("--flow-file", default="")
    parser.add_argument("--random-seed", default="")
    parser.add_argument("--work-dir", default=".")
    parser.add_argument("--archive-root", default="output_k")
    parser.add_argument("--waf-cmd", default="python2 ./waf")
    parser.add_argument("--direct-bin", default="", help="run this executable directly instead of waf")
    parser.add_argument("--ld-library-path", default="", help="prepend this path when using --direct-bin")
    parser.add_argument("--timeout", type=int, default=3600)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--trace", action="store_true", default=True)
    parser.add_argument("--no-trace", action="store_false", dest="trace")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not os.path.exists(args.template_config):
        raise FileNotFoundError(args.template_config)
    os.makedirs(os.path.join(args.work_dir, "mix", "output"), exist_ok=True)

    tasks = [{"args": args, "k": k} for k in parse_int_list(args.ks)]
    print("Starting {} K experiment tasks with {} worker(s)".format(len(tasks), args.workers))

    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        futures = [executor.submit(run_one, task) for task in tasks]
        for future in concurrent.futures.as_completed(futures):
            results.append(future.result())

    summary_path = os.path.join(args.archive_root, args.method, "cc{}".format(args.cc), "summary.csv")
    write_summary(results, summary_path)
    print("Wrote {}".format(summary_path))
    for result in sorted(results, key=lambda r: r["k"]):
        print(
            "K={k} status={status} fct_avg_us={fct_avg_us} goodput_avg_gbps={goodput_avg_gbps} "
            "path_switch_count={path_switch_count} retrans_count={retrans_count}".format(**result)
        )


if __name__ == "__main__":
    main()
