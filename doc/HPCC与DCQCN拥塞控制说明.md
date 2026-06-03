# HPCC 与 DCQCN 拥塞控制说明

> HPCC：`CC_MODE=3`  
> DCQCN：`CC_MODE=1`  
> 模式枚举：`src/point-to-point/model/rdma-queue-pair.h`  
> 主要实现：`src/point-to-point/model/rdma-hw.cc`、`src/point-to-point/model/switch-node.cc`  
> 配置入口：`scratch/network-load-balance.cc`

## 1. 模式入口

`CC_MODE` 从 `config.txt` 读入后，会同时配置 Host RDMA 和交换机：

```cpp
// scratch/network-load-balance.cc
rdmaHw->SetAttribute("CcMode", UintegerValue(cc_mode));
sw->SetAttribute("CcMode", UintegerValue(cc_mode));
```

队列对中定义的枚举如下：

```cpp
// src/point-to-point/model/rdma-queue-pair.h
enum CcMode {
    CC_MODE_DCQCN = 1,
    CC_MODE_HPCC = 3,
    CC_MODE_TIMELY = 7,
    CC_MODE_DCTCP = 8,
    CC_MODE_UNDEFINED = 0,
};
```

ACK 到达发送端后，`RdmaHw::ReceiveAck()` 根据 `m_cc_mode` 分发：

```cpp
if (cnp) {
    if (m_cc_mode == 1) {
        cnp_received_mlx(qp);
    }
}

if (m_cc_mode == 3) {
    bool ack_progress = (qp->snd_una > old_snd_una);
    HandleAckHp(qp, p, ch, ack_progress);
}
```

一句话区别：

- DCQCN 用交换机 ECN 标记触发接收端回 CNP，发送端根据 CNP 降速，再用定时器恢复速率。
- HPCC 用 INT 在 ACK 中携带逐跳队列、字节计数、时间戳、链路速率，发送端直接估计瓶颈利用率并更新窗口/速率。

## 2. DCQCN：`CC_MODE=1`

### 2.1 相关配置项

| 配置项 | 作用 |
|---|---|
| `ENABLE_QCN` | 启用交换机 ECN marking，是 DCQCN 反馈来源。 |
| `KMIN_MAP/KMAX_MAP/PMAX_MAP` | 每种链路速率的 ECN marking 阈值和最大概率。 |
| `ALPHA_RESUME_INTERVAL` | alpha EWMA 更新周期，单位 us。 |
| `EWMA_GAIN` | alpha EWMA 增益 `g`。 |
| `RATE_DECREASE_INTERVAL` | 降速检查周期，单位 us。 |
| `CLAMP_TARGET_RATE` | 收到 CNP 后是否 clamp target rate。 |
| `RP_TIMER` | 速率恢复定时器，单位 us。 |
| `FAST_RECOVERY_TIMES` | Fast Recovery 阶段次数阈值。 |
| `RATE_AI` | Active Increase 加性增长步长。 |
| `RATE_HAI` | Hyper Active Increase 加性增长步长。 |
| `MIN_RATE` | 最低速率。 |
| `CNP_OUTPUT_FILE` | CNP 统计输出，仅 `CC_MODE=1` 打开。 |

### 2.2 交换机侧：ECN 标记

交换机出队时检查队列是否拥塞，如果达到 ECN 条件，就把 IP ECN 置为 `0x03`：

```cpp
// src/point-to-point/model/switch-node.cc
if (m_ecnEnabled) {
    bool egressCongested = m_mmu->ShouldSendCN(ifIndex, qIndex);
    if (egressCongested) {
        PppHeader ppp;
        Ipv4Header h;
        p->RemoveHeader(ppp);
        p->RemoveHeader(h);
        h.SetEcn((Ipv4Header::EcnType)0x03);
        p->AddHeader(h);
        p->AddHeader(ppp);
    }
}
```

阈值来自配置表：

```cpp
// scratch/network-load-balance.cc
uint64_t rate = dev->GetDataRate().GetBitRate();
sw->m_mmu->ConfigEcn(j, rate2kmin[rate], rate2kmax[rate], rate2pmax[rate]);
```

### 2.3 接收端：ACK 中携带 CNP 标志

RDMA 接收端收到数据包后，如果包头 ECN 有标记，生成 ACK/NACK 时设置 CNP flag：

```cpp
// src/point-to-point/model/rdma-hw.cc
if (ecnbits || cnp_check) {
    cnp_total++;
    if (ecnbits) cnp_by_ecn++;
    if (cnp_check) cnp_by_ooo++;
    seqh.SetCnp();
}
```

