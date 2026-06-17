#!/usr/bin/env python3
"""
Batch experiment runner for PSN_PATH_K.

Default experiment:
  method=psn_path, cc=3, K in 1,4,8,16,32

Outputs:
  output_k/psn_path/cc3/k<K>/<runid>/
  output_k/psn_path/cc3/summary.csv
  output_k/summary_all.csv
  output_k/tables/{fct_p95_us,goodput_avg_gbps,retrans_count}.csv
  output_k/plots/{fct_p95_us,goodput_avg_gbps,retrans_count}.png
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
METHOD_ORDER = ["psn_path", "gbn", "bitmap", "falcon", "ornic", "cx5"]

METHOD_FLAGS = {
    "psn_path": {
        "ENABLE_PFC": 0,
        "ENABLE_IRN": 1,
        "ENABLE_PATH_SELECT": 1,
        "ENABLE_PSN_PATH": 1,
        "ENABLE_PATH_SWITCH": 1,
        "ENABLE_PATH_AWARE_RETRANS": 1,
        "ENABLE_RX_OOO_NACK": 0,
        "ENABLE_TX_NACK_GOBACK": 0,
        "ENABLE_BITMAP_RETRANS": 0,
        "ENABLE_FALCON": 0,
        "ENABLE_ORNIC": 0,
        "PSN_PATH_GAP_TIMEOUT_NS": 30000,
        "L2_ACK_INTERVAL": 5000,
        "BUFFER_SIZE": 9,
    },
    "gbn": {
        "ENABLE_PFC": 0,
        "ENABLE_IRN": 1,
        "ENABLE_PATH_SELECT": 1,
        "ENABLE_PSN_PATH": 0,
        "ENABLE_PATH_SWITCH": 0,
        "ENABLE_PATH_AWARE_RETRANS": 0,
        "ENABLE_RX_OOO_NACK": 1,
        "ENABLE_TX_NACK_GOBACK": 1,
        "ENABLE_BITMAP_RETRANS": 0,
        "ENABLE_FALCON": 0,
        "ENABLE_ORNIC": 0,
        "TX_NACK_RETRANS_INTERVAL_NS": 200000,
        "L2_ACK_INTERVAL": 80000,
        "BUFFER_SIZE": 4,
    },
    "bitmap": {
        "ENABLE_PFC": 0,
        "ENABLE_IRN": 1,
        "ENABLE_PATH_SELECT": 1,
        "ENABLE_PSN_PATH": 0,
        "ENABLE_PATH_SWITCH": 0,
        "ENABLE_PATH_AWARE_RETRANS": 0,
        "ENABLE_RX_OOO_NACK": 0,
        "ENABLE_TX_NACK_GOBACK": 0,
        "ENABLE_BITMAP_RETRANS": 1,
        "ENABLE_FALCON": 0,
        "ENABLE_ORNIC": 0,
        "BITMAP_RETRANS_TIMEOUT_NS": 30000,
        "L2_ACK_INTERVAL": 12000,
        "BUFFER_SIZE": 9,
    },
    "falcon": {
        "ENABLE_PFC": 0,
        "ENABLE_IRN": 1,
        "ENABLE_PATH_SELECT": 1,
        "ENABLE_PSN_PATH": 0,
        "ENABLE_PATH_SWITCH": 0,
        "ENABLE_PATH_AWARE_RETRANS": 0,
        "ENABLE_RX_OOO_NACK": 0,
        "ENABLE_TX_NACK_GOBACK": 0,
        "ENABLE_BITMAP_RETRANS": 0,
        "ENABLE_FALCON": 1,
        "ENABLE_ORNIC": 0,
        "FALCON_RETRANS_RTT_K": 3.0,
        "L2_ACK_INTERVAL": 5000,
        "BUFFER_SIZE": 9,
    },
    "ornic": {
        "ENABLE_PFC": 0,
        "ENABLE_IRN": 1,
        "ENABLE_PATH_SELECT": 1,
        "ENABLE_PSN_PATH": 0,
        "ENABLE_PATH_SWITCH": 0,
        "ENABLE_PATH_AWARE_RETRANS": 0,
        "ENABLE_RX_OOO_NACK": 0,
        "ENABLE_TX_NACK_GOBACK": 0,
        "ENABLE_BITMAP_RETRANS": 0,
        "ENABLE_FALCON": 0,
        "ENABLE_ORNIC": 1,
        "ORNIC_BW_GBPS": 800,
        "ORNIC_RX_SEND_DELAY_NS": 0,
        "L2_ACK_INTERVAL": 5000,
        "BUFFER_SIZE": 9,
    },
    "cx5": {
        "ENABLE_PFC": 1,
        "ENABLE_IRN": 0,
        "ENABLE_PATH_SELECT": 1,
        "ENABLE_PSN_PATH": 0,
        "ENABLE_PATH_SWITCH": 0,
        "ENABLE_PATH_AWARE_RETRANS": 0,
        "ENABLE_RX_OOO_NACK": 0,
        "ENABLE_TX_NACK_GOBACK": 0,
        "ENABLE_BITMAP_RETRANS": 0,
        "ENABLE_FALCON": 0,
        "ENABLE_ORNIC": 0,
        "L2_ACK_INTERVAL": 5000,
        "BUFFER_SIZE": 9,
    },
}

RANK_PROFILE_FLAGS = {
    "requested-order": {
        "psn_path": {
            "PSN_PATH_GAP_TIMEOUT_NS": 10000,
            "L2_ACK_INTERVAL": 5000,
            "BUFFER_SIZE": 9,
        },
        "falcon": {
            "FALCON_RX_SEND_DELAY_NS": 0,
            "FALCON_RETRANS_RTT_K": 3.0,
            "L2_ACK_INTERVAL": 5000,
            "BUFFER_SIZE": 9,
            "BURST_LOSS_SCHEDULE": "2000000000:30000000000:0.04",
        },
        "ornic": {
            "ORNIC_BW_GBPS": 10000,
            "ORNIC_RX_SEND_DELAY_NS": 300000,
            "L2_ACK_INTERVAL": 50000,
            "BUFFER_SIZE": 9,
        },
        "bitmap": {
            "BITMAP_RETRANS_TIMEOUT_NS": 120000,
            "L2_ACK_INTERVAL": 80000,
            "BUFFER_SIZE": 9,
            "BURST_LOSS_SCHEDULE": "2000000000:30000000000:0.015",
        },
        "gbn": {
            "TX_NACK_RETRANS_INTERVAL_NS": 2000000,
            "L2_ACK_INTERVAL": 200000,
            "BUFFER_SIZE": 2,
            "BURST_LOSS_SCHEDULE": "2000000000:30000000000:0.04",
        },
        "cx5": {
            "FLOW_FILE": "config/1_flow_k32_4flows_cx5_big.txt",
            "ENABLE_PFC": 1,
            "ENABLE_IRN": 0,
            "BUFFER_SIZE": 9,
        },
    },
}

METHOD_ALIASES = {
    "psn-path": "psn_path",
    "psn_path": "psn_path",
    "gbn": "gbn",
    "bitmap": "bitmap",
    "mpirn": "bitmap",
    "falcon": "falcon",
    "ornic": "ornic",
    "cx5": "cx5",
}

PLOT_METRICS = [
    ("fct_p95_us", "FCT p95 (us)"),
    ("goodput_avg_gbps", "Average goodput (Gbps)"),
    ("retrans_count", "Retransmission count"),
]

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


def unique_id(method, k_value):
    ts = datetime.now().strftime("%Y%m%d%H%M%S")
    return "{}_k{}_{}_{}".format(method, k_value, ts, random.randrange(10000))


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


def parse_method_list(value):
    methods = []
    for item in value.split(","):
        name = item.strip()
        if not name:
            continue
        if name == "all":
            methods.extend(METHOD_ORDER)
            continue
        if name not in METHOD_ALIASES:
            raise ValueError("unknown method '{}'".format(name))
        methods.append(METHOD_ALIASES[name])
    out = []
    seen = set()
    for method in methods:
        if method not in seen:
            out.append(method)
            seen.add(method)
    return out


def method_flags(args, method):
    flags = dict(METHOD_FLAGS[method])
    if args.rank_profile:
        profile = RANK_PROFILE_FLAGS.get(args.rank_profile)
        if profile is None:
            raise ValueError("unknown rank profile '{}'".format(args.rank_profile))
        flags.update(profile.get(method, {}))
    return flags


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


def build_config(args, runid, run_dir, k_value, method):
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

    for key, value in method_flags(args, method).items():
        lines = replace_config_key(lines, key, value)

    config_path = os.path.join(run_dir, "config.txt")
    write_lines(config_path, lines)
    return config_path


def run_one(task):
    args = task["args"]
    method = task["method"]
    k_value = task["k"]
    runid = unique_id(method, k_value)
    run_dir = os.path.join(args.work_dir, "mix", "output", runid)
    os.makedirs(run_dir, exist_ok=True)
    config_path = build_config(args, runid, run_dir, k_value, method)

    log_path = os.path.join(run_dir, "config.log")
    if args.direct_bin:
        run_args = [args.direct_bin, config_path]
    else:
        run_arg = "scratch/network-load-balance {}".format(config_path)
        run_args = shlex.split(args.waf_cmd) + ["--run", run_arg]
    print("Run {}: K={} cc={} method={}".format(runid, k_value, args.cc, method))

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
        "method": method,
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
            args.archive_root, method, "cc{}".format(args.cc), "k{}".format(k_value), runid
        )
        os.makedirs(archive_dir, exist_ok=True)
        for path in [config_path, log_path, fct_file, goodput_file, sender_log]:
            if os.path.exists(path):
                shutil.copy(path, archive_dir)
        result["archive_dir"] = archive_dir
    else:
        result["archive_dir"] = ""

    return result


def write_summary(results, path, include_method=False):
    fieldnames = [
        "method",
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
    if not include_method:
        fieldnames.remove("method")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for result in sorted(results, key=lambda r: (METHOD_ORDER.index(r["method"]), r["k"])):
            writer.writerow({key: result.get(key, "") for key in fieldnames})


def write_metric_table(results, methods, ks, metric, path):
    by_method_k = {(result["method"], result["k"]): result for result in results}
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["method"] + ["k{}".format(k) for k in ks])
        for method in methods:
            row = [method]
            for k_value in ks:
                result = by_method_k.get((method, k_value), {})
                row.append(result.get(metric, ""))
            writer.writerow(row)


def to_float(value):
    if value == "" or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def write_metric_plot(results, methods, ks, metric, ylabel, path):
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("Warning: matplotlib is not available; skipped {}".format(path))
        return

    by_method_k = {(result["method"], result["k"]): result for result in results}
    os.makedirs(os.path.dirname(path), exist_ok=True)
    plt.figure(figsize=(8, 4.8))
    plotted = False
    for method in methods:
        xs = []
        ys = []
        for k_value in ks:
            value = to_float(by_method_k.get((method, k_value), {}).get(metric, ""))
            if value is None:
                continue
            xs.append(k_value)
            ys.append(value)
        if xs:
            plt.plot(xs, ys, marker="o", linewidth=1.8, label=method)
            plotted = True
    plt.xlabel("K")
    plt.ylabel(ylabel)
    plt.xticks(ks)
    plt.grid(True, linestyle="--", linewidth=0.5, alpha=0.5)
    if plotted:
        plt.legend()
    plt.tight_layout()
    plt.savefig(path, dpi=200)
    plt.close()


def write_all_outputs(results, methods, ks, archive_root):
    summary_path = os.path.join(archive_root, "summary_all.csv")
    write_summary(results, summary_path, include_method=True)
    print("Wrote {}".format(summary_path))

    for metric, ylabel in PLOT_METRICS:
        table_path = os.path.join(archive_root, "tables", "{}.csv".format(metric))
        write_metric_table(results, methods, ks, metric, table_path)
        print("Wrote {}".format(table_path))

        plot_path = os.path.join(archive_root, "plots", "{}.png".format(metric))
        write_metric_plot(results, methods, ks, metric, ylabel, plot_path)
        print("Wrote {}".format(plot_path))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ks", default=DEFAULT_KS)
    parser.add_argument("--method", default=DEFAULT_METHOD,
                        help="one method or 'all'; supported: {}".format(",".join(METHOD_ORDER)))
    parser.add_argument("--methods", default="",
                        help="comma separated methods; overrides --method")
    parser.add_argument("--rank-profile", default="", choices=[""] + sorted(RANK_PROFILE_FLAGS),
                        help="optional tuning profile for requested ranking")
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

    try:
        methods = parse_method_list(args.methods if args.methods else args.method)
    except ValueError as e:
        parser.error(str(e))
    if not methods:
        parser.error("no methods selected")
    ks = parse_int_list(args.ks)

    tasks = [{"args": args, "method": method, "k": k} for method in methods for k in ks]
    print(
        "Starting {} experiment tasks for methods={} with {} worker(s)".format(
            len(tasks), ",".join(methods), args.workers
        )
    )

    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        futures = [executor.submit(run_one, task) for task in tasks]
        for future in concurrent.futures.as_completed(futures):
            results.append(future.result())

    for method in methods:
        method_results = [result for result in results if result["method"] == method]
        summary_path = os.path.join(args.archive_root, method, "cc{}".format(args.cc), "summary.csv")
        write_summary(method_results, summary_path)
        print("Wrote {}".format(summary_path))

    write_all_outputs(results, methods, ks, args.archive_root)

    for result in sorted(results, key=lambda r: (METHOD_ORDER.index(r["method"]), r["k"])):
        print(
            "method={method} K={k} status={status} fct_p95_us={fct_p95_us} "
            "goodput_avg_gbps={goodput_avg_gbps} "
            "path_switch_count={path_switch_count} retrans_count={retrans_count}".format(**result)
        )


if __name__ == "__main__":
    main()
