# config.txt 配置项详解

> 对应样例：`mix/output/1/config.txt`  
> 主解析入口：`scratch/network-load-balance.cc::main()`，约 1020-1438 行  
> RDMA Host 属性绑定：`scratch/network-load-balance.cc`，约 1820-1865 行  
> 交换机/ECN/INT 绑定：`scratch/network-load-balance.cc`，约 1740-1805、1880-1890 行

## 1. 配置文件解析规则

`network-load-balance.cc` 使用 `conf >> key` 按空白读取 token，再用一串 `if/else if` 识别 key。格式是：

```text
KEY VALUE
```

注意：

- 当前文件里的 `多路径`、`路径条数`、`方法psn-path`、`方法GBN`、`方法传统位图`、`方法falcon` 都不是代码识别的配置项，只是单 token 分组标题，会被忽略。
- 不建议写成 `中文标题 123` 这种形式，因为 `123` 会在下一轮被当成 key 继续读取。
- 布尔项通常写 `0/1`。源码中有些读入类型是 `bool`，有些是 `uint32_t` 或 `int`，但实际都按 `0=false`、`非 0=true` 使用。
- 时间单位并不统一，下面每项会单独标明。

## 2. 输入与输出文件

| 配置项 | 当前值 | 类型/单位 | 作用 | 主要代码 |
|---|---:|---|---|---|
| `TOPOLOGY_FILE` | `config/1_topology.txt` | 路径 | 拓扑文件。第一行是节点数、交换机数、链路数，后续给出交换机 ID 和链路。 | `topof.open(topology_file)` |
| `FLOW_FILE` | `config/1_flow.txt` | 路径 | 流量输入文件。第一行是 flow 数量，后续每行包含 `src dst pg size start_time`。 | `flowf.open(flow_file)`、`ReadFlowInput()` |
| `FLOW_INPUT_FILE` | `mix/output/1/1_in.txt` | 路径 | 预留的输入流记录文件。当前 `ScheduleFlowInputs()` 中写入逻辑包在 `if (0)` 内，默认不会输出。 | `flow_input_stream = fopen(...)` |
| `CNP_OUTPUT_FILE` | `mix/output/1/1_out_cnp.txt` | 路径 | DCQCN 模式下记录 CNP 统计，格式为 `time nodeId cnp_by_ecn cnp_by_ooo cnp_total`。仅 `CC_MODE=1` 时打开。 | `cnp_freq_monitoring()` |
| `FCT_OUTPUT_FILE` | `mix/output/1/1_out_fct.txt` | 路径 | 每条 RDMA flow 完成时输出 FCT、重传数、路径切换数、throughput、goodput。 | `qp_finish()` |
| `PFC_OUTPUT_FILE` | `mix/output/1/1_out_pfc.txt` | 路径 | PFC pause/resume 事件输出，格式为 `time nodeId nodeType ifIndex type`。 | `get_pfc()` |
| `QLEN_MON_FILE` | `mix/output/1/1_out_qlen.txt` | 路径 | 旧版队列长度输出文件名。当前队列监控函数被 `#if(false)` 禁用，通常不会生成有效内容。 | `monitor_buffer()` 已禁用 |
| `VOQ_MON_FILE` | `mix/output/1/1_out_voq.txt` | 路径 | ConWeave 模式下输出每个 ToR 的 VOQ 数量和排队包数。仅 `LB_MODE=9` 有效。 | `periodic_monitoring()` |
| `VOQ_MON_DETAIL_FILE` | `mix/output/1/1_out_voq_per_dst.txt` | 路径 | ConWeave 模式下按目的 IP 聚合的 VOQ 详情。仅 `LB_MODE=9` 有效。 | `periodic_monitoring()` |
| `UPLINK_MON_FILE` | `mix/output/1/1_out_uplink.txt` | 路径 | 周期输出 ToR 上行端口累计发送字节，格式为 `time,ToRId,OutDev,Bytes`。 | `periodic_monitoring()` |
| `CONN_MON_FILE` | `mix/output/1/1_out_conn.txt` | 路径 | 周期输出每台服务器当前 QP 总数和 active QP 数，格式为 `time,serverId,nQP,nActiveQP`。 | `periodic_monitoring()` |
| `EST_ERROR_MON_FILE` | `mix/output/1/1_out_est_error.txt` | 路径 | ConWeave flush 估计误差输出文件名。当前输出逻辑在 `if (0)` 内，默认不会写。 | `conweave_history_print()` |

## 3. 仿真时间、监控和缓冲区

