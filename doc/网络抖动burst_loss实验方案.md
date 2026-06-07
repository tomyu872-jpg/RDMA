# 网络抖动 burst loss 实验方案

本文档记录网络抖动实验的设计方案。当前阶段只定义配置、统计口径和输出格式，后续再按本文档修改仿真代码。

## 实验目标

在仿真运行过程中，按指定时间段对所有链路突然注入 burst loss，即临时提高丢包率，观察网络抖动下的性能变化。

关注指标：

- FCT
- Goodput
- 重传次数
- 路径切换次数
- 按流、按时间窗口统计的吞吐量
- 按流、按时间窗口统计的冗余重传率

## burst loss 配置

burst loss 作用范围：所有链路。

配置写入 `config.txt`，建议格式如下：

```txt
BURST_LOSS_SCHEDULE 10000000:15000000:0.01,30000000:35000000:0.02,60000000:70000000:0.005
BURST_LOSS_LOG_FILE burst_loss.txt
```

`BURST_LOSS_SCHEDULE` 每段格式为：

```txt
start_ns:end_ns:loss_rate
```

含义：

- `start_ns`：burst loss 开始时间，单位 ns
- `end_ns`：burst loss 结束时间，单位 ns
- `loss_rate`：burst 期间的包丢包率

结束后恢复到 `ERROR_RATE_PER_LINK` 配置的基础丢包率。

`BURST_LOSS_LOG_FILE` 用于记录 burst loss 生效和恢复事件，便于画图时标注抖动区间。建议输出格式：

```txt
time_ns loss_rate event
```

示例：

```txt
10000000 0.01 burst_start
15000000 0 burst_end
```

## 按流时间窗口统计配置

统计从仿真 `0ns` 开始，按固定窗口滚动统计。

配置写入 `config.txt`：

```txt
BURST_STAT_INTERVAL_NS 1000000
BURST_GOODPUT_OUTPUT_FILE burst_goodput.txt
BURST_REDUNDANCY_OUTPUT_FILE burst_redundancy.txt
```

含义：

- `BURST_STAT_INTERVAL_NS`：统计窗口长度，单位 ns
- `BURST_GOODPUT_OUTPUT_FILE`：按流吞吐量时间序列输出文件
- `BURST_REDUNDANCY_OUTPUT_FILE`：按流冗余重传率时间序列输出文件

## 统计口径

统计对象：发送端发出的 RDMA data packet。

按流标识：只使用 `src dst`。

如果同一对 `src dst` 之间存在多个 QP 或多个端口，第一版统计时合并为同一条流。

### Goodput

每个时间窗口内，按发送端发出的全部 RDMA data bytes 计算吞吐量，包含新发 data 包和重传 data 包。

公式：

```txt
goodput_gbps = tx_data_bytes * 8 / interval_ns
```

其中 `tx_data_bytes` 是该 `src dst` 在窗口内发送端发出的全部 data bytes。

由于单位是 bytes 和 ns，`bytes * 8 / ns` 的数值等于 Gbps。

### Redundancy Rate

每个时间窗口内，按重传 data 包数量占全部发送 data 包数量的比例计算。

公式：

```txt
redundancy_rate = retrans_data_pkts / tx_data_pkts
tx_data_pkts = new_data_pkts + retrans_data_pkts
```

空窗口处理：

- Goodput 输出 `0`
- Redundancy Rate 如果分母为 `0`，输出 `0`

## 输出格式

两个新文件均为一行一个 `src dst` 流，格式参考现有 `mix/output/1/1_out_fct.txt`，使用空格分隔且不加表头。

### burst_goodput.txt

格式：

```txt
src dst v0 v1 v2 v3 ...
```

其中：

- `v0` 对应 `[0, interval_ns)`
- `v1` 对应 `[interval_ns, 2 * interval_ns)`
- `v2` 对应 `[2 * interval_ns, 3 * interval_ns)`

每个 `v` 是该窗口内该 `src dst` 的发送端 data 吞吐量，单位 Gbps。

示例：

```txt
10 20 0 63.1 58.7 40.2 61.5
30 40 0 60.4 55.2 42.0 59.9
```

### burst_redundancy.txt

格式：

```txt
src dst v0 v1 v2 v3 ...
```

每个 `v` 是该窗口内该 `src dst` 的冗余重传率：

```txt
retrans_data_pkts / tx_data_pkts
```

示例：

```txt
10 20 0 0.0012 0.015 0.083 0.002
30 40 0 0 0.021 0.075 0.001
```

## 画图建议

横坐标为时间窗口：

```txt
time_ns = bucket_index * BURST_STAT_INTERVAL_NS
```

画图时可以转换成 ms：

```txt
time_ms = time_ns / 1000000
```

建议图形：

- x 轴：time
- 左 y 轴：Goodput，单位 Gbps
- 右 y 轴：Redundancy Rate
- 使用 `BURST_LOSS_LOG_FILE` 中的时间段给 burst loss 区间加半透明背景

如果要画全局曲线，可以对所有 `src dst` 的同一时间窗口做聚合：

- 全局 Goodput：同一窗口内所有流 Goodput 求和
- 全局 Redundancy Rate：建议使用全局计数重新计算，而不是直接平均各流比例

全局冗余重传率应为：

```txt
global_redundancy_rate = sum(retrans_data_pkts) / sum(tx_data_pkts)
```

## 后续代码修改位置

建议后续优先在发送端 RDMA data packet 发出的位置统计，因为这里可以同时获得：

- 当前 packet 所属 `src dst`
- 是否为 data packet
- 是否为重传 packet
- packet size
- 当前仿真时间

burst loss 调度建议复用现有 `RateErrorModel`，在指定时间调用 `SetAttribute("ErrorRate", DoubleValue(...))` 动态调整丢包率。
