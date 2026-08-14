# Homa 实现 Plan（cc_mode=12）

> 历史实现计划。当前实现状态、资格结果和仍保留的语义边界以
> [`../README.md`](../README.md) 与
> [`homa-qualification.md`](homa-qualification.md) 为准；下文的 PR 编号和
> “待实现”描述仅用于追溯设计过程。

**目标**：在 guard 仓库（NS-3 19）main 分支上完整复刻一个标准版 Homa，对照
[SIGCOMM'18 paper](https://dl.acm.org/doi/10.1145/3230543.3230564) +
[PlatformLab/Homa](https://github.com/PlatformLab/Homa) 的 packet format。

简易版 (cc_mode=10) 已经改名为 `homa-simple`（commit `7224304`）。本 plan
描述完整版 (cc_mode=12) 的增量实现路径——拆 6 个 PR，每个 PR 单独可编译可
跑，覆盖一个清晰的语义切片。

---

## 0. 蓝本要点（Recap）

来自论文 + DPDK 实现，本实现需要复刻的关键机制：

| 机制 | 论文位置 | 关键参数 |
|------|----------|----------|
| **Unscheduled bytes** | §3.4, §5.2 | RTTbytes（≈ 1 BDP）一次性免 grant 发出 |
| **Receiver-driven GRANT** | §3.4, §5.3 | 接收端 SRPT 序按消息剩余字节排序 |
| **Overcommitment** | §3.5 | 同时给 top-N sender 发 GRANT，N ≈ degree（论文用 ≤ 8） |
| **8 priority queues (DSCP)** | §3.6, §5.4 | 低 priority 给 unscheduled，高 priority 给 scheduled，按消息长度分桶 |
| **Per-message scheduling** | §3.3 | 不是 per-flow / per-connection |
| **PFC-free + drop-tolerant** | §6.2 | 不依赖无损网络，靠 RESEND 恢复 |
| **RESEND / BUSY / NEED_ACK / ACK** | §3.7 | 包类型完整 |

---

## 1. 仓库现状 vs Homa 需求

### 1.1 已经具备的基础设施

| 组件 | 现状 | Homa 用法 |
|------|------|-----------|
| Switch 8 priority queues | `qCnt = 8`，存在于 `switch-node.cc` / `switch-mmu.cc` / `qbb-net-device.cc` | 直接用，按 `ch.udp.pg` 选 qIndex |
| Per-port-per-queue PFC | `m_paused[qCnt]` per-priority pause/resume | 需要 per-PG 启用/禁用 |
| Per-queue admission | `CheckIngressAdmission(qIndex,...)` | 直接用 |
| ECN marking | per-queue `ShouldSendCN(qIndex)` | 不需要（Homa 不用 ECN） |
| IRN (lossy NIC retransmit) | DCQCN 用，有 RTO low/high + BDP limit + SACK manager | **可借鉴而不复用**——Homa 有自己的 RESEND 语义 |
| QP = single-message | 一个 `RdmaQueuePair` 当作一个消息（m_size 字节，单向） | 1 QP ≡ 1 message，不需要 per-message 重构 |
| ACK / NACK protocol | `0xFC` ACK / `0xFD` NACK / `0xFE` PFC / `0xFF` CNP / `0xFB` guard rate / homa-simple credit | 给 Homa 控制包用 `0xFA`（保留号） |

### 1.2 必须新增 / 修改

| 项 | 类型 | 工作量 |
|----|------|--------|
| `homa-header.{h,cc}` 多 type Header | 新增 | 中 |
| `cc_mode == 12` dispatch | rdma-hw.{h,cc} 改 | 中 |
| `IntHeader::mode = 3` for Homa | int-header.h 改 | 极小 |
| Receiver overcommit + SRPT 调度 | rdma-hw 加新类 `HomaScheduler` | 大 |
| Sender state machine + 重传 | rdma-hw 加新类 `HomaSender` | 大 |
| Per-PG PFC toggle | switch-mmu 扩 | 小 |
| Priority cutoff 配置 | settings + run.py | 小 |
| traffic_gen.py 支持 cc_mode=12 | 改 | 极小 |

预估 **新增 / 改动 1500–2500 行 C++**，分布约 8–10 个文件。

---

## 2. Mapping: Homa 概念 ↔ 仓库概念

| Homa 概念 | 本仓 |
|-----------|------|
| RPC message | `RdmaQueuePair`（一个 QP 一条单向 m_size 字节消息） |
| message ID | `qp->m_flow_id`（已有） |
| RTTbytes | `bdp_bytes = baseRtt * bps / 8 / 1e9`（rdma-hw.cc:243 已有） |
| sender host | 持 `m_qpMap[key]` 的节点 |
| receiver host | 持 `m_rxQpMap[key]` 的节点 |
| 8 priority queues | switch 的 qIndex 0–7，packet 的 `ch.udp.pg` |
| GRANT packet | 用 `0xFA` 作为 IPv4 protocol number（待用） |
| BUSY / RESEND / ACK | 复用 `0xFA`，靠 HomaHeader 的 `type` 字段区分 |
| timeout | 借鉴 IRN 的 RTO 机制，自实现 RESEND 计时器 |

---

## 3. 设计决策（已拍板）

| 决策点 | 选项 | 选定 |
|--------|------|------|
| 蓝本 | paper / DPDK / Kernel | **SIGCOMM'18 paper + PlatformLab DPDK packet format** |
| cc_mode 编号 | 10/12/13 | `CC_MODE_HOMA = 12` |
| 简易版定位 | 删 / 保留 / 重命名 | 保留并重命名为 `CC_MODE_HOMA_SIMPLE = 10`（已完成） |
| PFC 处理 | per-PG / 全局关 / hacky bypass | **per-PG 开关**（扩 SwitchMmu 加 `m_PFCenabledPg[qCnt]`） |
| switch 多优先级 | 现有 / 重写 | **直接用** `qCnt = 8` 现成基础设施 |
| 协议号 | 单 0xFA + type 字段 / 多协议号 | **单 0xFA**，Homa 所有控制 / 数据包都过同一协议号，靠 `HomaHeader::type` 区分 |
| 重传机制 | 复用 IRN / 自写 | **自写 RESEND**（Homa 有自己的语义：基于 grant offset，不是 SACK） |

## 4. 待定 / 需要先实验决定

| 问题 | 默认 | 备注 |
|------|------|------|
| Unscheduled priority 分桶 | 按 message size 分 4 档 → pg=4..7 | 论文里 "RPCthreshold cutoffs" 是动态的；先做静态版 |
| Scheduled priority 分配 | top-N sender 各拿 pg=0..N-1 | N=overcommit_degree，初值 8 |
| Overcommit degree N | 8 | 论文 §3.5 默认值 |
| RESEND 触发阈值 | 1.5 × baseRtt | 论文 §3.7 |
| BUSY 阈值 | grant 已发但 sender 没回包，1 RTT 后 | 论文 §3.7 |
| Sender pacer | 不加，让 NIC line rate 自己跑 | Homa 不显式 pacing |

---

## 5. PR 拆分

每个 PR **可独立编译、跑通至少烟测**。建议按顺序合，但 PR2 / PR3 内部可以分 commit。

### PR 1 — 骨架：HomaHeader + cc_mode=12 dispatch（占位）

**目标**：cc_mode=12 走得通 dispatch（即使行为暂时简化为透传），所有
后续 PR 在这之上加业务逻辑。

**改动**：
- `src/point-to-point/model/homa-header.{h,cc}`（新）
  - `HomaHeader::Type` 枚举：`DATA = 0`, `GRANT = 1`, `RESEND = 2`, `BUSY = 3`, `NEED_ACK = 4`, `ACK = 5`, `UNKNOWN = 6`
  - 字段：`type / message_id / msg_total_length / pkt_offset / pkt_length / priority / granted_offset`
  - Serialize / Deserialize（按 PlatformLab DPDK 字段顺序）
- `src/point-to-point/model/rdma-queue-pair.h`：
  - 加 `CC_MODE_HOMA = 12,`
  - 加 `struct {...} homa;` per-QP 状态（暂留空字段）
- `src/network/utils/int-header.h`：
  - 注释更新 `// 0=INT, 1=TS, 2=Homa-Simple, 3=Homa-Full, 5=none`
- `scratch/network-load-balance.cc`：
  - 加 `else if (cc_mode == 12) IntHeader::mode = 3;`
- `src/point-to-point/model/rdma-hw.{h,cc}`：
  - cc_mode == 12 在 `Setup()` / `AddQueuePair()` / `Receive()` / `ReceiveUdp()` 加占位分支（先打 NS_LOG，行为退化为 cc_mode=10 simple）
- `src/network/utils/custom-header.{h,cc}`：
  - 让 0x11 UDP 解析在 `IntHeader::mode == 3` 时识别 `HomaHeader`
  - 让 0xFA 协议号被 deserializer 接受
- `src/point-to-point/model/switch-node.cc`：
  - `0xFA` 包归类为 control，qIndex=0
- `src/point-to-point/wscript`：注册新文件
- `run.py`：`cc_modes["homa"] = 12`
- `traffic_gen/traffic_gen.py`：识别 cc_mode=12（同 cc_mode=10 处理即可）

**验收**：`--cc homa` 烟测能跑完，输出 FCT 文件（行为先暂时退化）。

**预计行数**：+400 / -10。

---

### PR 2 — Receiver: SRPT + Overcommit GRANT 调度

**目标**：接收端实现 Homa 风格的 receiver-driven scheduling——同时给
top-N sender 发 GRANT，按 SRPT 排序，priority 跨多个 queue 分布。

**改动**：
- `rdma-hw.h`：新类 `HomaScheduler`
  - 类似 `HomaSimpleScheduler` 的二叉堆，但排序键改为 `(message_remaining_bytes ASC)`，不再按 pg
  - 维护 `active_msgs`（堆）+ `granted_msgs`（最多 N 条，正在 grant 中）
  - 每 RTT 重新评估：从 active 中 pop top-N 进入 granted；其余等下次
- `ScheduleHoma()`：每收到一个 DATA → 看是不是触发新 grant；定时器 1 RTT 重新评估 overcommit
- `SendGrant()`：发 type=GRANT 的包，priority 字段 = 该 sender 在 N 个 granted 中的"slot"（slot 0 → pg 0，slot N-1 → pg N-1）
- Sender 收到 GRANT：更新 `homa.granted_offset` + `granted_priority`
- 基本 unscheduled 处理：sender 在 RTTbytes 之内可以自由发，priority 用静态 cutoff 表
- `m_overcommit_degree` 配置项，默认 8

**不在本 PR**：丢包恢复、BUSY、NEED_ACK / ACK。本 PR 假设网络无丢包
（PFC 还没关），先把 happy-path 跑通。

**验收**：`--cc homa --pfc 1`（暂时还在 PFC 上，因为还没做 loss
recovery）烟测：
- FCT slowdown 数字应该和 simple 同数量级
- log 看到 GRANT 在多 priority slot 上发出
- queue 监测看到不同优先级被 sender 用到

**预计行数**：+800 / -50。

---

### PR 3 — Sender: 状态机 + Unscheduled priority 分桶

**目标**：sender 端按 Homa 规则发包：先发 RTTbytes unscheduled，再按
GRANT 发 scheduled；priority 字段按规则填。

**改动**：
- `qp->homa`：
  - `bytes_unscheduled`（≤ RTTbytes）
  - `bytes_granted`（receiver 已授权的字节数）
  - `bytes_sent`
  - `current_priority`（unscheduled 段固定，scheduled 段跟 GRANT）
  - `last_progress_time`（用于 PR5 的 RESEND 检测）
- `GetNxtPacketHoma()`：
  - 如果 bytes_sent < bytes_unscheduled：unscheduled，priority = `unscheduled_cutoff_table[m_size]`
  - 否则：scheduled，priority = qp->homa.granted_priority；前提 bytes_sent < bytes_granted（否则停摆）
  - 写 HomaHeader{type=DATA, ...}
  - 写 `udp.pg = priority`
- 静态 unscheduled cutoff 表（4 档默认）：
  ```
  m_size <  RTTbytes/4   → pg = 7（最高，给最短消息）
  m_size <  RTTbytes/2   → pg = 6
  m_size <  RTTbytes     → pg = 5
  m_size >= RTTbytes     → pg = 4
  ```
  （这块挪到 settings 配置项里）
- 移除 cc_mode==12 在 PR1 中的"退化为 simple"分支，正式接入

**验收**：
- 烟测看 fct slowdown，应该比 simple 略好（或至少不差）——尤其是混合
  长短消息的负载
- 拆 mix/output 的 qlen mon，确认不同 priority queue 都被使用

**预计行数**：+400 / -100。

---

### PR 4 — Switch: per-PG PFC toggle + cc_mode=12 默认 PFC-free

**目标**：让 cc_mode=12 流量跑在 lossy 网络上（drop 而非 pause）。

**改动**：
- `src/point-to-point/model/switch-mmu.h`：
  - 加 `bool m_PFCenabledPg[qCnt];`（默认全 = m_PFCenabled）
  - 加 setter `SetPFCEnabledForPg(uint32_t pg, bool enabled)`
- `switch-mmu.cc`：`GetPauseClasses` / `CheckAndSendPfc` 跳过禁用的 PG
- `qbb-net-device.cc`：发 PFC 时同样 per-PG check
- `scratch/network-load-balance.cc`：cc_mode=12 时遍历 switches，把
  Homa 用到的 8 个 PG（0–7）全部 disable PFC（其余 cc_mode 不影响）
- `run.py`：cc_mode=12 默认 `--pfc 1`（系统层启 PFC，但 Homa PG 在 MMU
  层禁用），保持其他算法的 PFC 行为不变
- 注意：尚没有 RESEND，丢包就丢了——FCT summary 可能不完整，本 PR 不
  追求功能正确性，只追求"能跑、能 drop"

**验收**：
- 烟测：低负载（netload=25）下应该没明显丢包，FCT summary 仍然合理
- 高负载（netload=70）下：开始观测到 drop（`out_pfc.txt` 应为空，因为
  Homa PG 不 pause；但 `config.log` 里能看到 drop count）

**预计行数**：+150 / -20。

---

### PR 5 — Loss recovery: RESEND / NEED_ACK / ACK / BUSY

**目标**：sender / receiver 都能在丢包后恢复，让 cc_mode=12 在 lossy
网络下功能正确。

**改动**：
- Receiver 端 (`HomaReceiver`)：
  - 每个 active rx_msg 跟踪 `next_expected_offset` + `out_of_order_set`（已收到的乱序段）
  - 检测到空洞：1.5 × baseRtt 后给 sender 发 `RESEND{from, to}`
  - 收到 RESEND 请求时：转给 sender 处理（实现在 sender 那边）
  - 收完整条消息：发 `ACK{message_id}`
- Sender 端：
  - 收到 RESEND：从 retransmit buffer / 重新 enqueue 指定 range
  - 收到 ACK：标记消息完成，触发 QpComplete
  - 持有"未 ACK 的飞行字节"，如 1.5 × baseRtt 内 receiver 没回 ACK 也没 GRANT：发 `NEED_ACK{message_id}`
- BUSY：receiver 已发 GRANT 但久未见 DATA → 发 `BUSY` 提示 sender 仍活着但被其他流抢占了，此时 sender 应该让出 priority
  - （可选先实现，论文 §3.7 提到主要为了应对 sender 端拥塞）
- Sender 端 retransmit buffer：可以借鉴现有 `irn.m_sack` / `IrnSackManager` 的模式，但是 Homa 的 RESEND 是 byte-range 而不是 SACK
- 配置 `m_homa_resend_rto = 1.5 * baseRtt`

**验收**：
- 高负载（netload=70）+ 模拟 1% drop 烟测：
  - FCT summary 完整（所有 flow 都完成）
  - 长尾 99.9% slowdown 在合理范围（< 10×）
- 注入受控丢包（写个 PerFlowDrop 测试）确认 RESEND 路径正常

**预计行数**：+800 / -30。

---

### PR 6 — 调优 + 对照 benchmark（可选 / 慢慢做）

**目标**：把 cc_mode=12 跟 cc_mode=10 (simple) / cc_mode=3 (hpcc) /
cc_mode=11 (guard) 在相同负载下对比，复现论文里的趋势。

**改动**：
- 调 unscheduled cutoff 表 / overcommit degree
- 跑 leaf_spine_8 / leaf_spine_16 / leaf_spine_128 三个拓扑下的对照
- 写 `docs/homa-results.md` 记录数字
- 必要时回头调 PR2/PR3 的参数

**预计行数**：~0 代码改动（除了配置项）；文档为主。

---

## 6. 共享数据结构 / settings 项

新加配置项（在 `Settings` / `Configure*Param`）：

| 名 | 类型 | 默认 | 用处 |
|----|------|------|------|
| `m_homa_overcommit_degree` | uint32 | 8 | PR2 |
| `m_homa_unscheduled_cutoffs` | uint64[4] | RTT/4, RTT/2, RTT, ∞ | PR3 |
| `m_homa_resend_rto` | Time | 1.5 × baseRtt | PR5 |
| `m_homa_busy_rto` | Time | 1.0 × baseRtt | PR5 |
| `m_PFCenabledPg[qCnt]` | bool[8] | derived | PR4 |

---

## 7. 测试策略

### 7.1 烟测（每个 PR 必跑）

```bash
# 简易版回归（不能破）
python3 run.py --cc homa --lb fecmp --pfc 1 --simul_time 0.01 --netload 25 --topo leaf_spine_8_100G_OS1

# Homa
python3 run.py --cc homa --lb fecmp --pfc 1 --simul_time 0.01 --netload 25 --topo leaf_spine_8_100G_OS1
```

### 7.2 中等负载（PR3+）

```bash
python3 run.py --cc homa --lb fecmp --pfc 1 --simul_time 0.05 --netload 50 --topo leaf_spine_8_100G_OS1
```

### 7.3 PFC-free + lossy（PR5+）

```bash
python3 run.py --cc homa --lb fecmp --pfc 1 --simul_time 0.1 --netload 70 --topo leaf_spine_16_100G_OS1
```

每个 PR 跑完都清 `mix/output/*`。

---

## 8. 风险 / 已知坑

| 风险 | 影响 | 缓解 |
|------|------|------|
| NS-3 19 老版本 + waf 1.7.11 + Py 2.7.18 build chain 脆弱 | 可能某次 PR 触碰宏 / 模块依赖时编译失败 | 每个 PR 都先 `python waf` 确认 build 通过再跑测 |
| HomaHeader 字段顺序跟 PlatformLab 实测包不一致 | 跨实现兼容性差 | 仅在仿真内部使用，不需要跟真实 wire 兼容；按 paper 字段顺序即可 |
| Receiver overcommit + 8 priority 在小拓扑（leaf_spine_8）跑不出区分度 | benchmark 看不到 Homa 的优势 | PR6 在 leaf_spine_128 / fat_k8 上跑，且 mix 长短消息（用 `--cdf AliStorage2019`） |
| Per-PG PFC 改动影响其他 cc_mode | DCQCN/HPCC/Guard 回归失败 | PR4 默认所有 PG PFC = 全局 m_PFCenabled，仅 cc_mode=12 时显式禁用 |
| RESEND 实现复杂度爆炸 | PR5 拖延 | 先做最简版（receiver 检测空洞 → RESEND；sender 维护连续 buffer 原样回放）；优化交给 PR6 |
| `RdmaQueuePair` 单消息假设和 Homa 多消息不符 | 改不动 NS-3 抽象 | 接受这个简化——本仓 1 QP = 1 message 已经合理近似论文场景 |

---

## 9. 不做的事（明确范围）

- **不重构 RdmaQueuePair → Message 抽象层**：1 QP = 1 message 在仿真里足够
- **不做 NIC pacer**：论文里 Homa 也不显式 pacing，靠 line-rate
- **不做 priority cutoff 自动调优**：用静态表，自动调参留给 PR6 / 后续工作
- **不实现 NEED_ACK 链路探活之外的复杂 BUSY 逻辑**：先做 RESEND，BUSY 当锦上添花
- **不兼容 IRN**：`--irn 1 --cc homa` 直接报错；Homa 自己管丢包

---

## 10. 时间线估计

| 阶段 | 工作 | 估时（实打实写代码 + 调试） |
|------|------|------|
| PR1 | 骨架 | 0.5 天 |
| PR2 | Receiver | 1.5 天 |
| PR3 | Sender + cutoff | 1 天 |
| PR4 | per-PG PFC | 0.5 天 |
| PR5 | Loss recovery | 2 天 |
| PR6 | benchmark | 1 天 |
| **合计** | | **~6.5 工作日** |

每个 PR 跑通后停下来 review 再下一个；遇到块就回头调上一 PR。