这里的 `cnp_check` 主要来自乱序/NACK 路径，`ecnbits` 来自交换机 ECN 标记。`cnp_freq_monitoring()` 会周期性把 `cnp_by_ecn/cnp_by_ooo/cnp_total` 写入 `CNP_OUTPUT_FILE`。

### 2.4 发送端：收到 CNP 后更新 alpha 并降速

收到带 CNP flag 的 ACK 后，发送端调用 `cnp_received_mlx()`：

```cpp
void RdmaHw::cnp_received_mlx(Ptr<RdmaQueuePair> q) {
    q->mlx.m_alpha_cnp_arrived = true;
    q->mlx.m_decrease_cnp_arrived = true;
    if (q->mlx.m_first_cnp) {
        q->mlx.m_alpha = 1;
        q->mlx.m_alpha_cnp_arrived = false;
        ScheduleUpdateAlphaMlx(q);
        ScheduleDecreaseRateMlx(q, 1);
        q->mlx.m_targetRate = q->m_rate = m_rateOnFirstCNP * q->m_rate;
        q->mlx.m_first_cnp = false;
    }
}
```

alpha 使用二值反馈做 EWMA：

```cpp
void RdmaHw::UpdateAlphaMlx(Ptr<RdmaQueuePair> q) {
    if (q->mlx.m_alpha_cnp_arrived) {
        q->mlx.m_alpha = (1 - m_g) * q->mlx.m_alpha + m_g;
    } else {
        q->mlx.m_alpha = (1 - m_g) * q->mlx.m_alpha;
    }
    q->mlx.m_alpha_cnp_arrived = false;
    ScheduleUpdateAlphaMlx(q);
}
```

降速公式：

```cpp
void RdmaHw::CheckRateDecreaseMlx(Ptr<RdmaQueuePair> q) {
    ScheduleDecreaseRateMlx(q, 0);
    if (q->mlx.m_decrease_cnp_arrived) {
        bool clamp = true;
        if (!m_EcnClampTgtRate) {
            if (q->mlx.m_rpTimeStage == 0) clamp = false;
        }
        if (clamp) {
            q->mlx.m_targetRate = q->m_rate;
        }
        q->m_rate = std::max(m_minRate, q->m_rate * (1 - q->mlx.m_alpha / 2));
        q->mlx.m_rpTimeStage = 0;
        q->mlx.m_decrease_cnp_arrived = false;
        Simulator::Cancel(q->mlx.m_rpTimer);
        q->mlx.m_rpTimer = Simulator::Schedule(
            MicroSeconds(m_rpgTimeReset), &RdmaHw::RateIncEventTimerMlx, this, q);
    }
}
```

### 2.5 发送端：速率恢复

DCQCN 恢复分三段：

```cpp
void RdmaHw::RateIncEventMlx(Ptr<RdmaQueuePair> q) {
    if (q->mlx.m_rpTimeStage < m_rpgThreshold) {
        FastRecoveryMlx(q);
    } else if (q->mlx.m_rpTimeStage == m_rpgThreshold) {
        ActiveIncreaseMlx(q);
    } else {
        HyperIncreaseMlx(q);
    }
}
```

对应行为：

- `FastRecoveryMlx()`：`m_rate = (m_rate + targetRate) / 2`
- `ActiveIncreaseMlx()`：`targetRate += RATE_AI`，再折中到当前速率
- `HyperIncreaseMlx()`：`targetRate += RATE_HAI`，再折中到当前速率

## 3. HPCC：`CC_MODE=3`

### 3.1 相关配置项

| 配置项 | 作用 |
|---|---|
| `U_TARGET` | 目标利用率，写入 `TargetUtil`，样例为 0.85。 |
| `MI_THRESH` | HPCC 连续增长阶段阈值，写入 `hp.m_maxStage`。 |
| `FAST_REACT` | 是否启用 fast react。 |
| `SAMPLE_FEEDBACK` | fast react 时是否采样队列非空的 hop。 |
| `RATE_BOUND` | 是否用 HPCC 计算出的 `qp->m_rate` 控制发送间隔。 |
| `HAS_WIN` | 是否设置 flow window；HPCC 自己的 `hp.m_W` 也会参与窗口计算。 |
| `GLOBAL_T` | flow 初始化时使用全局最大 RTT/BDP 还是 pair 级 RTT/BDP。 |
| `INT_MULTI` | INT hop 数据倍率。 |
| `HPCC_TRACE` | 打印 `[HPCC_*]`、`[INT_PUSH]` 等调试日志。 |
| `HPCC_TRACE_INTERVAL` | trace 采样间隔。 |

