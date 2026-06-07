from pathlib import Path


def main() -> None:
    leaf_start = 0
    leaf_count = 32
    spine_start = 32
    spine_count = 32
    host_start = 64
    hosts_per_leaf = 32

    rate = "100Gbps"
    delay = "1000ns"
    # 普通主机链路无丢包，叶->脊链路的最后一跳设置为 0.01 丢包率
    leaf_spine_error = "0.01"
    host_error = "0"

    switch_ids = list(range(leaf_start, leaf_start + leaf_count)) + list(
        range(spine_start, spine_start + spine_count)
    )

    host_count = leaf_count * hosts_per_leaf
    total_nodes = len(switch_ids) + host_count

    leaf_spine_links = leaf_count * spine_count
    leaf_host_links = host_count
    total_links = leaf_spine_links + leaf_host_links

    output_path = Path(__file__).with_name("1_topology.txt")

    with output_path.open("w", encoding="ascii", newline="\n") as f:
        f.write(f"{total_nodes} {len(switch_ids)} {total_links}\n")
        f.write(" ".join(str(sid) for sid in switch_ids) + "\n")

        for leaf_id in range(leaf_start, leaf_start + leaf_count):
            for spine_id in range(spine_start, spine_start + spine_count):
                f.write(f"{leaf_id} {spine_id} {rate} {delay} {leaf_spine_error}\n")

        for leaf_index, leaf_id in enumerate(
            range(leaf_start, leaf_start + leaf_count)
        ):
            host_base = host_start + leaf_index * hosts_per_leaf
            for host_id in range(host_base, host_base + hosts_per_leaf):
                f.write(f"{leaf_id} {host_id} {rate} {delay} {host_error}\n")


if __name__ == "__main__":
    main()