| 配置项 | 当前值 | 类型/单位 | 作用 | 调参影响 |
|---|---:|---|---|---|
| `QLEN_MON_START` | `2.0` | 读入到 `uint64_t`，实际当前无效 | 旧版队列监控开始时间。当前 `monitor_buffer()` 禁用。 | 可忽略 |
| `QLEN_MON_END` | `2.1` | 读入到 `uint64_t`，实际当前无效 | 旧版队列监控结束时间。当前 `monitor_buffer()` 禁用。 | 可忽略 |
| `SW_MONITORING_INTERVAL` | `10000` | ns | `periodic_monitoring()` 的采样间隔。 | 越小输出越细、文件越大、仿真越慢 |
| `FLOWGEN_START_TIME` | `2.0` | 秒 | 开始调度 flow 的时间，同时初始化 CNP/IRN 监控开始时间。 | 通常保持与 workload 生成脚本一致 |
| `FLOWGEN_STOP_TIME` | `30` | 秒 | flow 生成停止时间；仿真硬停止为 `FLOWGEN_STOP_TIME + 10s`，也会在全部 flow 完成后提前停止。 | 越大仿真越久 |
| `BUFFER_SIZE` | `9` | MB | 每个交换机 MMU buffer 大小，调用 `ConfigBufferSize(buffer_size * 1024 * 1024)`。 | 影响丢包、PFC、ECN、重传 |
| `ENABLE_CONSOLE_LOG` | `1` | bool | 为 0 时将 stdout/stderr 重定向到 `/dev/null`。 | 大规模实验可设 0 减少日志开销 |

## 4. 模式选择：拥塞控制、负载均衡、PFC、IRN

| 配置项 | 当前值 | 类型/单位 | 作用 | 主要代码 |
|---|---:|---|---|---|
| `CC_MODE` | `3` | 枚举 | RDMA 拥塞控制：`1=DCQCN`，`3=HPCC`，`7=TIMELY`，`8=DCTCP`。 | `RdmaQueuePair::CcMode`、`RdmaHw::ReceiveAck()` |
| `LB_MODE` | `0` | 枚举 | 负载均衡：`0=flow ECMP`，`2=DRILL`，`3=CONGA`，`6=LetFlow`，`9=ConWeave`。 | `SwitchNode::GetOutDev()` |
| `ENABLE_PFC` | `0` | bool | 是否启用 QbbNetDevice 的 PFC。 | `QbbNetDevice::QbbEnabled` |
| `ENABLE_IRN` | `1` | bool | 是否启用 IRN。启用后 QP 使用 IRN RTO、BDP 限制、SACK/Direct NACK 逻辑。 | `RdmaHw::AddQueuePair()` |

## 5. 多路径与四种重传相关项

这些项和 `doc/四种重传方式详解.md` 对应。发送端 QP 初始化时的核心逻辑在 `RdmaHw::AddQueuePair()`：

```cpp
qp->psnPath.pathSelectionEnabled = m_enablePathSelection || m_enablePsnPath;
qp->psnPath.pathSwitchEnabled = m_enablePathSwitch && qp->psnPath.pathSelectionEnabled;
qp->psnPath.pathAwareRetransEnabled =
    m_enablePathAwareRetrans || m_enablePsnPath || m_enableBitmapRetrans || m_enableFalcon;
qp->psnPath.enabled =
    qp->psnPath.pathSelectionEnabled || qp->psnPath.pathAwareRetransEnabled;
qp->psnPath.k = m_psnPathK == 0 ? 1 : m_psnPathK;
```

| 配置项 | 当前值 | 类型/单位 | 作用 | 调参建议 |
|---|---:|---|---|---|
| `ENABLE_PATH_SELECT` | `1` | bool | 启用发送端 PSN 到路径的多路径选择。会让不同 PSN 使用不同 UDP 源端口。 | 多路径实验通常开启 |
| `PSN_PATH_K` | `16` | 正整数 | PSN-PATH 的路径槽数量 K。`pathId` 会基于 PSN、K、active mask 计算。 | 应小于等于可用 ECMP/路径数量的有效范围 |
| `ENABLE_PSN_PATH` | `0` | bool | legacy 总开关。开启后隐含启用路径选择和路径感知重传。 | 单独测试 PSN-PATH 方法时开启 |
| `ENABLE_PATH_SWITCH` | `0` | bool | 启用路径状态评估和动态切换。必须同时有路径选择才生效。 | 需要研究路径故障/差异时开启 |
| `ENABLE_PATH_AWARE_RETRANS` | `0` | bool | NACK 后不直接回退整个发送窗口，而是把缺失 PSN 放入 `pendingRetrans` 队列。 | 选择性/路径感知重传实验开启 |
| `ENABLE_RX_OOO_NACK` | `1` | bool | GBN 接收端乱序立即 NACK，NACK 指向缺失的 expected seq。 | GBN 方法开启 |
| `ENABLE_TX_NACK_GOBACK` | `1` | bool | GBN 发送端收到 NACK 后把 `snd_nxt` 回退到 NACK 序号。 | GBN 方法开启 |
| `ENABLE_BITMAP_RETRANS` | `0` | bool | 启用传统 Bitmap 选择性重传模块。开启后接收端绕过 `ReceiverCheckSeq()`。 | Bitmap 方法开启 |
| `BITMAP_RETRANS_SIZE` | `128` | 包/bitmap bit 数 | Bitmap 接收窗口大小，解析时会强制至少为 1。 | 越大越能容忍乱序，状态开销越大 |
| `ENABLE_FALCON` | `0` | bool | 启用 Falcon CumAck + Bitmap 重传模块。优先级高于 Bitmap。 | Falcon 方法开启 |
| `FALCON_REO_WND_NS` | `50000` | ns | Falcon 重排序等待窗口，超过后才将疑似缺失包纳入重传。 | 越大越容忍乱序，丢包恢复越慢 |

