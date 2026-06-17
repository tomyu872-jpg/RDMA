#!/usr/bin/env python3
import argparse
import csv
from pathlib import Path


def read_config(path):
    values = {}
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) >= 2:
            values[parts[0]] = parts[1]
    return values


def parse_schedule(schedule):
    first = schedule.split(",", 1)[0]
    start, end, rate = first.split(":")
    return int(start), int(end), float(rate)


def phase_ranges(stat_start, interval, burst_start, burst_end):
    duration = burst_end - burst_start
    phases = {
        "before": (burst_start - duration, burst_start),
        "during": (burst_start, burst_end),
        "after": (burst_end, burst_end + duration),
    }
    out = {}
    for name, (start, end) in phases.items():
        first = max(0, (start - stat_start) // interval)
        last = max(first, (end - stat_start + interval - 1) // interval)
        out[name] = (int(first), int(last), int(start), int(end))
    return out


def method_name(run_dir):
    name = run_dir.name
    if name.startswith("burst_"):
        rest = name[len("burst_") :]
        for marker in ("_202",):
            if marker in rest:
                return rest.split(marker, 1)[0]
    return name


def find_pair(run_dir):
    goodput = list(run_dir.glob("*_burst_goodput.txt"))
    redundancy = list(run_dir.glob("*_burst_redundancy.txt"))
    if len(goodput) != 1 or len(redundancy) != 1:
        raise RuntimeError(f"{run_dir}: expected one goodput and one redundancy file")
    return goodput[0], redundancy[0]


def add_selected_values(path, selected_indexes):
    totals = [0.0] * len(selected_indexes)
    flow_counts = [0] * len(selected_indexes)
    with path.open("r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            parts = line.split()
            if len(parts) <= 2:
                continue
            for pos, bucket in enumerate(selected_indexes):
                idx = bucket + 2
                if idx < len(parts):
                    value = float(parts[idx])
                    totals[pos] += value
                    flow_counts[pos] += 1
    return totals, flow_counts


def summarize(values, start_pos, end_pos):
    part = values[start_pos:end_pos]
    if not part:
        return {"samples": 0, "avg": 0.0, "p50": 0.0, "min": 0.0, "max": 0.0}
    ordered = sorted(part)
    mid = len(ordered) // 2
    p50 = ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2.0
    return {
        "samples": len(part),
        "avg": sum(part) / len(part),
        "p50": p50,
        "min": ordered[0],
        "max": ordered[-1],
    }


def interpolate_series(raw, raw_step_ns, dense_step_ns, smooth_window_ns):
    if not raw:
        return []
    dense = []
    factor = max(1, raw_step_ns // dense_step_ns)
    for i in range((len(raw) - 1) * factor + 1):
        left = i // factor
        frac = (i % factor) / float(factor)
        if left + 1 < len(raw):
            value = raw[left] * (1.0 - frac) + raw[left + 1] * frac
        else:
            value = raw[left]
        dense.append(value)

    half = max(0, (smooth_window_ns // dense_step_ns) // 2)
    if half == 0:
        return dense
    prefix = [0.0]
    for value in dense:
        prefix.append(prefix[-1] + value)
    smoothed = []
    for i in range(len(dense)):
        lo = max(0, i - half)
        hi = min(len(dense), i + half + 1)
        smoothed.append((prefix[hi] - prefix[lo]) / (hi - lo))
    return smoothed


def extend_to_length(values, target_len):
    if len(values) >= target_len:
        return values[:target_len]
    fill = values[-1] if values else 0.0
    return values + [fill] * (target_len - len(values))


def phase_for_time(time_ns, phases):
    for name, (_, _, start, end) in phases.items():
        if start <= time_ns < end:
            return name
    return "outside"


def five_point_rows(method, dense_gp, dense_rr, start_ns, burst_start, phases, dense_step_ns):
    rows = []
    for phase, (_, _, phase_start, phase_end) in phases.items():
        phase_start_idx = max(0, (phase_start - start_ns) // dense_step_ns)
        phase_end_idx = max(phase_start_idx, (phase_end - start_ns) // dense_step_ns)
        phase_len = phase_end_idx - phase_start_idx
        if phase_len <= 0:
            continue
        for point in range(5):
            lo = phase_start_idx + (phase_len * point) // 5
            hi = phase_start_idx + (phase_len * (point + 1)) // 5
            gp_part = dense_gp[lo:hi]
            rr_part = dense_rr[lo:hi]
            if not gp_part:
                continue
            mid_time = start_ns + ((lo + hi - 1) // 2) * dense_step_ns
            rows.append(
                {
                    "method": method,
                    "phase": phase,
                    "phase_point": point + 1,
                    "time_ns": mid_time,
                    "time_us_from_burst_start": (mid_time - burst_start) / 1000.0,
                    "smoothed_total_goodput_gbps": sum(gp_part) / len(gp_part),
                    "smoothed_avg_redundancy_rate": sum(rr_part) / len(rr_part),
                    "points_averaged": len(gp_part),
                    "dense_step_ns": dense_step_ns,
                }
            )
    return rows


def compact_rows_to_wide(rows):
    by_method = {}
    for row in rows:
        method = row["method"]
        out = by_method.setdefault(method, {"method": method})
        prefix = f"{row['phase']}_{row['phase_point']}"
        out[f"{prefix}_goodput_gbps"] = row["smoothed_total_goodput_gbps"]
        out[f"{prefix}_redundancy_rate"] = row["smoothed_avg_redundancy_rate"]
    return [by_method[method] for method in sorted(by_method)]


def metric_rows_to_wide(rows, value_key):
    by_method = {}
    for row in rows:
        method = row["method"]
        out = by_method.setdefault(method, {"method": method})
        out[f"{row['phase']}_{row['phase_point']}"] = row[value_key]
    return [by_method[method] for method in sorted(by_method)]


def compact_fieldnames():
    fields = ["method"]
    for phase in ("before", "during", "after"):
        for point in range(1, 6):
            fields.append(f"{phase}_{point}_goodput_gbps")
            fields.append(f"{phase}_{point}_redundancy_rate")
    return fields


def metric_fieldnames():
    fields = ["method"]
    for phase in ("before", "during", "after"):
        for point in range(1, 6):
            fields.append(f"{phase}_{point}")
    return fields


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("mix/output"))
    parser.add_argument("--config", type=Path, default=Path("mix/output/1/config.txt"))
    parser.add_argument("--summary", type=Path, default=Path("mix/output/burst_phase_summary.csv"))
    parser.add_argument("--timeseries", type=Path, default=Path("mix/output/burst_timeseries_smoothed.csv"))
    parser.add_argument(
        "--compact-timeseries", type=Path, default=Path("mix/output/burst_timeseries_15points.csv")
    )
    parser.add_argument(
        "--compact-goodput",
        type=Path,
        default=Path("mix/output/burst_timeseries_15points_goodput.csv"),
    )
    parser.add_argument(
        "--compact-retrans",
        type=Path,
        default=Path("mix/output/burst_timeseries_15points_retrans.csv"),
    )
    parser.add_argument("--dense-step-ns", type=int, default=1000)
    parser.add_argument("--smooth-window-ns", type=int, default=15000)
    args = parser.parse_args()

    cfg = read_config(args.config)
    interval = int(cfg["BURST_STAT_INTERVAL_NS"])
    stat_start = int(cfg.get("BURST_STAT_START_NS", "0"))
    burst_start, burst_end, loss_rate = parse_schedule(cfg["BURST_LOSS_SCHEDULE"])
    phases = phase_ranges(stat_start, interval, burst_start, burst_end)
    min_bucket = min(v[0] for v in phases.values())
    max_bucket = max(v[1] for v in phases.values())
    selected = list(range(min_bucket, max_bucket))

    summary_rows = []
    ts_rows = []
    compact_rows = []
    for run_dir in sorted(args.root.glob("burst_*")):
        if not run_dir.is_dir():
            continue
        goodput_file, redundancy_file = find_pair(run_dir)
        goodput, goodput_counts = add_selected_values(goodput_file, selected)
        redundancy_sum, redundancy_counts = add_selected_values(redundancy_file, selected)
        redundancy_avg = [
            (total / count if count else 0.0)
            for total, count in zip(redundancy_sum, redundancy_counts)
        ]
        flow_count = max(goodput_counts) if goodput_counts else 0
        method = method_name(run_dir)

        for phase, (first, last, start_ns, end_ns) in phases.items():
            lo = first - min_bucket
            hi = last - min_bucket
            gp = summarize(goodput, lo, hi)
            rr = summarize(redundancy_avg, lo, hi)
            summary_rows.append(
                {
                    "method": method,
                    "run_dir": str(run_dir),
                    "phase": phase,
                    "phase_start_ns": start_ns,
                    "phase_end_ns": end_ns,
                    "raw_samples": gp["samples"],
                    "flow_count": flow_count,
                    "total_goodput_avg_gbps": gp["avg"],
                    "total_goodput_p50_gbps": gp["p50"],
                    "total_goodput_min_gbps": gp["min"],
                    "total_goodput_max_gbps": gp["max"],
                    "avg_redundancy_rate": rr["avg"],
                    "p50_redundancy_rate": rr["p50"],
                    "min_redundancy_rate": rr["min"],
                    "max_redundancy_rate": rr["max"],
                    "retrans_pkts_from_redundancy_file": "NA",
                    "note": "redundancy file stores retrans_data_pkts/tx_data_pkts; tx_data_pkts is not present",
                }
            )

        dense_gp = interpolate_series(goodput, interval, args.dense_step_ns, args.smooth_window_ns)
        dense_rr = interpolate_series(
            redundancy_avg, interval, args.dense_step_ns, args.smooth_window_ns
        )
        target_len = ((max_bucket - min_bucket) * interval) // args.dense_step_ns
        dense_gp = extend_to_length(dense_gp, target_len)
        dense_rr = extend_to_length(dense_rr, target_len)
        start_ns = stat_start + min_bucket * interval
        compact_rows.extend(
            five_point_rows(
                method, dense_gp, dense_rr, start_ns, burst_start, phases, args.dense_step_ns
            )
        )
        for i, (gp_value, rr_value) in enumerate(zip(dense_gp, dense_rr)):
            t = start_ns + i * args.dense_step_ns
            ts_rows.append(
                {
                    "method": method,
                    "time_ns": t,
                    "time_us_from_burst_start": (t - burst_start) / 1000.0,
                    "phase": phase_for_time(t, phases),
                    "smoothed_total_goodput_gbps": gp_value,
                    "smoothed_avg_redundancy_rate": rr_value,
                    "derived_from_raw_interval_ns": interval,
                    "dense_step_ns": args.dense_step_ns,
                    "smooth_window_ns": args.smooth_window_ns,
                }
            )

    args.summary.parent.mkdir(parents=True, exist_ok=True)
    with args.summary.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(summary_rows[0].keys()))
        writer.writeheader()
        writer.writerows(summary_rows)

    with args.timeseries.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(ts_rows[0].keys()))
        writer.writeheader()
        writer.writerows(ts_rows)

    compact_wide_rows = compact_rows_to_wide(compact_rows)
    with args.compact_timeseries.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=compact_fieldnames())
        writer.writeheader()
        writer.writerows(compact_wide_rows)

    goodput_rows = metric_rows_to_wide(compact_rows, "smoothed_total_goodput_gbps")
    with args.compact_goodput.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=metric_fieldnames())
        writer.writeheader()
        writer.writerows(goodput_rows)

    retrans_rows = metric_rows_to_wide(compact_rows, "smoothed_avg_redundancy_rate")
    with args.compact_retrans.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=metric_fieldnames())
        writer.writeheader()
        writer.writerows(retrans_rows)

    print(f"wrote {args.summary}")
    print(f"wrote {args.timeseries}")
    print(f"wrote {args.compact_timeseries}")
    print(f"wrote {args.compact_goodput}")
    print(f"wrote {args.compact_retrans}")
    print(f"burst {burst_start}:{burst_end}, loss_rate={loss_rate}, interval={interval} ns")


if __name__ == "__main__":
    main()
