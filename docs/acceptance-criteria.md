# GUARD 带宽利用率验收指标

## 1. 验收目标

使用 NS-3 仿真器验证 GUARD 拥塞控制算法在 128 台服务器规模下，能够使服务器
NIC 到 ToR 交换机的 100 Gbps 接入链路达到以下指标：

> 固定稳态统计窗口内，每条参与测试的 NIC→ToR 链路平均带宽利用率大于 95%。

本指标统计 NS-3 链路发送器的实际忙碌时间；数据包、ACK 和 GUARD 控制包均会占用
发送器。应用 payload goodput 作为辅助指标单独报告，不用于代替链路利用率。

## 2. 指标定义

对主机 `i`，固定统计窗口 `[T0, T1]` 内的链路利用率定义为：

```text
U_i = link_busy_time_i / (T1 - T0)
reported_Gbps_i = U_i × 100 Gbps
```

仿真使用 `monitor_profile=full`，由主机设备的 `PhyTxBegin/PhyTxEnd` 和
`PhyRxBegin/PhyRxEnd` trace 对实际序列化区间积分，并在 100 us 采样边界切分仍在传输
的报文。此定义不会把一个跨越边界的整包错误地全部计入单侧采样桶。输出文件为：

```text
mix/output/<RUN_ID>/<RUN_ID>_out_bw.txt
```

文件列依次为：

```text
time_ns  node_id  tx_Gbps  rx_Gbps
```

本次采用相对于 2 秒 flow-generation 起点的固定窗口：

```text
T0 = 5 ms
T1 = 15 ms
窗口长度 = 10 ms
每条链路采样数 = 100
```

不得根据流的实际完成时间动态裁剪窗口，以免产生选择偏差。

## 3. 验收条件

正式验收必须同时满足：

1. 使用 128 台服务器，且所有服务器均参与发送和接收；
2. 每个 seed 中，128 条 NIC→ToR 链路的 10 ms 平均利用率均大于 95%；
3. seed 1–5 全部通过，不允许验收后删除或替换 seed；
4. 每条链路在每个 seed 中必须具有完整的 100 个采样；
5. 正式窗口内每台服务器必须始终存在 active QP；
6. 所有生成的流必须完成；
7. switch drop、恢复重传和 timeout 必须为 0；
8. 保存输入流量快照、`config.txt`、原始带宽输出和汇总结果。

除逐链路硬门槛外，还应报告最小值、P5、全网平均值及五个 seed 平均值的 95%
置信区间。不能只用全网平均值代替逐链路检查。

## 4. 仿真拓扑

使用：

```text
config/leaf_spine_128_100G_OS1.txt
```

该拓扑包含：

- 128 台服务器；
- 8 个 ToR，每个 ToR 连接 16 台服务器；
- 16 个 Spine；
- 服务器接入链路速率为 100 Gbps；
- fabric 为 1:1 无超售配置。

主验收不使用 `leaf_spine_128_100G_OS2`。OS2 为 2:1 超售拓扑，在全部服务器同时
发送跨 ToR 流量时，fabric 容量可能使接入链路无法同时达到 95%，不适合作为该硬
指标的判定环境。

## 5. 验收负载

负载类型为 `access-saturation`，所有流均位于对应服务器所在的 ToR 内，以隔离并
验证 NIC 接入链路能力。

- 每台服务器发起 4 条长流；
- 每台服务器同时接收 4 条长流；
- 每条流大小为 64 MiB；
- priority group 为 4；
- 每个 seed 共 512 条流；
- 每个 seed 的总 payload 为 32 GiB；
- 流起始时间具有不超过 10 us 的确定性随机抖动；
- 每台服务器的 4 条流共同形成持续 backlog。

对每个 ToR 内的主机，端点映射为：

```text
rack_base = (src // 16) × 16
local_src = src % 16
dst       = rack_base + ((local_src + k) % 16), k ∈ {1, 2, 3, 4}
```

该映射保证每台服务器恰好有 4 条发送流和 4 条接收流，并使 GUARD 在每个接收端
维护 4 个活跃流的配额。

## 6. 复现命令

生成单个 seed 的流量：

```bash
python3 experiments/generate_workload.py \
  --workload access-saturation \
  --output /tmp/guard-util-128/access-s1.flow \
  --manifest /tmp/guard-util-128/access-s1.json \
  --hosts 128 --duration-ms 30 \
  --priority-group 4 --max-flows 1000 \
  --seed 1 --flow-bytes 67108864 \
  --access-hosts-per-tor 16 \
  --access-fanout 4 \
  --access-jitter-us 10
```

运行仿真：

```bash
python3 run.py \
  --cc guard --lb fecmp \
  --pfc 1 --irn 0 \
  --topo leaf_spine_128_100G_OS1 \
  --bw 100 --simul_time 0.03 \
  --monitor_profile full \
  --qlen_monitoring_interval 100000 \
  --sw_monitoring_interval 100000 \
  --max_flows 1000 \
  --flow_file /tmp/guard-util-128/access-s1.flow \
  --seed 1
```

