# NS-3 RDMA 拥塞控制对比实验平台

本仓库基于 [ConWeave (SIGCOMM'23)](https://doi.org/10.1145/3603269.3604849) 的 NS-3 仿真器扩展而来，目标是 **在同一份仿真代码内对比多种 RDMA 拥塞控制 (CC) 算法**。

`guard` 分支已经把以下三种独立的 CC 实现整合到同一棵代码树里，可以通过 `--cc` 参数一键切换：

| `--cc`        | `cc_mode` | 算法                                                        |
| ------------- | --------- | ----------------------------------------------------------- |
| `dcqcn`       | 1         | Mellanox 版 DCQCN（原仓库自带）                              |
| `hpcc`        | 3         | 原版 HPCC（in-network INT 反馈），无任何接收端干预          |
| `timely`      | 7         | TIMELY（基于 RTT）                                          |
| `dctcp`       | 8         | DCTCP（原仓库自带）                                         |
| **`guard`**     | **11**    | HPCC + 接收端等分配额上限 + EWMA 主动配额释放                |
| **`homa`** | **12**    | Homa ns-3 reimplementation：receiver grants、SRPT、动态优先级与原生 RESEND |
| `guard-active-only` | 13 | GUARD receiver-rate-only 组件消融（无 INT/HPCC 环） |

> "guard" 是本仓库提出的算法，目标是在保留 HPCC in-network 反馈的同时，在接收端再加一层基于活跃流数的等分配额上限 + 主动尾部释放，主要改善大流的尾延迟。

---

## 1. 三种重点算法的设计

### 1.1 HPCC（`cc_mode=3`，对照基线）

完全保持 HPCC 原始逻辑：交换机在数据包内附 INT (qlen / txBytes / ts)，接收端把 INT 透传回 ACK，发送端按公式更新速率。本分支没有对 HPCC 做任何修改，作为基线对照。

### 1.2 Guard（`cc_mode=11`，本分支提出的算法）

在 HPCC 之上叠加一层 **接收端驱动的速率配额 (rate grant)**：

1. **接收端速率请求**：当一条流的第一个数据包（带 `FlowStatTag::FLOW_START`）到达，且总流大小 > 1 BDP，接收端把它登记到本 NIC 的活跃流集合 `m_rate_flow_ctl_set`。
2. **等分配额**：接收端把 `线速 / 活跃流数 N` 作为速率上限，给集合内 *所有* 流广播一个 `0xFB` Rate Grant 包。这是个**基于当前活跃流数的静态等分**——只在流加入 / 退出集合时重算，且不做"剩余带宽再分配"，所以**不是严格意义上的 max-min fair**；只有在接收 NIC 是唯一瓶颈、所有发送端都能跑满 `线速/N` 时，它才与 max-min 分配等价。
3. **发送端处理**：发送端把 grant 速率写进 `qp->hp.m_grantRate`，最终发送速率取 `min(HPCC 算出的速率, m_grantRate)`，由新增的 `SyncHwRate()` 统一下发。也就是说 grant 和 HPCC 各提供一个上限；HPCC 不会把受限流未使用的 `线速/N` 份额再分给其他流。
4. **主动配额释放（Proactive Release）**：接收端用 EWMA（`β=0.125`）实时估算每条流的瞬时接收速率，当 *剩余字节* < `est_rate × baseRTT × γ`（`γ=1.0`）时，认为这条流的剩余流量已经全部在飞行中，主动把它从集合里移除并广播新一轮 grant。
5. **INT hop 截断**：guard 模式下，接收端在回 ACK 前 *删掉最后一跳的 INT 信息*——因为接收端下行链路已由 receiver rate-grant 环管理，HPCC 不需要再为这一跳计算第二个速率约束。

组件消融使用明确的名字：`--cc hpcc` 是 reactive-only，`--cc guard-active-only`
是 receiver-rate-only，`--cc guard` 才是两环同时运行的完整 GUARD。不要把
`guard-active-only` 称为 Homa 或 ACC。

控制包用一个新的 IPv4 协议号 `0xFB`（与 ACK=0xFC、NACK=0xFD、CNP=0xFF 并列），交换机按最高优先级转发。

**v2 借鉴 homa 的两点改进**（`guard-borrow-homa` 分支）：

6. **短流走高优先级队列（按 m_size 分桶）**：v1 所有数据包都用 traffic_gen 给的同一个 pg（=3），导致短流和长流挤在 switch 的同一个 priority queue 里。v2 在 `AddQueuePair` 里按 m_size 分四档：`< BDP/4 → pg 1`、`< BDP/2 → pg 2`、`< BDP → pg 3`、`≥ BDP → pg 4`，整条流统一用这个 pg。短流路由到更高优先级队列、不再被长流堵。HPCC 的 INT 反馈、PFC pause 检查、RCC grant 路由全部仍然一致（每条流自己的 pg 是稳定的，跟 homa 那种 per-packet 变化不一样）。
7. **RCC 触发阈值从硬编码 BDP 改成 baseRtt-derived**：v1 在 `ReceiveUdp` 里写死 `bdp = 104000`（按 leaf_spine 100G + 8.32µs RTT 算出来的），换拓扑会误判。v2 用 `FlowStatTag::GetBaseRttSeconds()` × 接收 NIC 线速算每条流自己的 BDP，跨拓扑正确（在 leaf_spine_8 上数值不变，行为不变；在 fat_k8 / 不同 RTT 拓扑下避免错把中等流注册到 RCC 等分集合）。

### 1.3 Homa ns-3 基线（`cc_mode=12`）

该模式参考 [SIGCOMM'18 Homa](https://dl.acm.org/doi/10.1145/3230543.3230564)
的接收端调度与 packet format，实现了可审计的 **Homa ns-3 reimplementation**。
它不是 PlatformLab 实现的源码移植；论文图中必须标为 `Homa (ns-3)`，并披露
本节末尾的模拟边界。当前实现包括：

1. **HomaHeader（每个数据包都携带）**：64 字节，含 `type`（DATA/GRANT/RESEND/BUSY/NEED_ACK/ACK/UNKNOWN）+ 该 type 用得到的字段联合（DATA 段：`msg_total_length / pkt_offset / pkt_length / unscheduled_bytes / priority`；GRANT 段：`granted_offset / grant_priority`；RESEND 段：`resend_offset / resend_length / restart_priority`）。
2. **HomaScheduler（接收端，per-NIC）**：SRPT 二叉堆按 `bytes_remaining_to_grant` 排序；定时器每 `pacing_interval`（=MTU/线速）处理 top-N 流，给每个发 GRANT。每条选中消息的“已授权但尚未收到”字节限制在约 1 BDP，避免把阻塞消息一次性 grant 完；`--homa_overcommit` 默认等于 scheduled priority 数，也可显式设为 1--6。
3. **Workload-derived per-packet priority**：PG 0 留给控制包，PG 1--7 给 DATA。`run.py` 从标准 CDF（自定义 trace 则从冻结 snapshot）计算 unscheduled-byte 占比，据此划分 unscheduled/scheduled priority 数，并让每个 unscheduled 桶承载近似相等的字节；两组 priority 不重叠。scheduled 消息不足时使用最低的可用 priority，给新到短消息保留抢占空间。交换机在 Homa mode 对 PG 1--7 执行严格优先级调度，发送 NIC 在 ready message 间执行 SRPT。
4. **PFC-free 数据队列**：SwitchMmu 加了 `m_PFCenabledPg[qCnt]` per-PG 开关，`scratch/network-load-balance.cc` 在 cc_mode=12 时把 PG 1–7（数据队列）的 PFC 关掉，只保留 PG 0（控制包）的 PFC。
5. **Native RESEND 路径**：接收端用不相交 byte-range map 跟踪乱序 DATA，并保留状态直到消息完整；默认 1 ms 无进展后发送 `RESEND{offset,length}`。Homa DATA 不再进入仓库通用 RDMA ACK/NACK、Go-Back-N 或 sender RTO。发送端优先处理 RESEND range。
6. **有界审计统计**：`out_guard_stats.txt` 记录 DATA/GRANT/RESEND/completion notice、message lifecycle、最大 pending messages 和 PG 0--7 的 DATA packet/byte 数；可同时核对通用 recovery、PFC 与 switch drop 是否为零。

**用法**：

```bash
# PFC 在 NIC 层开（控制包用），数据 PG 由 MMU 关掉，RESEND 自己恢复
python3 run.py --cc homa --pfc 1 --irn 0 --simul_time 0.01 --netload 25 --topo leaf_spine_8_100G_OS1
# 默认 priority profile 来自 CDF；custom flow 自动使用冻结 snapshot 的分布
python3 run.py --cc homa --pfc 1 --irn 0 --flow_file experiments/inputs/homa_loss_incast.txt --simul_time 0.01 --topo leaf_spine_16_100G_OS4
```

机制资格结果和固定输入见 [`docs/homa-qualification.md`](docs/homa-qualification.md)：
无丢包 AliStorage 完成 6,056/6,056；forced-loss 五个 seed 均只用原生 RESEND
完成；sender SRPT 与 receiver overcommit 定向测试也通过。

> **仍需披露的模拟边界：**
>
> - 模拟器的 PG 0 专用于控制，因此只有 7 个 DATA priority，而论文环境有
>   8 个可用于数据调度的 priority。
> - workload profile 来自配置 CDF；custom trace 使用其冻结 size distribution。
>   这属于已知 workload 配置，不应解释为在线自适应。
> - 仓库流量模型是单向 message，不产生 RPC response；实现用 ACK-shaped
>   completion notice 结束 sender QP，这只是模拟 plumbing，不是 Homa 的显式 ACK。
> - `BUSY`、`NEED_ACK`、`UNKNOWN` 尚未实现；当前资格没有覆盖“所有初始 DATA
>   全丢”或 completion notice 自身丢失。
>
> 因此它可以作为同一 ns-3 环境中的 `Homa (ns-3)` 基线，但不能声称与
> PlatformLab Homa bit-for-bit 等价，也不能拿跨模拟器结果直接比较。

---

## 2. 关键文件

```
src/point-to-point/model/
├── rdma-hw.{h,cc}          # 主体逻辑：ReceiveUdp 分支、UpdateRateHp、HomaScheduler、SyncHwRate
├── rdma-queue-pair.{h,cc}  # QP 结构：hp.m_grantRate（guard）、homa.{m_unscheduled_bytes,m_granted_offset,m_retransmit_queue,...}
├── homa-header.{h,cc} # Homa 每包 header（64B）：type + DATA/GRANT/RESEND 联合字段
├── flow-stat-tag.{h,cc}    # 在 packet tag 里透传 baseRTT，给 guard 的 EWMA 阈值使用
├── switch-node.cc          # 0xFB / 0xFA 走 ECMP + 高优先级（qIndex=0）
└── switch-mmu.{h,cc}       # 加 m_PFCenabledPg[qCnt]：homa 关掉数据 PG 的 PFC

src/network/utils/
├── custom-header.{h,cc}    # 按 IntHeader::mode 解析 Homa 字段；接受 0xFB / 0xFA
└── int-header.h            # IntHeader::mode：0=INT (HPCC/Guard) / 1=TS (Timely) / 3=Homa / 5=none

src/point-to-point/model/qbb-net-device.cc
                            # 发送侧：mode==3 (homa) 按 unsched/granted 上限 + retransmit queue 决定可发

scratch/network-load-balance.cc
                            # cc_mode → IntHeader::mode 映射；cc_mode=12 时关数据 PG 的 PFC；BW / qlen / flow-bw 监控

docs/homa-plan.md      # Homa 增量实现 plan（PR1-PR6 拆分 + 设计决策）

run.py                      # 入口脚本：cc_modes 字典、配置模板、调度仿真+分析
```

---

## 3. 编译

`waf 1.7.11` 是 ns-3.19 自带的，**只能用 Python 2** 引导（脚本里嵌了 bz2 二进制 archive，Python 3 无法解析含空字节的源码）。Debian 13 / Ubuntu 22.04+ 默认没有 Python 2，需要用 pyenv 装一个：

```bash
# 1. 安装 pyenv
curl -L https://pyenv.run | bash
export PYENV_ROOT="$HOME/.pyenv"
export PATH="$PYENV_ROOT/bin:$PATH"
eval "$(pyenv init - bash)"

# 2. 装 python 2.7.18 并锁定到当前仓库
pyenv install 2.7.18
cd /path/to/this/repo
pyenv local 2.7.18

# 3. 配置 + 编译
./waf configure --build-profile=optimized
./waf build
```

> 仿真本身、`run.py`、`fctAnalysis.py` 等运行时脚本仍然用 **Python 3**，只有 `./waf` 这个引导器需要 Python 2。

---

## 4. 跑仿真

```bash
python3 run.py --cc <hpcc|guard|homa|...> \
               --lb fecmp \
               --pfc 1 --irn 0 \
               --simul_time 0.01 \
               --netload 25 \
               --topo leaf_spine_8_100G_OS1
```

`run.py` 默认使用 `--monitor_profile bulk`：保留 FCT、PFC、GUARD 统计、配置文件以及
有界的队列摘要，但不写队列、节点带宽、逐流带宽、上行链路和连接数时序。只有在小规模、
有界诊断中才应使用 `--monitor_profile full`。队列采样间隔可用
`--qlen_monitoring_interval <ns>` 设置，采样严格限制在配置的队列监控时间窗内。
正式运行至少需要 10ms，默认排除前 5ms warm-up；5ms 诊断必须显式加 `--smoke`，
且不产生可误用的正式 FCT 摘要。`--max_flows` 默认在 150000 flows 时中止，
该检查在创建结果目录和启动 ns-3 之前完成。

reviewer 实验的示例：

```bash
# 完整 GUARD；有效 HPCC target = 0.95 * 1.4 = 1.33
python3 run.py --cc guard --guard_lambda 1.4 --guard_beta 0.125 \
  --guard_gamma 1.0 --seed 3 --pfc 1 --irn 0 \
  --simul_time 0.01 --netload 25 --topo leaf_spine_8_100G_OS1

# 保留 last-hop INT，测量两个控制环重复响应的消融
python3 run.py --cc guard --guard_keep_last_hop_int 1 --seed 3 \
  --pfc 1 --irn 0 --simul_time 0.01 --netload 25 \
  --topo leaf_spine_8_100G_OS1

# 分开消融 OFLM 的两部分：所有流注册，但仍允许提前释放
python3 run.py --cc guard --guard_selective_registration 0 \
  --guard_proactive_release 1 --seed 3 \
  --pfc 1 --irn 0 --simul_time 0.01 --netload 25 \
  --topo leaf_spine_8_100G_OS1

# 旧命令仍可用：显式 0 会同时关闭选择性注册和提前释放
python3 run.py --cc guard --guard_oflm 0 --seed 3 \
  --pfc 1 --irn 0 --simul_time 0.01 --netload 25 \
  --topo leaf_spine_8_100G_OS1

# 只用于小规模归因：逐流记录 OFLM 注册、释放和完成时刻
python3 run.py --cc guard --guard_lifecycle_trace 1 \
  --guard_lifecycle_max_lines 16 --seed 3 \
  --pfc 1 --irn 0 --simul_time 0.01 --netload 25 \
  --topo leaf_spine_8_100G_OS1

# 两个单组件基线
python3 run.py --cc hpcc --seed 3 ...
python3 run.py --cc guard-active-only --seed 3 ...
```

`lambda * 0.95` 不截断到 1：HPCC 的归一化拥塞量还包含归一化队列项，
不是单纯的物理链路利用率。`config.log` 会记录 `EFFECTIVE_U_TARGET`；HPCC
及其他基线始终使用未缩放的 0.95。

参数说明：

| 参数             | 含义                                        |
| ---------------- | ------------------------------------------- |
| `--cc`           | 拥塞控制算法（见上表），决定 `cc_mode`      |
| `--lb`           | 负载均衡：`fecmp/drill/conga/letflow/conweave` |
| `--pfc / --irn`  | 丢包恢复机制（恰好二选一）                  |
| `--simul_time`   | 正式仿真至少 0.01s；`--smoke` 诊断可为 0.005s |
| `--analysis_warmup` | 正式 FCT 统计排除的 warm-up，默认 0.005s |
| `--max_flows`    | 启动 ns-3 前允许的最大 flow 数，默认 150000 |
| `--netload`      | 网卡负载百分比（25 表示 25%）                |
| `--topo`         | 拓扑名（见 `config/leaf_spine_*` 等）         |
| `--bw`           | 网卡带宽（Gbps，默认 100）                   |
| `--cdf`          | 流大小 CDF：默认 `AliStorage2019`，可选 `WebSearch` 等 |
| `--seed`         | 同时设置流量发生器和 ns-3 RNG；也进入流量文件名，默认 1 |
| `--monitor_profile` | `bulk` 只保留核心输出和队列摘要；`full` 额外输出详细时序 |
| `--qlen_monitoring_interval` | `full` 时队列时序及所有模式队列摘要的采样间隔（ns） |
| `--guard_beta`   | OFLM EWMA 的历史样本权重，范围 [0,1]，默认 0.125 |
| `--guard_gamma`  | OFLM 主动释放阈值倍数，非负，默认 1.0 |
| `--guard_lambda` | 完整 GUARD 的 HPCC target 倍数，至少 1，默认 1.0 |
| `--guard_selective_registration` | 1=仅大于 1 BDP 的流在首包注册；0=所有流在首包注册，默认 1 |
| `--guard_proactive_release` | 1=按剩余字节阈值提前释放已注册流；0=仅在完整接收时释放，默认 1 |
| `--guard_oflm` | 兼容旧脚本；显式 0 无条件关闭上述两项，显式 1 不覆盖单独给出的新开关 |
| `--guard_keep_last_hop_int` | last-hop INT 消融；0=默认删除，1=保留 |
| `--guard_lifecycle_trace` | 有界 OFLM 逐流生命周期 CSV；默认 0，仅支持 GUARD 模式 |
| `--guard_lifecycle_output` | 可选 CSV 路径；未给出时写入本次 run 目录 |
| `--guard_lifecycle_max_lines` | 最多接纳及输出的生命周期记录，默认 1024，硬上限 10000 |

每次仿真创建 `mix/output/<10位ID>/`，里面包含：

- `<id>_in.txt`：原始流输入
- `<id>_out_fct.txt` / `<id>_out_fct_summary.txt`：FCT 结果（slowdown 和绝对值的 P50/P95/P99/P99.9）
- `<id>_out_qlen.txt`：交换机出端口队列长度时序（仅 `full` 模式）
- `<id>_out_queue_stats.txt`：有界时间窗内所有交换机出端口队列的样本数、平均值、P95、P99 和最大值
- `<id>_out_bw.txt`：节点级吞吐采样（每 100µs）
- `<id>_flow_bw.txt`：每条流的吞吐采样
- `<id>_out_pfc.txt`：PFC 触发记录
- `<id>_out_guard_stats.txt`：逐 host 和总计的 rate-grant 发送/接收数、完整
  GUARD HPCC feedback 调用数、含有效 INT hop 的反馈数、实际应用速率更新数，以及 receiver 注册数、选择性注册数、主动释放数、
  完成释放数和最大活跃流数，可用于验证双环执行和组件消融。关闭
  `--guard_selective_registration` 时 `selected_registrations` 应为 0；关闭
  `--guard_proactive_release` 时 `proactive_releases` 应为 0。单包流由
  `FLOW_START_AND_END` 同时触发注册和完成释放，乱序尾包则以连续接收进度达到
  `flow_size` 为完成判据
- `<id>_out_guard_lifecycle.csv`：仅在 `--guard_lifecycle_trace 1` 时创建。每个被接纳
  的注册流至多一行，包含 flow ID、大小、接收节点、首包/注册/释放/完成时间、
  释放原因、释放时剩余字节和注册/释放前后的活跃流数。`-1` 表示仿真结束前尚未
  发生相应事件。记录接纳数和文件数据行数受同一个 `max_lines` 限制；即使配置了
  更多流，也不会增长超过命令行上限
- `config.txt` / `config.log`：本次仿真的输入配置和 stdout 输出

`out_pfc` 每行是 `time_ns node_id node_type interface event`，其中 event 1/0
分别表示该设备**收到** pause/resume。当前 trace 不含 priority/qIndex，也不直接
累计 pause 时长；因此它可以统计事件数、设备/端口覆盖率和事件时间线，不能仅凭
该文件精确报告 per-priority pause duration。若论文需要后者，须扩展 trace 参数。

---

## 5. 内置拓扑

`config/` 下提供了多种 leaf-spine 拓扑（`leaf_spine_<N>_100G_OS<K>` 表示 N 个 host，oversubscription K:1）：

```
leaf_spine_8_100G_OS1     leaf_spine_8_100G_OS2     leaf_spine_8_100G_OS4
leaf_spine_12_100G_OS4
leaf_spine_16_100G_OS1    leaf_spine_16_100G_OS4
leaf_spine_64_100G_OS1
leaf_spine_128_100G_OS1   leaf_spine_128_100G_OS2
fat_k8_100G_OS2           # 3-tier
```

`netload` 必须能被 oversub 整除。

---

## 6. 流量发生器

`traffic_gen/traffic_gen.py` 在原版 Poisson 流之上加了可选的 incast 模式：

```bash
python3 traffic_gen/traffic_gen.py \
        -c traffic_gen/AliStorage2019.txt \
        -n 16 -l 0.25 -b 100G -t 0.01 \
        -i \                  # 启用 incast：仿真时间 20% 处 60→1 同时打 500KB
        -o config/L_25_..._flow.txt
```

`run.py` 会按 `(load, cdf, n_host, time, bw, seed)` 自动构造文件名，已存在则跳过生成。

审稿实验还提供四种确定性的有界流量：`incast`、带背景流的 `hybrid`、
`ring-allreduce` 和 `all-to-all`。生成器默认使用 16 hosts、20 ms 时间窗，硬上限为
25,000 flows；它会回读校验 flow 数、端点、起始时间和总字节数，并在流量文件旁写
一份带 SHA-256 的 JSON manifest。例如：

```bash
python3 experiments/generate_workload.py \
  --workload hybrid --seed 3 \
  --output config/reviewer_hybrid_seed3.txt

python3 run.py --cc guard --pfc 1 --irn 0 \
  --topo leaf_spine_16_100G_OS4 --netload 40 \
  --simul_time 0.02 --max_flows 25000 \
  --flow_file config/reviewer_hybrid_seed3.txt
```

`oflm-churn` 是机制优先的高 churn 实验。八条跨 ToR、发往同一 receiver 的
16 MiB elephant 在统计 warmup 内同步启动；warmup 后以固定 25 us 间隔注入
8 轮流，每轮各含两条 103,999、104,000、104,001 和 208,000 B 的流。预设层级与
机制阈值在 `experiments/oflm_churn_ladder.json`，必须按顺序选择首个满足全部阈值的
层级，不能检查 FCT/queue 后再挑配置。默认 tier1 的生成命令为：

```bash
python3 experiments/generate_workload.py \
  --workload oflm-churn --oflm-churn-rounds 8 \
  --oflm-churn-interval-us 25 --seed 1 \
  --output config/reviewer_oflm_churn_tier1_seed1.txt
```

四组合先用 `analyze_oflm_churn.py` 检查完成率、注册数、release lead、active-set
area、grant、drop/recovery 和产物上限，再由 `select_oflm_churn_tier.py` 决定是否
解封性能。只有各 seed 选择相同层级且 flow SHA 相同时，才能用
`aggregate_oflm_churn.py` 计算配对置信区间。

OFLM 参数敏感性在选定 tier1 后使用独立的固定六格，不得增删参数点：
`gamma=1` 时 `beta={0,0.125,0.5,0.875}`，以及 `beta=0.125` 时
`gamma={0.5,1,2}`；`beta=0.125,gamma=1` 只运行一次并作为配对基线。正式
trace 必须用 `--priority-group 4 --oflm-churn-jitter-us 5` 分别生成 seeds 1–5，
同一 seed 的六格复用同一份 flow snapshot，不同 seed 的 SHA-256 必须不同。每格使用
`--guard_lifecycle_trace 1 --guard_lifecycle_max_lines 100` 和 bulk monitor，controller
trace 关闭。先仅运行 seed1 六格；只有 72/72 完成、40/40 注册均 proactive、无
drop/recovery、lifecycle 未截断，并且 beta 与 gamma 两条轴都改变 release lead、
remaining bytes 与 active-set area 后，才能无选择地扩展 seeds 2–5。

每格先用 `analyze_oflm_churn.py` 生成严格校验的 JSON，再将六格（或正式 30 格）交给：

```bash
python3 experiments/aggregate_oflm_sensitivity.py \
  --summary b0-g1:1=/path/to/summary.json \
  --summary b0p125-g1:1=/path/to/summary.json \
  --summary b0p5-g1:1=/path/to/summary.json \
  --summary b0p875-g1:1=/path/to/summary.json \
  --summary b0p125-g0p5:1=/path/to/summary.json \
  --summary b0p125-g2:1=/path/to/summary.json \
  --json-out /path/to/oflm_sensitivity.json \
  --csv-out /path/to/oflm_sensitivity.csv
```

正式 5-seed 输入按相同格式追加 seeds 2–5。结果同时保留每 seed 数值、Student-t
95% CI，以及相对基线的同 seed 配对绝对差和百分比差；百分比正值表示该指标高于
基线，不能据性能结果删除或更换参数格。

通用 CDF 的三臂正式对比由
`experiments/campaigns/general_workloads_formal.json` 和
`run_general_workloads.py` 管理。它先在不启动 ns-3 的情况下冻结五个 PG3 flow
snapshot，再用 seed 1 做机制准入；只有通过准入的 workload 才能扩展五个 seed 并由
`summarize_general_workloads.py` 计算按流大小分组的配对 t95 结果。

`--flow_file` 完全绕过 Poisson/CDF 随机生成。`run.py` 先检查首行声明的 flow 数，
再将输入复制到本次 `mix/output/<ID>/`；配置和模拟器只使用这份只读快照，因此原文件
随后变化也不会影响已启动的运行。`--simul_time` 应与 manifest 的
`run_hint.simul_time_s` 一致。生成器拒绝短于 10 ms 或超过 25,000 flows 的输入；
可用下列命令运行轻量自测：

```bash
python3 -m unittest tests/test_generate_workload.py
```

---

## 7. 验证（leaf_spine_8_100G_OS1, simul_time=0.01s, netload=25%）

> **历史结果，禁止作为当前双环 GUARD 的论文数据。** 最早一批数字生成时，
> `cc_mode=11` 的 ACK 路径没有调用 `HandleAckHp`；随后截至提交 `8336f6e` 的
> 重跑虽然进入了该函数，但交换机仍只为 `cc_mode=3` 写 INT hop，因而 mode 11
> 收到的 `nhop=0`，HPCC 速率仍未实际更新。这两个缺陷均已修复。下面表格仅保留
> 为历史记录；在 `hpcc_valid_feedback` 与 `hpcc_rate_updates_applied` 都非零之前，
> 任何 GUARD 运行都不得被视为完整双环证据。

| 模式            | `--pfc/--irn` | <1BDP 平均/p99   | >1BDP 平均/p99   |
| --------------- | ------------- | ---------------- | ---------------- |
| hpcc            | 1 / 0         | 1.135 / 2.28     | 1.892 / 5.42     |
| guard (v1)      | 1 / 0         | 1.131 / 2.22     | 1.817 / **3.68** |
| **guard (v2)**  | 1 / 0         | **1.101 / 1.81** | 1.822 / 3.76     |
| **homa-inspired（历史）** | 1 / 0 | **1.120 / 1.88** | 2.176 / 10.18 |

数字是 FCT slowdown（实际 FCT / 理想 FCT）。

### guard v1 → v2 对比（同样 leaf_spine_8_100G_OS1）

| 负载 | 类别 | v1 平均/p99 | v2 平均/p99 | v2 收益 |
| --- | --- | --- | --- | --- |
| 25% | <1BDP | 1.131 / 2.221 | 1.101 / 1.805 | p99 -19% ✓ |
| 25% | >1BDP | 1.817 / 3.676 | 1.822 / 3.757 | 持平（噪声） |
| 50% | <1BDP | 1.372 / 3.373 | 1.265 / 2.965 | p99 -12% ✓ |
| 50% | >1BDP | 3.429 / 8.567 | 3.453 / 8.681 | 持平（噪声） |

短消息 p99 显著改善（来自 §1.2 改进 #6 的 8 priority queue 路由），长消息基本不变（guard 已有的等分配额 + Proactive Release 仍然主导长流）。

- 历史 guard 数字以及提交 `8336f6e` 产生的 reviewer campaign 不能用于声明完整双环 GUARD 相对 HPCC 的收益
- homa-inspired 数字来自尚未通过协议一致性审计的实验原型；由于上述 ACK/NACK、overcommit 和 RESEND 差异，它们不得解释为标准 Homa 的性能，也不得进入正式论文基线

---

## 8. 致谢与许可

本仓库基于：

- [ConWeave (NUS, SIGCOMM'23)](https://github.com/conweave-project/conweave-ns3)
- [HPCC (Alibaba, SIGCOMM'19)](https://github.com/alibaba-edu/High-Precision-Congestion-Control)
- [TLT-RDMA (KAIST INA)](https://github.com/kaist-ina/ns3-tlt-rdma-public)

许可：MIT License。
