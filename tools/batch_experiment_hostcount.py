#!/usr/bin/env python3
"""
Batch experiment runner for varying host counts.

Default experiment:
  host_count in 256,512,1024,2048,4096
  methods psn_path,bitmap,gbn,falcon,ornic
  CC_MODE=3

The topology keeps the leaf/spine switch counts fixed and only changes the
number of hosts attached to each leaf switch.
"""
import argparse
import concurrent.futures
import csv
import heapq
import math
import os
import random
import shlex
import shutil
import subprocess
from datetime import datetime
from statistics import mean


DEFAULT_HOSTS = "128,256,384,512,768,1024"
DEFAULT_METHODS = "psn_path,bitmap,gbn,falcon,ornic"
DEFAULT_CC = ""

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
    "gbn": {
        "ENABLE_PSN_PATH": 0,
        "ENABLE_PATH_SWITCH": 0,
        "ENABLE_PATH_AWARE_RETRANS": 0,
        "ENABLE_RX_OOO_NACK": 1,
        "ENABLE_TX_NACK_GOBACK": 1,
        "ENABLE_BITMAP_RETRANS": 0,
        "ENABLE_FALCON": 0,
        "ENABLE_ORNIC": 0,
    },
    "bitmap": {
        "ENABLE_PSN_PATH": 0,
        "ENABLE_PATH_SWITCH": 0,
        "ENABLE_PATH_AWARE_RETRANS": 0,
        "ENABLE_RX_OOO_NACK": 0,
        "ENABLE_TX_NACK_GOBACK": 0,
        "ENABLE_BITMAP_RETRANS": 1,
        "BITMAP_RETRANS_SIZE": 64,
        "BITMAP_RETRANS_TIMEOUT_NS": 10000,
        "ENABLE_FALCON": 0,
        "ENABLE_ORNIC": 0,
    },
    "falcon": {
        "ENABLE_PSN_PATH": 0,
        "ENABLE_PATH_SWITCH": 0,
        "ENABLE_PATH_AWARE_RETRANS": 0,
        "ENABLE_RX_OOO_NACK": 0,
        "ENABLE_TX_NACK_GOBACK": 0,
        "ENABLE_BITMAP_RETRANS": 0,
        "ENABLE_FALCON": 1,
        "FALCON_RETRANS_RTT_K": 0.0,
        "ENABLE_ORNIC": 0,
    },
    "ornic": {
        "ENABLE_PSN_PATH": 0,
        "ENABLE_PATH_SWITCH": 0,
        "ENABLE_PATH_AWARE_RETRANS": 0,
        "ENABLE_RX_OOO_NACK": 0,
        "ENABLE_TX_NACK_GOBACK": 0,
        "ENABLE_BITMAP_RETRANS": 0,
        "ENABLE_FALCON": 0,
        "ENABLE_ORNIC": 1,
    },
}

