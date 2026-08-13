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
| **`homa`** | **12**    | Homa 标准版：SRPT + overcommit + per-packet 优先级 + RESEND 自恢复 |
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
3. **发送端处理**：发送端把 grant 速率写进 `qp->hp.m_grantRate`，最终发送速率取 `min(HPCC 算出的速率, m_grantRate)`，由新增的 `SyncHwRate()` 统一下发。也就是说 grant 只起**封顶**作用，"用满剩余容量"这件事仍由 HPCC 那一层负责。
4. **主动配额释放（Proactive Release）**：接收端用 EWMA（`β=0.125`）实时估算每条流的瞬时接收速率，当 *剩余字节* < `est_rate × baseRTT × γ`（`γ=1.0`）时，认为这条流的剩余流量已经全部在飞行中，主动把它从集合里移除并广播新一轮 grant。
5. **INT hop 截断**：guard 模式下，接收端在回 ACK 前 *删掉最后一跳的 INT 信息*——因为最后一跳（接收端 NIC）的拥塞已经由 RCC 直接接管了，HPCC 不需要再为这一跳算速率。

组件消融使用明确的名字：`--cc hpcc` 是 reactive-only，`--cc guard-active-only`
是 receiver-rate-only，`--cc guard` 才是两环同时运行的完整 GUARD。不要把
`guard-active-only` 称为 Homa 或 ACC。

控制包用一个新的 IPv4 协议号 `0xFB`（与 ACK=0xFC、NACK=0xFD、CNP=0xFF 并列），交换机按最高优先级转发。

**v2 借鉴 homa 的两点改进**（`guard-borrow-homa` 分支）：

6. **短流走高优先级队列（按 m_size 分桶）**：v1 所有数据包都用 traffic_gen 给的同一个 pg（=3），导致短流和长流挤在 switch 的同一个 priority queue 里。v2 在 `AddQueuePair` 里按 m_size 分四档：`< BDP/4 → pg 1`、`< BDP/2 → pg 2`、`< BDP → pg 3`、`≥ BDP → pg 4`，整条流统一用这个 pg。短流路由到更高优先级队列、不再被长流堵。HPCC 的 INT 反馈、PFC pause 检查、RCC grant 路由全部仍然一致（每条流自己的 pg 是稳定的，跟 homa 那种 per-packet 变化不一样）。
7. **RCC 触发阈值从硬编码 BDP 改成 baseRtt-derived**：v1 在 `ReceiveUdp` 里写死 `bdp = 104000`（按 leaf_spine 100G + 8.32µs RTT 算出来的），换拓扑会误判。v2 用 `FlowStatTag::GetBaseRttSeconds()` × 接收 NIC 线速算每条流自己的 BDP，跨拓扑正确（在 leaf_spine_8 上数值不变，行为不变；在 fat_k8 / 不同 RTT 拓扑下避免错把中等流注册到 RCC 等分集合）。

### 1.3 Homa（`cc_mode=12`，按 SIGCOMM'18 论文 + PlatformLab packet format 复刻）

