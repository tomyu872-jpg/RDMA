#!/usr/bin/env python3
import argparse
import random
import shlex
import subprocess
from datetime import datetime
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
WORK_DIR = SCRIPT_DIR.parent
TEMPLATE_CONFIG = WORK_DIR / "mix" / "output" / "1" / "config.txt"
SUMMARY_FILE = WORK_DIR / "output" / "summary.txt"
DEFAULT_WAF_CMD = "python2 ./waf"
DEFAULT_TIMEOUT = 3600

METHOD_ORDER = ["psn_path", "gbn", "mpirn", "falcon", "ornic"]

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
        "ORNIC_BW_GBPS": 100,
        "ORNIC_RX_SEND_DELAY_NS": 9000,
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
        "ORNIC_BW_GBPS": 100,
        "ORNIC_RX_SEND_DELAY_NS": 9000,
    },
    "mpirn": {
        "ENABLE_PSN_PATH": 0,
        "ENABLE_PATH_SWITCH": 0,
        "ENABLE_PATH_AWARE_RETRANS": 0,
        "ENABLE_RX_OOO_NACK": 0,
        "ENABLE_TX_NACK_GOBACK": 0,
        "ENABLE_BITMAP_RETRANS": 1,
        "ENABLE_FALCON": 0,
        "ENABLE_ORNIC": 0,
        "ORNIC_BW_GBPS": 100,
        "ORNIC_RX_SEND_DELAY_NS": 9000,
    },
    "falcon": {
        "ENABLE_PSN_PATH": 0,
        "ENABLE_PATH_SWITCH": 0,
        "ENABLE_PATH_AWARE_RETRANS": 0,
        "ENABLE_RX_OOO_NACK": 0,
        "ENABLE_TX_NACK_GOBACK": 0,
        "ENABLE_BITMAP_RETRANS": 0,
        "ENABLE_FALCON": 1,
        "FALCON_RX_SEND_DELAY_NS": 0,
        "ENABLE_ORNIC": 0,
        "ORNIC_BW_GBPS": 100,
        "ORNIC_RX_SEND_DELAY_NS": 9000,
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
        "ORNIC_BW_GBPS": 100,
        "ORNIC_RX_SEND_DELAY_NS": 9000,
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


def unique_id(method):
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    suffix = random.randrange(10000)
    return f"burst_{method}_{timestamp}_{suffix:04d}"


def read_lines(path):
    return path.read_text(encoding="utf-8").splitlines(keepends=True)


def write_lines(path, lines):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(lines), encoding="utf-8")


def replace_config_key(lines, key, value):
    out = []
    found = False
    for line in lines:
        parts = line.strip().split(None, 1)
        if parts and parts[0] == key:
            out.append(f"{key} {value}\n")
            found = True
        else:
            out.append(line)
    if not found:
        out.append(f"{key} {value}\n")
    return out


def read_rows(path):
    rows = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue

        try:
            rows.append([float(value) for value in stripped.split()])
        except ValueError as exc:
            raise ValueError(f"line {line_number} contains non-numeric content: {line}") from exc

    return rows


def column_averages(path):
    rows = read_rows(path)
    if not rows:
        raise ValueError("no numeric rows")

    min_columns = min(len(row) for row in rows)
    if min_columns == 0:
        raise ValueError("no numeric columns")

    averages = []
    for column_index in range(min_columns):
        column_values = [row[column_index] for row in rows]
        averages.append(sum(column_values) / len(column_values))
    return averages


def format_values(values):
    return " ".join(f"{value:.10g}" for value in values)


def build_config(method, runid, run_dir, template_config):
    lines = read_lines(template_config)
    relative_run_dir = Path("mix") / "output" / runid

    for key, filename_template in RUN_OUTPUT_KEYS.items():
        filename = filename_template.format(runid=runid)
        lines = replace_config_key(lines, key, relative_run_dir / filename)

    for key, value in METHOD_FLAGS[method].items():
        lines = replace_config_key(lines, key, value)

    config_path = run_dir / "config.txt"
    write_lines(config_path, lines)
    return config_path


def run_method(method, args):
    runid = unique_id(method)
    run_dir = WORK_DIR / "mix" / "output" / runid
    run_dir.mkdir(parents=True, exist_ok=True)

    config_path = build_config(method, runid, run_dir, args.template_config)
    goodput_file = run_dir / f"{runid}_out_burst_goodput.txt"
    log_path = run_dir / "config.log"
    run_arg = f"scratch/network-load-balance {config_path}"
    run_args = shlex.split(args.waf_cmd) + ["--run", run_arg]

    print(f"Run {method}: {args.waf_cmd} --run '{run_arg}'")
    if args.dry_run:
        return method, "failed", "dry-run"

    try:
        with log_path.open("w", encoding="utf-8") as log_file:
            rc = subprocess.call(
                run_args,
                cwd=str(WORK_DIR),
                stdout=log_file,
                stderr=subprocess.STDOUT,
                timeout=args.timeout,
            )
    except subprocess.TimeoutExpired:
        return method, "failed", f"timeout after {args.timeout}s"

    if rc != 0:
        return method, "failed", f"exit code {rc}"
    if not goodput_file.exists():
        return method, "failed", "missing burst goodput output"
    if goodput_file.stat().st_size == 0:
        return method, "failed", "empty burst goodput output"

    try:
        return method, column_averages(goodput_file), None
    except Exception as exc:
        return method, "failed", str(exc)


def write_summary(results, summary_file):
    lines = []
    for method, values, _error in results:
        if values == "failed":
            lines.append(f"{method} failed\n")
        else:
            lines.append(f"{method} {format_values(values)}\n")

    summary_file.parent.mkdir(parents=True, exist_ok=True)
    summary_file.write_text("".join(lines), encoding="utf-8")


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--template-config", type=Path, default=TEMPLATE_CONFIG)
    parser.add_argument("--summary-file", type=Path, default=SUMMARY_FILE)
    parser.add_argument("--waf-cmd", default=DEFAULT_WAF_CMD)
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main():
    args = parse_args()
    if not args.template_config.exists():
        raise FileNotFoundError(f"missing template config: {args.template_config}")

    results = []
    for method in METHOD_ORDER:
        method_result = run_method(method, args)
        results.append(method_result)
        method, values, error = method_result
        if values == "failed":
            print(f"{method}: failed ({error})")
        else:
            print(f"{method}: averaged {len(values)} columns")

    write_summary(results, args.summary_file)
    print(f"wrote {args.summary_file}")


if __name__ == "__main__":
    main()