## 6. ConWeave 参数

这些参数只有 `LB_MODE=9` 时真正影响转发逻辑。初始化位置在 `m_conweaveRouting.SetConstants(...)`。

| 配置项 | 当前值 | 类型/单位 | 作用 |
|---|---:|---|---|
| `CONWEAVE_TX_EXPIRY_TIME` | `1000` | us | Tx 侧等待 CLEAR/过期的时间。 |
| `CONWEAVE_REPLY_TIMEOUT_EXTRA` | `4` | us | reply deadline 的额外余量。 |
| `CONWEAVE_PATH_PAUSE_TIME` | `16` | us | 收到拥塞反馈后暂停/避开路径的时间。 |
| `CONWEAVE_EXTRA_VOQ_FLUSH_TIME` | `64` | us | VOQ flush 估计额外时间余量。 |
| `CONWEAVE_DEFAULT_VOQ_WAITING_TIME` | `400` | us | 没有历史信息时 VOQ 默认等待时间。 |

## 7. DCQCN 参数

这些项主要在 `CC_MODE=1` 时使用，绑定到 `RdmaHw` 的 Mellanox/DCQCN 相关属性。

| 配置项 | 当前值 | 类型/单位 | 作用 |
|---|---:|---|---|
| `ALPHA_RESUME_INTERVAL` | `1` | us | DCQCN alpha 更新周期，调度 `UpdateAlphaMlx()`。 |
| `RATE_DECREASE_INTERVAL` | `4` | us | 检查是否需要降速的周期，调度 `CheckRateDecreaseMlx()`。 |
| `CLAMP_TARGET_RATE` | `0` | bool | 收到 CNP 后是否强制把 target rate clamp 到当前速率。 |
| `RP_TIMER` | `300` | us | DCQCN rate increase timer，触发 Fast Recovery / AI / HAI。 |
| `FAST_RECOVERY_TIMES` | `1` | 次 | Fast Recovery 阶段持续的 rate increase 次数阈值。 |
| `EWMA_GAIN` | `0.00390625` | 小数 | alpha EWMA 增益 `g`。越大对 CNP 越敏感。 |
| `RATE_AI` | `40.0Mb/s` | DataRate | Active Increase 阶段 target rate 加性增长步长。 |
| `RATE_HAI` | `100.0Mb/s` | DataRate | Hyper Active Increase 阶段 target rate 加性增长步长。 |
| `MIN_RATE` | `100Mb/s` | DataRate | 降速后的最小发送速率。 |
| `DCTCP_RATE_AI` | `1000Mb/s` | DataRate | DCTCP 模式使用的 AI 步长；`CC_MODE=1/3` 时基本无关。 |

## 8. 链路错误与 L2 ACK

| 配置项 | 当前值 | 类型/单位 | 作用 |
|---|---:|---|---|
| `ERROR_RATE_PER_LINK` | `0.0000` | 概率 | 默认链路包错误率。拓扑文件单条链路也可指定非 0 error rate 覆盖。 |
| `L2_CHUNK_SIZE` | `4000` | byte | L2 chunk 大小。为 0 表示关闭 chunk 模式。 |
| `L2_ACK_INTERVAL` | `5000` | 包/字节间隔，取决于 RDMA EQ 实现 | L2 ACK 间隔。为 0 时不应收到 ACK。 |
| `L2_BACK_TO_ZERO` | `0` | bool | ACK 时是否按 chunk 边界回退确认。 |

## 9. HPCC/窗口/INT 参数

这些项主要在 `CC_MODE=3` 时影响 HPCC，也会影响发送窗口和发包速率约束。