METHOD_ALIASES = {
    "psn-path": "psn_path",
    "psn_path": "psn_path",
    "bitmap": "bitmap",
    "mpirn": "bitmap",
    "gbn": "gbn",
    "falcon": "falcon",
    "ornic": "ornic",
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


def read_lines(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.readlines()


def write_lines(path, lines):
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
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


def config_value(lines, key, default=None):
    for line in lines:
        parts = line.strip().split(None, 1)
        if parts and parts[0] == key:
            return parts[1].strip() if len(parts) > 1 else ""
    return default


def parse_int_list(value):
    return [int(item.strip()) for item in value.split(",") if item.strip()]


def parse_method_list(value):
    methods = []
    for item in value.split(","):
        if not item.strip():
            continue
        method = METHOD_ALIASES.get(item.strip().lower())
        if method is None:
            raise RuntimeError("unknown method {}".format(item))
        methods.append(method)
    return methods


def unique_id(host_count, method):
    ts = datetime.now().strftime("%Y%m%d%H%M%S")
    return "hosts{}_{}_{}_{}".format(host_count, method, ts, random.randrange(10000))


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


def numeric_rows(path):
    rows = []
    if not path or not os.path.exists(path):
        return rows
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                rows.append([float(item) for item in stripped.split()])
            except ValueError:
                continue
    return rows


def summarize_fct(path):
    rows = numeric_rows(path)
    fct_ns = []
    goodput_gbps = []
    retrans = []
    path_switches = []
    for row in rows:
        if len(row) < 12:
            continue
        fct_ns.append(row[6])
        retrans.append(row[8])
        path_switches.append(row[9])
        goodput_gbps.append(row[11])
    return {
        "flow_count": len(fct_ns),
        "fct_p95_ns": percentile(fct_ns, 0.95),
        "fct_avg_ns": mean(fct_ns) if fct_ns else "",
        "goodput_avg_gbps": mean(goodput_gbps) if goodput_gbps else "",
        "goodput_p95_gbps": percentile(goodput_gbps, 0.95),
        "path_switch_total": int(sum(path_switches)) if path_switches else 0,
        "path_switch_p95": percentile(path_switches, 0.95),
        "retrans_total": int(sum(retrans)) if retrans else 0,
        "retrans_avg": mean(retrans) if retrans else "",
        "retrans_p95": percentile(retrans, 0.95),
    }


def load_cdf(path):
    cdf = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if not stripped:
                continue
            size, prob = stripped.split()[:2]
            cdf.append((float(size), float(prob)))
    if not cdf or cdf[0][1] != 0 or cdf[-1][1] != 100:
        raise RuntimeError("invalid CDF {}".format(path))
    return cdf


def cdf_average(cdf):
    avg = 0.0
    last_x, last_y = cdf[0]
    for x, y in cdf[1:]:
        avg += (x + last_x) / 2.0 * (y - last_y) / 100.0
        last_x, last_y = x, y
    return avg


def cdf_rand(cdf, rng):
    y = rng.random() * 100.0
    for idx in range(1, len(cdf)):
        x0, y0 = cdf[idx - 1]
        x1, y1 = cdf[idx]
        if y <= y1:
            if y1 == y0:
                return x1
            return x0 + (x1 - x0) * (y - y0) / (y1 - y0)
    return cdf[-1][0]


def poisson(mean_ns, rng):
    return -math.log(1.0 - rng.random()) * mean_ns


def parse_flow_pairs(value):
    pairs = []
    if not value:
        return pairs
    for item in value.split(","):
        item = item.strip()
        if not item:
            continue
        if "-" in item:
            src, dst = item.split("-", 1)
        elif ":" in item:
            src, dst = item.split(":", 1)
        else:
            raise RuntimeError("flow pair must be src-dst or src:dst: {}".format(item))
        pairs.append((int(src), int(dst)))
    return pairs


def validate_flow_pairs_are_hosts(pairs, host_count):
    for src, dst in pairs:
        if src < 0 or src >= host_count:
            raise RuntimeError("flow src {} is not a host for host_count {}".format(src, host_count))
        if dst < 0 or dst >= host_count:
            raise RuntimeError("flow dst {} is not a host for host_count {}".format(dst, host_count))
        if src == dst:
            raise RuntimeError("flow src and dst must differ: {}-{}".format(src, dst))


def generate_fixed_flow_file(path, host_count, cdf_path, flow_count, seed, start_time, flow_size,
                             flow_pairs=None):
    cdf = load_cdf(cdf_path)
    rng = random.Random(seed + host_count)
    pairs = flow_pairs or []
    if pairs:
        validate_flow_pairs_are_hosts(pairs, host_count)
        flow_count = len(pairs)
    lines = ["{} \n".format(flow_count)]
    for idx in range(flow_count):
        if pairs:
            src, dst = pairs[idx]
        else:
            src = rng.randrange(host_count)
            dst = rng.randrange(host_count)
            while dst == src:
                dst = rng.randrange(host_count)
        size = flow_size if flow_size > 0 else max(1, int(cdf_rand(cdf, rng)))
        lines.append("{} {} 3 {} {:.9f}\n".format(src, dst, size, start_time))
    write_lines(path, lines)


def generate_flow_file(path, host_count, cdf_path, load, bandwidth_gbps, traffic_time, seed):
    cdf = load_cdf(cdf_path)
    rng = random.Random(seed)
    avg_size = cdf_average(cdf)
    bandwidth_bps = bandwidth_gbps * 1e9
    avg_inter_arrival_ns = 1.0 / (bandwidth_bps * load / 8.0 / avg_size) * 1e9
    base_t_ns = 2000000000
    end_t_ns = base_t_ns + int(traffic_time * 1e9)

    events = []
    for src in range(host_count):
        t_ns = base_t_ns + int(poisson(avg_inter_arrival_ns, rng))
        events.append((t_ns, src))
    heapq.heapify(events)

    flows = []
    while events:
        t_ns, src = heapq.heappop(events)
        inter_t = int(poisson(avg_inter_arrival_ns, rng))
        next_t = t_ns + inter_t
        if next_t > end_t_ns:
            continue
        dst = rng.randrange(host_count)
        while dst == src:
            dst = rng.randrange(host_count)
        size = max(1, int(cdf_rand(cdf, rng)))
        flows.append((src, dst, 3, size, next_t * 1e-9))
        heapq.heappush(events, (next_t, src))

    lines = ["{} \n".format(len(flows))]
    lines.extend("{} {} {} {} {:.9f}\n".format(*flow) for flow in flows)
    write_lines(path, lines)


def generate_topology_file(path, host_count, leaf_count, spine_count, rate, delay,
                           leaf_spine_error, host_error):
    if host_count % leaf_count != 0:
        raise RuntimeError("host count {} is not divisible by leaf count {}".format(
            host_count, leaf_count))
    hosts_per_leaf = host_count // leaf_count
    leaf_start = host_count
    spine_start = host_count + leaf_count
    switch_ids = list(range(leaf_start, leaf_start + leaf_count))
    switch_ids.extend(range(spine_start, spine_start + spine_count))
    total_nodes = host_count + leaf_count + spine_count
    total_links = host_count + leaf_count * spine_count

    lines = [
        "{} {} {}\n".format(total_nodes, len(switch_ids), total_links),
        "{}\n".format(" ".join(str(sid) for sid in switch_ids)),
    ]
    for leaf_index in range(leaf_count):
        leaf_id = leaf_start + leaf_index
        host_base = leaf_index * hosts_per_leaf
        for host_id in range(host_base, host_base + hosts_per_leaf):
            lines.append("{} {} {} {} {}\n".format(host_id, leaf_id, rate, delay, host_error))
    for leaf_id in range(leaf_start, leaf_start + leaf_count):
        for spine_id in range(spine_start, spine_start + spine_count):
            lines.append("{} {} {} {} {}\n".format(
                leaf_id, spine_id, rate, delay, leaf_spine_error))
    write_lines(path, lines)


def build_config(args, runid, run_dir, host_count, method, topology_file, flow_file):
    lines = read_lines(args.template_config)
    rel_run_dir = os.path.join("mix", "output", runid)
    lines = replace_config_key(lines, "TOPOLOGY_FILE", topology_file)
    lines = replace_config_key(lines, "FLOW_FILE", flow_file)
    if args.cc:
        lines = replace_config_key(lines, "CC_MODE", args.cc)
    lines = replace_config_key(lines, "LOAD", int(args.load * 100))
    lines = replace_config_key(lines, "FLOWGEN_START_TIME", args.flowgen_start)
    lines = replace_config_key(lines, "FLOWGEN_STOP_TIME", args.flowgen_start + args.traffic_time)
    lines = replace_config_key(lines, "RANDOM_SEED", args.seed)

    for key, filename_template in RUN_OUTPUT_KEYS.items():
        lines = replace_config_key(
            lines, key, os.path.join(rel_run_dir, filename_template.format(runid=runid)))

    for key, value in METHOD_FLAGS[method].items():
        lines = replace_config_key(lines, key, value)

    config_path = os.path.join(run_dir, "config.txt")
    write_lines(config_path, lines)
    return config_path


def run_one(task):
    args = task["args"]
    host_count = task["host_count"]
    method = task["method"]
    runid = unique_id(host_count, method)
    run_dir = os.path.join(args.work_dir, "mix", "output", runid)
    os.makedirs(run_dir, exist_ok=True)

    input_dir = os.path.join(args.work_dir, args.generated_input_dir)
    os.makedirs(input_dir, exist_ok=True)
    topology_file = os.path.join(input_dir, "leaf_spine_hosts{}.txt".format(host_count))
    if args.flow_count > 0:
        flow_pairs = parse_flow_pairs(args.flow_pairs)
        pair_suffix = ""
        if flow_pairs:
            pair_suffix = "_" + "_".join("{}-{}".format(src, dst) for src, dst in flow_pairs)
        flow_file = os.path.join(
            input_dir,
            "flows_hosts{}_count{}_seed{}{}.txt".format(
                host_count, args.flow_count, args.seed, pair_suffix))
    else:
        flow_file = os.path.join(
            input_dir,
            "flows_hosts{}_load{}_time{}ms_seed{}.txt".format(
                host_count, int(args.load * 100), int(args.traffic_time * 1000), args.seed))

    if not os.path.exists(topology_file) or args.regenerate_inputs:
        generate_topology_file(
            topology_file, host_count, args.leaf_count, args.spine_count,
            "{}Gbps".format(args.bandwidth_gbps), "{}ns".format(args.link_delay_ns),
            args.leaf_spine_error, args.host_error)
    if not os.path.exists(flow_file) or args.regenerate_inputs:
        if args.flow_count > 0:
            generate_fixed_flow_file(
                flow_file, host_count, args.cdf, args.flow_count, args.seed,
                args.flowgen_start, args.fixed_flow_size, parse_flow_pairs(args.flow_pairs))
        else:
            generate_flow_file(
                flow_file, host_count, args.cdf, args.load, args.bandwidth_gbps,
                args.traffic_time, args.seed)

    config_path = build_config(args, runid, run_dir, host_count, method, topology_file, flow_file)
    log_path = os.path.join(run_dir, "config.log")
    if args.direct_bin:
        run_args = [args.direct_bin, config_path]
    else:
        run_arg = "scratch/network-load-balance {}".format(config_path)
        run_args = shlex.split(args.waf_cmd) + ["--run", run_arg]

    status = "ok"
    rc = None
    print("RUN hosts={} method={} cc={} id={}".format(host_count, method, args.cc, runid))
    if args.dry_run:
        status = "dry-run"
    else:
        try:
            env = os.environ.copy()
            ld_paths = []
            if args.direct_bin:
                ld_paths.extend([
                    os.path.join(args.work_dir, "build-local"),
                    "/usr/lib/x86_64-linux-gnu",
                    "/lib/x86_64-linux-gnu",
                ])
            if args.ld_library_path:
                ld_paths.extend(p for p in args.ld_library_path.split(os.pathsep) if p)
            existing = env.get("LD_LIBRARY_PATH", "")
            if existing:
                ld_paths.extend(p for p in existing.split(os.pathsep) if p)
            if ld_paths:
                deduped = []
                seen = set()
                for path in ld_paths:
                    if path not in seen:
                        deduped.append(path)
                        seen.add(path)
                env["LD_LIBRARY_PATH"] = os.pathsep.join(deduped)
            with open(log_path, "w", encoding="utf-8") as log_file:
                rc = subprocess.call(
                    run_args, cwd=args.work_dir, stdout=log_file,
                    stderr=subprocess.STDOUT, timeout=args.timeout, env=env)
        except subprocess.TimeoutExpired:
            status = "timeout"
        if rc not in (0, None):
            status = "failed"

    fct_file = os.path.join(run_dir, "{}_out_fct.txt".format(runid))
    result = {
        "host_count": host_count,
        "method": method,
        "cc": args.cc,
        "input_flow_count": args.flow_count if args.flow_count > 0 else "",
        "status": status,
        "rc": "" if rc is None else rc,
        "runid": runid,
        "run_dir": run_dir,
        "topology_file": topology_file,
        "flow_file": flow_file,
        "config": config_path,
        "log": log_path,
        "fct_file": fct_file,
    }
    result.update(summarize_fct(fct_file))

    result["archive_dir"] = ""
    return result


def write_summary(results, path, hosts, methods):
    by_host_method = {(result["host_count"], result["method"]): result for result in results}
    sections = [
        ("FCT_NS", "fct_avg_ns"),
        ("GOODPUT_GBPS", "goodput_avg_gbps"),
        ("RETRANS", "retrans_avg"),
    ]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        for section_idx, (section_name, result_key) in enumerate(sections):
            if section_idx:
                writer.writerow([])
            writer.writerow([section_name])
            writer.writerow(["host_count"] + methods)
            for host_count in hosts:
                row = [host_count]
                for method in methods:
                    result = by_host_method.get((host_count, method), {})
                    row.append(result.get(result_key, ""))
                writer.writerow(row)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--hosts", default=DEFAULT_HOSTS)
    parser.add_argument("--methods", default=DEFAULT_METHODS)
    parser.add_argument("--cc", default=DEFAULT_CC,
                        help="override CC_MODE; empty means use template config")
    parser.add_argument("--template-config", default="mix/output/1/config.txt")
    parser.add_argument("--cdf", default="traffic_gen/AliStorage2019.txt")
    parser.add_argument("--load", type=float, default=None,
                        help="per-host offered load, e.g. 0.5 for 50%%")
    parser.add_argument("--traffic-time", type=float, default=0.001)
    parser.add_argument("--flow-count", type=int, default=2,
                        help="generate exactly this many flows per host-count; 0 uses load/time")
    parser.add_argument("--fixed-flow-size", type=int, default=8192000,
                        help="flow size in bytes when --flow-count is used; <=0 samples from CDF")
    parser.add_argument("--flow-pairs", default="",
                        help="comma separated src-dst pairs, e.g. 37-409,286-91")
    parser.add_argument("--flowgen-start", type=float, default=2.0)
    parser.add_argument("--bandwidth-gbps", type=int, default=100)
    parser.add_argument("--leaf-count", type=int, default=32)
    parser.add_argument("--spine-count", type=int, default=32)
    parser.add_argument("--link-delay-ns", type=int, default=1000)
    parser.add_argument("--leaf-spine-error", default="0.01")
    parser.add_argument("--host-error", default="0")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--work-dir", default=".")
    parser.add_argument("--archive-root", default="output_hostcount")
    parser.add_argument("--generated-input-dir", default="config/hostcount")
    parser.add_argument("--waf-cmd", default="python2 ./waf")
    parser.add_argument("--direct-bin", default="")
    parser.add_argument("--ld-library-path", default="")
    parser.add_argument("--timeout", type=int, default=3600)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--regenerate-inputs", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("host_args", nargs="*",
                        help="optional host counts, e.g. 128 384 768")
    args = parser.parse_args()

    template_lines = read_lines(args.template_config)
    if args.load is None:
        args.load = float(config_value(template_lines, "LOAD", "50")) / 100.0
    if not args.cc:
        args.cc = config_value(template_lines, "CC_MODE", "3")

    if args.host_args:
        hosts = [int(item) for item in args.host_args]
        args.hosts = ",".join(str(item) for item in hosts)
    else:
        hosts = parse_int_list(args.hosts)
    methods = parse_method_list(args.methods)
    os.makedirs(os.path.join(args.work_dir, "mix", "output"), exist_ok=True)

    tasks = []
    for host_count in hosts:
        for method in methods:
            tasks.append({"args": args, "host_count": host_count, "method": method})

    print("TEMPLATE {}".format(os.path.abspath(args.template_config)))
    print("TASKS count={} workers={} cc={} hosts={} methods={}".format(
        len(tasks), max(1, args.workers), args.cc, args.hosts, ",".join(methods)))
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        futures = [executor.submit(run_one, task) for task in tasks]
        for future in concurrent.futures.as_completed(futures):
            results.append(future.result())

    summary_path = os.path.join(args.archive_root, "cc{}".format(args.cc), "summary.csv")
    write_summary(results, summary_path, hosts, methods)
    print("SUMMARY {}".format(summary_path))
    with open(summary_path, "r", encoding="utf-8") as f:
        for line in f:
            print(line.strip())


if __name__ == "__main__":
    main()