对 seed 2–5 使用相同参数重新生成流量并运行，不改变拓扑、流大小、fanout、统计窗口
或 GUARD 配置。

单次运行的逐主机统计命令如下，其中 `RUN_ID` 替换为实际结果目录：

```bash
awk '
$2 < 128 && $1 > 5000000 && $1 <= 15000000 {
    sum[$2] += $3
    samples[$2]++
}
END {
    for (host = 0; host < 128; host++) {
        printf "%d %.9f %d\n", host, sum[host] / samples[host], samples[host]
    }
}' mix/output/RUN_ID/RUN_ID_out_bw.txt
```

## 7. 实测结果

仿真日期：2026-09-03。

| Seed | Run ID | 最低 TX 平均值 | TX P5 | 全网 TX 平均值 | 最高 TX 平均值 | 达标链路 |
|---:|---:|---:|---:|---:|---:|---:|
| 1 | 589895220 | 99.8932 Gbps | 99.9090 Gbps | 99.9528 Gbps | 100.0000 Gbps | 128/128 |
| 2 | 817702662 | 99.8994 Gbps | 99.9158 Gbps | 99.9555 Gbps | 100.0000 Gbps | 128/128 |
| 3 | 921003172 | 99.8878 Gbps | 99.9081 Gbps | 99.9549 Gbps | 100.0000 Gbps | 128/128 |
| 4 | 426314840 | 99.9045 Gbps | 99.9099 Gbps | 99.9539 Gbps | 100.0000 Gbps | 128/128 |
| 5 | 972524273 | 99.8918 Gbps | 99.9051 Gbps | 99.9505 Gbps | 100.0000 Gbps | 128/128 |

跨五个 seed 的汇总结果：

- 全部运行中的最低逐链路利用率：99.8878%；
- 五个 seed 的全网平均利用率：99.9535%；
- 五个 seed 平均值的 95% t 置信区间：`[99.9511%, 99.9560%]`；
- 64,000 个 100 us 采样中低于 95% 的数量：0；
- TX 和 RX 超过 100 Gbps 的采样数量均为 0；
- 五轮 RX 最低逐链路平均值范围：99.9160–99.9283 Gbps；
- 2560/2560 条流完成；
- switch drop：0；
- PFC pause：0；
- retransmission/timeout：0；
- 正式窗口内 active-QP 检查：128/128 台主机通过全部五个 seed。

原始结果：

- [Seed 1 bandwidth trace](../mix/output/589895220/589895220_out_bw.txt)
- [Seed 2 bandwidth trace](../mix/output/817702662/817702662_out_bw.txt)
- [Seed 3 bandwidth trace](../mix/output/921003172/921003172_out_bw.txt)
- [Seed 4 bandwidth trace](../mix/output/426314840/426314840_out_bw.txt)
- [Seed 5 bandwidth trace](../mix/output/972524273/972524273_out_bw.txt)

对应的完整 workload 汇总：

- [Seed 1 summary](../mix/output/589895220/workload_summary.csv)
- [Seed 2 summary](../mix/output/817702662/workload_summary.csv)
- [Seed 3 summary](../mix/output/921003172/workload_summary.csv)
- [Seed 4 summary](../mix/output/426314840/workload_summary.csv)
- [Seed 5 summary](../mix/output/972524273/workload_summary.csv)

## 8. NS-3 时间分辨率修正

修正前，典型 1090B 数据包在 100 Gbps 下的理论序列化时间为 87.2ns，但旧实现把
每个包独立向下截断成 87ns，使持续流的模型速率变为 100.229885 Gbps。60B 控制包的
4.8ns 序列化时间也会被截断成 4ns。修正包含两部分：

1. point-to-point/Qbb 发送器使用整数运算保存小数 tick 余量，使 1090B 报文按
   88/87ns 序列累计到精确的 87.2ns 平均值，并保证任意累计报文前缀不超发；
2. 带宽监控对链路忙碌区间进行积分，在采样边界切分跨界报文，不再按 `PhyTxEnd`
   到达时间把整个包计入单个采样桶。

新增的序列化单元测试验证：5 个 1090B 报文总耗时为 436ns，5 个 60B 控制包总耗时
为 24ns。完整 `devices-point-to-point` 测试套件通过。修正后的五轮 128 节点实测中，
TX/RX 最大采样值和最大逐链路平均值均为 100.0000 Gbps，因此不再进行事后归一化或
截值。

## 9. Payload goodput 边界

本次运行的端到端应用 payload goodput 平均约为每台主机 88.92–89.23 Gbps。该值
低于链路忙时等效速率，主要包含数据/INT 头部、GUARD 控制流量以及流启动和收尾
时间的影响。

当前配置中，每 1000B payload 对应典型的约 1090B 模型数据包，仅考虑数据包头后的
payload 比例约为 91.74%。因此，如果项目验收条件实际是“应用有效 payload 吞吐超过
95 Gbps”，当前包大小和头部配置无法通过，必须单独修改 MTU/包头设计或调整指标定义。

## 10. 验收结论与适用范围