### 3.2 INT 模式开启

`CC_MODE=3` 时，程序设置 `IntHeader::mode = 0`，即使用 INT：

```cpp
// scratch/network-load-balance.cc
if (cc_mode == 7)
    IntHeader::mode = 1;
else if (cc_mode == 3)
    IntHeader::mode = 0;
else
    IntHeader::mode = 5;
```

### 3.3 交换机侧：把逐跳遥测写入数据包

交换机在 UDP 数据包出队时压入 INT hop：

```cpp
// src/point-to-point/model/switch-node.cc
if (m_ccMode == 3) {
    ih->PushHop(Simulator::Now().GetTimeStep(),
                m_txBytes[ifIndex],
                dev->GetQueue()->GetNBytesTotal(),
                dev->GetDataRate().GetBitRate());
}
```

每个 hop 记录：

- 当前交换机时间戳
- 该出端口累计发送字节 `m_txBytes[ifIndex]`
- 当前出队列长度
- 出端口链路速率

### 3.4 接收端：ACK 带回 INT Header

接收端生成 ACK/NACK 时，会把数据包中的 `IntHeader` 放入 ACK：

```cpp
// src/point-to-point/model/rdma-hw.cc
seqh.SetSeq(seq);
seqh.SetPG(ch.udp.pg);
seqh.SetSport(canonicalFlowSport);
seqh.SetDport(ch.udp.dport);
seqh.SetIntHeader(ch.udp.ih);
```

所以 HPCC 的控制闭环发生在“数据包携带 INT 到接收端，ACK 再把 INT 带回发送端”。

### 3.5 QP 初始化：HPCC 初始窗口和速率

创建 QP 时，如果 `m_cc_mode == 3`，发送速率先设为 NIC 线速，并按 base RTT 初始化窗口：

```cpp
// src/point-to-point/model/rdma-hw.cc
if (m_cc_mode == 3) {
    qp->hp.m_curRate = m_bps;
    qp->hp.m_baseRtt = std::max(1e-9, (double)qp->m_baseRtt * 1e-9);
    qp->hp.m_targetUtil = m_targetUtil;
    qp->hp.m_eta = std::max(1e-6, m_targetUtil);
    qp->hp.m_WAI = std::max<double>(1.0, m_mtu);
    qp->hp.m_maxStage = m_miThresh;
    double initWin = qp->m_max_rate.GetBitRate() * qp->hp.m_baseRtt / 8.0;
    initWin = std::max(initWin, 1.0);
    qp->hp.m_W = initWin;
    qp->hp.m_Wc = initWin;
}
```

HPCC 模式下，`RdmaQueuePair::GetWin()` 直接返回 `hp.m_W`：

```cpp
uint64_t RdmaQueuePair::GetWin() {
    if (m_cc_mode == CC_MODE_HPCC) {
        return (uint64_t)std::max(0.0, hp.m_W);
    }
    ...
}
```

### 3.6 发送端：ACK 驱动 HPCC 更新

ACK 到达后，HPCC 会区分完整 RTT 更新和 fast react：

```cpp
void RdmaHw::HandleAckHp(Ptr<RdmaQueuePair> qp, Ptr<Packet> p,
                         CustomHeader &ch, bool ack_progress) {
    uint32_t ack_seq = ch.ack.seq;
    if (!ack_progress && ch.l3Prot == 0xFC) {
        return;
    }
    if (ack_seq > qp->hp.m_lastUpdateSeq) {
        qp->hp.m_fastReactBudget = 4;
        UpdateRateHp(qp, p, ch, false);
    } else {
        if (ack_progress) {
            FastReactHp(qp, p, ch);
        }
    }
}
```

`FastReactHp()` 受 `FAST_REACT` 和预算限制：

```cpp
void RdmaHw::FastReactHp(Ptr<RdmaQueuePair> qp, Ptr<Packet> p, CustomHeader &ch) {
    if (!m_fast_react) return;
    if (qp->hp.m_fastReactBudget == 0) return;
    if (ch.ack.seq <= qp->hp.m_lastFastReactSeq) return;
    qp->hp.m_lastFastReactSeq = ch.ack.seq;
    qp->hp.m_fastReactBudget--;
    UpdateRateHp(qp, p, ch, true);
}
```

### 3.7 HPCC 核心公式

`UpdateRateHp()` 从 INT 中计算每个 hop 的利用率估计：