| 配置项 | 当前值 | 类型/单位 | 作用 |
|---|---:|---|---|
| `RATE_BOUND` | `1` | bool | 是否用当前 `qp->m_rate` 限制下一次可发送时间。为 0 时按线速。 |
| `HAS_WIN` | `0` | bool/int | 是否给 RDMA flow 设置窗口。为 0 时 `win=0`，窗口不限制 in-flight。 |
| `VAR_WIN` | `0` | bool | 非 HPCC 模式下窗口是否随当前速率缩放；HPCC 使用自身 `hp.m_W`。 |
| `FAST_REACT` | `0` | bool | HPCC 是否在未满 RTT 的 ACK 反馈上做 fast react。 |
| `MI_THRESH` | `12` | 次 | HPCC 连续 AI 到 MI/乘性调整的阶段阈值，绑定 `hp.m_maxStage`。 |
| `INT_MULTI` | `1` | 整数 | INT hop 记录倍率，写入 `IntHop::multi`。 |
| `GLOBAL_T` | `1` | bool/int | 设置 flow window/base RTT 时用全局最大 BDP/RTT，还是 pair 级 BDP/RTT。 |
| `U_TARGET` | `0.85` | 小数 | HPCC 目标链路利用率，绑定 `TargetUtil`。 |
| `MULTI_RATE` | `0` | bool | HPCC 是否维护 multiple rates。当前源码中主要作为属性保留。 |
| `SAMPLE_FEEDBACK` | `0` | bool | HPCC fast react 时是否只对队列非空的 hop 反馈作反应。 |
| `HPCC_TRACE` | `1` | bool | 打印 HPCC/INT/路径/重传相关调试日志。 |
| `HPCC_TRACE_INTERVAL` | `1` | 正整数 | HPCC trace 采样间隔；解析时若为 0 会改成 1。 |

## 10. ECN、PFC 阈值和包大小

| 配置项 | 当前值 | 类型/单位 | 作用 |
|---|---:|---|---|
| `ENABLE_QCN` | `1` | bool | 是否启用交换机 ECN marking / QCN 相关逻辑。DCQCN 依赖 ECN/CNP 反馈。 |
| `USE_DYNAMIC_PFC_THRESHOLD` | `0` | bool | QbbNetDevice 是否使用动态 PFC 阈值。 |
| `PACKET_PAYLOAD_SIZE` | `8192` | byte | RDMA payload/MTU，写入 `Settings::packet_payload` 和 `RdmaHw::Mtu`。也影响 BDP、FCT、PSN 计算。 |

## 11. 链路故障、ECN 阈值表、负载和随机数

| 配置项 | 当前值 | 类型/单位 | 作用 |
|---|---:|---|---|
| `LINK_DOWN` | `0 0 0` | `time_us nodeA nodeB` | 若 `time_us > 0`，在 `FLOWGEN_START_TIME + time_us` 关闭 A-B 链路并重算路由。 |
| `KMAX_MAP` | 6 组 | `count rate kmax ...` | 按链路速率配置 ECN 最大阈值。rate 单位是 bit/s。必须覆盖拓扑中的所有链路速率。 |
| `KMIN_MAP` | 6 组 | `count rate kmin ...` | 按链路速率配置 ECN 最小阈值。 |
| `PMAX_MAP` | 6 组 | `count rate pmax ...` | 按链路速率配置 ECN 最大 marking 概率。 |
| `LOAD` | `50` | 百分比/实验元数据 | 主要由 `run.py`/流量生成阶段使用；仿真主程序只读入并打印，当前不直接改变已生成的 flow。 |
| `RANDOM_SEED` | `1` | 整数 | 设置 `srand()` 和 ns-3 `SeedManager::SetSeed()`。 |

ECN 表的使用位置：

```cpp
uint64_t rate = dev->GetDataRate().GetBitRate();
sw->m_mmu->ConfigEcn(j, rate2kmin[rate], rate2kmax[rate], rate2pmax[rate]);
```

如果拓扑里出现了配置表没有覆盖的链路速率，程序会触发 assert。

## 12. 常用组合建议

| 实验目标 | 推荐关键配置 |
|---|---|
| HPCC + ECMP + IRN + GBN | `CC_MODE=3, LB_MODE=0, ENABLE_IRN=1, ENABLE_PATH_SELECT=1, ENABLE_RX_OOO_NACK=1, ENABLE_TX_NACK_GOBACK=1` |
| DCQCN + PFC | `CC_MODE=1, ENABLE_QCN=1, ENABLE_PFC=1, ENABLE_IRN=0` |
| PSN-PATH 重传 | `ENABLE_PSN_PATH=1`，通常关闭 Bitmap/Falcon |
| Bitmap 选择性重传 | `ENABLE_BITMAP_RETRANS=1, BITMAP_RETRANS_SIZE=128/256` |
| Falcon 重传 | `ENABLE_FALCON=1, FALCON_REO_WND_NS` 根据 RTT/乱序程度调整 |
| ConWeave | `LB_MODE=9`，并重点检查五个 `CONWEAVE_*` 参数 |