在上述冻结配置、128 台服务器、五个独立 seed 和固定 10ms 稳态窗口下：

> GUARD 的 NIC→ToR 链路忙时带宽利用率验收通过，所有被测链路均高于 95%。

该结论仅证明 128-host 环境中的接入链路饱和能力。本负载为机架内均衡流量，不证明
跨 ToR fabric、2:1 超售拓扑或应用 payload goodput 能够达到同样的 95% 指标；这些场景
应作为独立验收项目测试和报告。

## 11. 1024 节点扩展验证

仿真日期：2026-09-07。

1024 节点验证保持第 2、3、5 节的统计口径、硬门槛和负载不变。使用
`config/leaf_spine_1024_100G_OS1.txt`：

- 1024 台服务器；
- 64 个 ToR，每个 ToR 连接 16 台服务器；
- 16 个 Spine，每个 ToR 分别通过一条 100 Gbps 链路连接每个 Spine；
- 每个 ToR 的服务器侧容量与 fabric 侧容量均为 1.6 Tbps，订阅比为 1:1；
- 1104 个总节点、80 个交换机、2048 条链路。

拓扑生成命令：

```bash
python3 config/leaf_spine_topology_gen.py \
  --hosts 1024 --hosts-per-tor 16 --spines 16 \
  --rate-gbps 100 --delay-ns 1000 \
  --output config/leaf_spine_1024_100G_OS1.txt
```

每个 seed 生成 4096 条 64 MiB 长流，每台服务器仍恰好有 4 条发送流和 4 条接收流，
总 payload 为 256 GiB。除节点数和 `max_flows` 外，运行参数与 128 节点验收相同：

```bash
python3 experiments/generate_workload.py \
  --workload access-saturation \
  --output /tmp/guard-util-1024/access-s1.flow \
  --manifest /tmp/guard-util-1024/access-s1.json \
  --hosts 1024 --duration-ms 30 \
  --priority-group 4 --max-flows 5000 \
  --seed 1 --flow-bytes 67108864 \
  --access-hosts-per-tor 16 --access-fanout 4 \
  --access-jitter-us 10

python3 run.py \
  --cc guard --lb fecmp --pfc 1 --irn 0 \
  --topo leaf_spine_1024_100G_OS1 \
  --bw 100 --simul_time 0.03 \
  --monitor_profile full \
  --qlen_monitoring_interval 100000 \
  --sw_monitoring_interval 100000 \
  --max_flows 5000 \
  --flow_file /tmp/guard-util-1024/access-s1.flow \
  --seed 1
```

五轮实测结果如下。每个最低值、P5、平均值和最高值均由固定 5–15 ms 窗口内每条
NIC→ToR 上行的 100 个样本先求平均，再在 1024 条链路间汇总。

| Seed | Run ID | 最低 TX 平均值 | TX P5 | 全网 TX 平均值 | 最高 TX 平均值 | 达标链路 |
|---:|---:|---:|---:|---:|---:|---:|
| 1 | 696671277 | 99.872860 Gbps | 99.908370 Gbps | 99.953136 Gbps | 100.000000 Gbps | 1024/1024 |
| 2 | 372823244 | 99.872550 Gbps | 99.908020 Gbps | 99.952174 Gbps | 100.000000 Gbps | 1024/1024 |
| 3 | 502891325 | 99.887860 Gbps | 99.909560 Gbps | 99.953266 Gbps | 100.000000 Gbps | 1024/1024 |
| 4 | 403406717 | 99.881690 Gbps | 99.910410 Gbps | 99.951866 Gbps | 100.000000 Gbps | 1024/1024 |
| 5 | 585487356 | 99.873340 Gbps | 99.908680 Gbps | 99.952520 Gbps | 100.000000 Gbps | 1024/1024 |

跨五个 seed 的汇总结果：

- 全部运行中的最低逐链路利用率：99.872550%；
- 五个 seed 的全网平均利用率：99.952593%；
- 五个 seed 平均值的 95% t 置信区间：`[99.951844%, 99.953342%]`；
- 512,000 个 100 us 正式样本中低于 95 Gbps 的数量：0；
- TX 和 RX 超过 100 Gbps 的样本数量均为 0；
- 五轮 RX 最低逐链路平均值范围：99.914540–99.920110 Gbps；
- 20,480/20,480 条流完成；
- switch drop、PFC pause、IRN retransmission 和 timeout recovery 均为 0；
- 正式窗口内 active-QP 检查：五轮共 5120/5120 台主机通过；
- 每轮峰值内存约 8.14 GiB，单轮墙钟时间约 65–79 分钟。

原始带宽 trace 和完整 workload 汇总保存在各 Run ID 对应的 `mix/output/<RUN_ID>/`
目录中。

因此，在相同的机架内接入链路饱和负载下，1024 节点扩展验证也满足每条
NIC→ToR 上行平均利用率大于 95% 的验收条件。本验证仍然隔离了 fabric 瓶颈，不能
解释为跨 ToR 业务或应用 payload goodput 达到 95%。