经典的接收端驱动调度（[SIGCOMM'18 Homa](https://dl.acm.org/doi/10.1145/3230543.3230564)），具备**8 priority queue 路由 + 自带 RESEND 恢复**，不依赖 PFC 也能跑：

1. **HomaHeader（每个数据包都携带）**：64 字节，含 `type`（DATA/GRANT/RESEND/BUSY/NEED_ACK/ACK/UNKNOWN）+ 该 type 用得到的字段联合（DATA 段：`msg_total_length / pkt_offset / pkt_length / unscheduled_bytes / priority`；GRANT 段：`granted_offset / grant_priority`；RESEND 段：`resend_offset / resend_length / restart_priority`）。
2. **HomaScheduler（接收端，per-NIC）**：SRPT 二叉堆按 `bytes_remaining_to_grant` 排序；定时器每 `pacing_interval`（=MTU/线速）pop 出 top-N（`overcommit_degree`）流，给每个发一个 GRANT 推进 1 MTU。GRANT 包用 `0xFA` 协议号。
3. **Per-packet priority 路由**：发送端 `udp.pg` 按下面规则填，交换机直接路由到对应 8 priority 队列里：
   - **Unscheduled cutoffs（短消息优先）**：`m_size < BDP/4 → pg=1`；`< BDP/2 → pg=2`；`< BDP → pg=3`；`≥ BDP → pg=4`
   - **Scheduled grant slots（让位给 unscheduled）**：top SRPT 流 → `pg=4`，往下到 `pg=7`
4. **PFC-free 数据队列**：SwitchMmu 加了 `m_PFCenabledPg[qCnt]` per-PG 开关，`scratch/network-load-balance.cc` 在 cc_mode=12 时把 PG 1–7（数据队列）的 PFC 关掉，只保留 PG 0（控制包）的 PFC。
5. **接收端 RESEND（恢复机制）**：HomaFlow 跟踪 `next_expected_offset`（连续接收的最高边界）；每 `stall_rto`（默认 15 µs ≈ 1.5×baseRTT）扫描 flow_hash，发现"已授权但未到达且无进展"的洞，发 `RESEND{offset, length}`。发送端把 RESEND range 推进 `qp->homa.m_retransmit_queue`，`GetNxtPacketHoma` 优先抢占发出去（不推进 `snd_nxt`）。
6. **QP-key 一致性**：cc_mode=12 强制 `qp->m_pg=0`（sender QP key），`rxQp.pg=0`，控制包用 `qbbh.pg=0`，ACK 也用 `pg=0`——避免 per-packet 变化的 `udp.pg` 把一条流拆成多个 QP 条目。

**用法**：

```bash
# 推荐：PFC 在 NIC 层开（控制包用），数据 PG 由 MMU 关掉，RESEND 自己恢复
python3 run.py --cc homa --pfc 1 --irn 0 --simul_time 0.01 --netload 25 --topo leaf_spine_8_100G_OS1
# 也可以叠加 IRN 作 belt-and-suspenders
python3 run.py --cc homa --pfc 1 --irn 1 ...
```

> **当前 PR1–PR5 落地范围**：functional happy-path + RESEND 恢复完整。`overcommit_degree` 暂定 1（实验显示 ≥2 会被 RESEND 一 MTU/15 µs 的恢复速度拖崩）；NEED_ACK / BUSY / UNKNOWN 暂未实现；out-of-order 段没逐字节追踪（RESEND 可能重传已收到的乱序包，无害）。详见 `docs/homa-plan.md`。

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
| `--simul_time`   | 仿真时长（秒），≥ 0.005                      |
| `--netload`      | 网卡负载百分比（25 表示 25%）                |
| `--topo`         | 拓扑名（见 `config/leaf_spine_*` 等）         |
| `--bw`           | 网卡带宽（Gbps，默认 100）                   |
| `--cdf`          | 流大小 CDF：默认 `AliStorage2019`，可选 `WebSearch` 等 |
| `--seed`         | 同时设置流量发生器和 ns-3 RNG；也进入流量文件名，默认 1 |
| `--guard_beta`   | OFLM EWMA 的历史样本权重，范围 [0,1]，默认 0.125 |
| `--guard_gamma`  | OFLM 主动释放阈值倍数，非负，默认 1.0 |
| `--guard_lambda` | 完整 GUARD 的 HPCC target 倍数，至少 1，默认 1.0 |
| `--guard_keep_last_hop_int` | last-hop INT 消融；0=默认删除，1=保留 |

每次仿真创建 `mix/output/<10位ID>/`，里面包含：

- `<id>_in.txt`：原始流输入
- `<id>_out_fct.txt` / `<id>_out_fct_summary.txt`：FCT 结果（slowdown 和绝对值的 P50/P95/P99/P99.9）
- `<id>_out_qlen.txt`：交换机出端口队列长度采样（每 1µs，guard 默认开）
- `<id>_out_bw.txt`：节点级吞吐采样（每 100µs）
- `<id>_flow_bw.txt`：每条流的吞吐采样
- `<id>_out_pfc.txt`：PFC 触发记录
- `<id>_out_guard_stats.txt`：逐 host 和总计的 rate-grant 发送/接收数、完整
  GUARD HPCC feedback 更新数、receiver 最大活跃流数，可用于验证双环执行和
  分析 grant 放大
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

---

## 7. 验证（leaf_spine_8_100G_OS1, simul_time=0.01s, netload=25%）

> **历史结果，禁止作为当前双环 GUARD 的论文数据。** 这些数字生成时，
> `cc_mode=11` 的 ACK 路径没有调用 `HandleAckHp`，实际只运行了 receiver rate
> cap。该缺陷现已修复，下面表格仅保留为历史记录；所有 GUARD 对比、图表和结论
> 必须用当前代码、多随机种子和置信区间重新运行。

| 模式            | `--pfc/--irn` | <1BDP 平均/p99   | >1BDP 平均/p99   |
| --------------- | ------------- | ---------------- | ---------------- |
| hpcc            | 1 / 0         | 1.135 / 2.28     | 1.892 / 5.42     |
| guard (v1)      | 1 / 0         | 1.131 / 2.22     | 1.817 / **3.68** |
| **guard (v2)**  | 1 / 0         | **1.101 / 1.81** | 1.822 / 3.76     |
| **homa**   | 1 / 0         | **1.120 / 1.88** | 2.176 / 10.18    |

数字是 FCT slowdown（实际 FCT / 理想 FCT）。

### guard v1 → v2 对比（同样 leaf_spine_8_100G_OS1）

| 负载 | 类别 | v1 平均/p99 | v2 平均/p99 | v2 收益 |
| --- | --- | --- | --- | --- |
| 25% | <1BDP | 1.131 / 2.221 | 1.101 / 1.805 | p99 -19% ✓ |
| 25% | >1BDP | 1.817 / 3.676 | 1.822 / 3.757 | 持平（噪声） |
| 50% | <1BDP | 1.372 / 3.373 | 1.265 / 2.965 | p99 -12% ✓ |
| 50% | >1BDP | 3.429 / 8.567 | 3.453 / 8.681 | 持平（噪声） |

短消息 p99 显著改善（来自 §1.2 改进 #6 的 8 priority queue 路由），长消息基本不变（guard 已有的等分配额 + Proactive Release 仍然主导长流）。

- 历史 guard 数字不能用于声明完整双环 GUARD 相对 HPCC 的收益
- homa 通过 8 priority queue 真正路由 + unscheduled cutoffs，把短消息 p99 压到 1.88（vs vanilla HPCC 的 2.28）；长消息 p99 暂时受限于保守的 overcommit_degree=1 + RESEND 一 MTU/15µs 的恢复节奏，调参留给后续

---

## 8. 致谢与许可

本仓库基于：

- [ConWeave (NUS, SIGCOMM'23)](https://github.com/conweave-project/conweave-ns3)
- [HPCC (Alibaba, SIGCOMM'19)](https://github.com/alibaba-edu/High-Precision-Congestion-Control)
- [TLT-RDMA (KAIST INA)](https://github.com/kaist-ina/ns3-tlt-rdma-public)

许可：MIT License。