```cpp
double duration = tau * 1e-9;
double txRate = (bytesNow - bytesLast) * 8.0 / duration;
double qBits = std::min(ih.hop[i].GetQlen(), prevHop.GetQlen()) * 8.0;
double uPrime = qBits / (lineRate * baseRttSec) + txRate / lineRate;
```

含义：

- `txRate / lineRate` 是链路发送速率利用率。
- `qBits / (lineRate * baseRttSec)` 把队列积压折算成利用率压力。
- 多跳中取最大的 `uPrime` 作为瓶颈反馈。

然后更新平滑利用率 `u`、窗口 `W` 和速率：

```cpp
double alpha = std::min(std::max(tauForU / baseRttSec, 0.0), 1.0);
qp->hp.u = (1.0 - alpha) * qp->hp.u + alpha * maxUPrime;

if (qp->hp.u >= eta || qp->hp.m_incStage >= qp->hp.m_maxStage) {
    double ratio = std::max(qp->hp.u / eta, 1e-6);
    W = qp->hp.m_Wc / ratio + qp->hp.m_WAI;
    if (updateWc) {
        qp->hp.m_incStage = 0;
        qp->hp.m_Wc = W;
    }
} else {
    W = qp->hp.m_Wc + qp->hp.m_WAI;
    if (updateWc) {
        qp->hp.m_incStage++;
        qp->hp.m_Wc = W;
    }
}

qp->hp.m_W = std::max(W, 1.0);
double rateBps = qp->hp.m_W * 8.0 / baseRttSec;
DataRate new_rate((uint64_t)rateBps);
if (new_rate < m_minRate) new_rate = m_minRate;
if (new_rate > qp->m_max_rate) new_rate = qp->m_max_rate;
ChangeRate(qp, new_rate);
qp->hp.m_curRate = new_rate;
```

### 3.8 多路径场景下的 HPCC 处理

本项目的 HPCC 对 PSN-PATH 做了额外适配。若启用路径选择，会按 ACK 对应的 PSN 找到 pathId，并为不同路径维护独立 INT baseline：

```cpp
uint32_t hpPathId = 0;
if (qp->psnPath.pathSelectionEnabled && !qp->psnPath.pathStats.empty()) {
    uint32_t ackPsn = PsnPath::GetPsnFromSeq(ch.ack.seq, qp->psnPath.packetSize);
    hpPathId = SelectPathFromStats(ackPsn, qp->psnPath.k, qp->psnPath.o,
                                   qp->psnPath.pathStats);
}
HpPathState &hpPath = qp->hp.pathState[hpPathId];
```

这样做的原因是不同 ECMP 路径的 hop 序列、队列和字节计数可能不同；如果共用一个 baseline，`txRate` 和 `tau` 会被路径切换污染。

## 4. 该怎么选

| 目标 | 建议 |
|---|---|
| 复现实验中基于 ECN/CNP 的 RoCE 拥塞控制 | 用 `CC_MODE=1`，同时开启 `ENABLE_QCN=1`，检查 `KMIN/KMAX/PMAX`。 |
| 研究低延迟、精细遥测反馈、INT 驱动的速率控制 | 用 `CC_MODE=3`，关注 `U_TARGET`、`FAST_REACT`、`MI_THRESH`。 |
| 看拥塞点和 CNP 频率 | DCQCN 更直接，查看 `CNP_OUTPUT_FILE`。 |
| 看逐跳队列和速率估计 | HPCC 更合适，打开 `HPCC_TRACE=1` 和必要时 `INT_TRACE=1`。 |
| 与重传方法组合实验 | 两者都可以和 IRN/GBN/Bitmap/Falcon 组合，但 HPCC 的 ACK/INT 反馈对乱序和重复 ACK 更敏感，建议保留 `ack_progress` 相关保护。 |

## 5. 最小配置示例

DCQCN：

```text
CC_MODE 1
ENABLE_QCN 1
ENABLE_PFC 1
ENABLE_IRN 0
EWMA_GAIN 0.00390625
RATE_DECREASE_INTERVAL 4
RP_TIMER 300
RATE_AI 40.0Mb/s
RATE_HAI 100.0Mb/s
MIN_RATE 100Mb/s
```

HPCC：

```text
CC_MODE 3
ENABLE_QCN 1
ENABLE_IRN 1
U_TARGET 0.85
MI_THRESH 12
FAST_REACT 0
RATE_BOUND 1
HPCC_TRACE 1
HPCC_TRACE_INTERVAL 1
```

