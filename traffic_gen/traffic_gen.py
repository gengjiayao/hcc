# -*- coding: utf-8 -*-
import sys
import random
import math
import heapq
from optparse import OptionParser
from custom_rand import CustomRand

class Flow:
	def __init__(self, src, dst, size, t):
		self.src, self.dst, self.size, self.t = src, dst, size, t
	def __str__(self):
		return "%d %d 3 %d %.9f"%(self.src, self.dst, self.size, self.t)

def translate_bandwidth(b):
	if b == None:
		return None
	if type(b)!=str:
		return None
	if b[-1] == 'G':
		return float(b[:-1])*1e9
	if b[-1] == 'M':
		return float(b[:-1])*1e6
	if b[-1] == 'K':
		return float(b[:-1])*1e3
	return float(b)

def poisson(lam):
	return -math.log(1-random.random())*lam

if __name__ == "__main__":
	port = 80
	parser = OptionParser()
	parser.add_option("-c", "--cdf", dest = "cdf_file", help = "the file of the traffic size cdf", default = "uniform_distribution.txt")
	parser.add_option("-n", "--nhost", dest = "nhost", help = "number of hosts")
	parser.add_option("-l", "--load", dest = "load", help = "the percentage of the traffic load to the network capacity, by default 0.3", default = "0.3")
	parser.add_option("-b", "--bandwidth", dest = "bandwidth", help = "the bandwidth of host link (G/M/K), by default 10G", default = "10G")
	parser.add_option("-t", "--time", dest = "time", help = "the total run time (s), by default 10", default = "10")
	parser.add_option("-o", "--output", dest = "output", help = "the output file", default = "tmp_traffic.txt")
	parser.add_option("-i", "--incast", dest = "enable_incast", action="store_true", help = "enable incast traffic pattern", default = True)
	options,args = parser.parse_args()

	base_t = 2000000000 # 2000000000

	if not options.nhost:
		print("please use -n to enter number of hosts")
		sys.exit(0)
	nhost = int(options.nhost)
	load = float(options.load)
	bandwidth = translate_bandwidth(options.bandwidth)
	time = float(options.time)*1e9 # translates to ns
	output = options.output
	if bandwidth == None:
		print("bandwidth format incorrect")
		sys.exit(0)

	fileName = options.cdf_file
	file = open(fileName,"r")
	lines = file.readlines()
	# read the cdf, save in cdf as [[x_i, cdf_i] ...]
	cdf = []
	for line in lines:
		x,y = map(float, line.strip().split(' '))
		cdf.append([x,y])

	# create a custom random generator, which takes a cdf, and generate number according to the cdf
	customRand = CustomRand()
	if not customRand.setCdf(cdf):
		print("Error: Not valid cdf")
		sys.exit(0)

	ofile = open(output, "w")

	# ==========================================
	# Part 1: Generate Incast Traffic (Conditional)
	# ==========================================
	incast_flows = []
	
	if options.enable_incast:
		incast_size = 500 * 1024  # 500KB = 512000 Bytes
		
		# [关键修改] 动态设置 Incast 时间
		# 设置在仿真开始后的 20% 处触发。
		# 例如 time=5ms (5,000,000ns)，则 incast 在 base_t + 1ms 处触发
		incast_offset = time * 0.2
		incast_start_time = base_t + incast_offset
		
		if nhost > 1:
			incast_dst = random.randint(0, nhost - 1)
			potential_senders = list(range(nhost))
			potential_senders.remove(incast_dst)
			
			num_senders = 60
			if len(potential_senders) < num_senders:
				print("Warning: Not enough hosts for 60 senders. Using %d senders." % len(potential_senders))
				incast_senders = potential_senders
			else:
				incast_senders = random.sample(potential_senders, num_senders)
				
			for src in incast_senders:
				# 添加微小的抖动 (0-100ns)
				jitter = random.randint(0, 100) 
				incast_flows.append((incast_start_time + jitter, src, incast_dst, incast_size))
			
			incast_flows.sort(key=lambda x: x[0])
			print("Incast enabled: %d flows (Size: %d) starting at %.9fs" % (len(incast_flows), incast_size, incast_start_time * 1e-9))
		else:
			print("Warning: Not enough hosts for Incast (need at least 2). Skipping Incast.")

	# ==========================================
	# Part 2: Main Loop (Merge Logic)
	# ==========================================
	
	avg = customRand.getAvg()
	avg_inter_arrival = 1/(bandwidth*load/8./avg)*1000000000
	n_flow_estimate = int(time / avg_inter_arrival * nhost) + len(incast_flows)
	n_flow = 0
	ofile.write("%d \n"%n_flow_estimate)
	host_list = [(base_t + int(poisson(avg_inter_arrival)), i) for i in range(nhost)]
	heapq.heapify(host_list)
	incast_idx = 0
	total_incast = len(incast_flows)
	while len(host_list) > 0:
		t_poisson, src_poisson = host_list[0]
		
		# --- MIX LOGIC ---
		while incast_idx < total_incast:
			t_incast, src_inc, dst_inc, size_inc = incast_flows[incast_idx]
			
			# 如果 Incast 时间 <= 下一个 Poisson 时间，先写入 Incast
			if t_incast <= t_poisson:
				# 只有当 Incast 时间在仿真总时间范围内才写入
				if t_incast <= base_t + time: 
					ofile.write("%d %d 3 %d %.9f\n"%(src_inc, dst_inc, size_inc, t_incast * 1e-9))
					n_flow += 1
				incast_idx += 1
			else:
				break
		# -----------------

		inter_t = int(poisson(avg_inter_arrival))
		
		dst_poisson = random.randint(0, nhost-1)
		while (dst_poisson == src_poisson):
			dst_poisson = random.randint(0, nhost-1)
			
		if (t_poisson + inter_t > time + base_t):
			heapq.heappop(host_list)
		else:
			size = int(customRand.rand())
			if size <= 0:
				size = 1
			n_flow += 1
			ofile.write("%d %d 3 %d %.9f\n"%(src_poisson, dst_poisson, size, t_poisson * 1e-9))
			heapq.heapreplace(host_list, (t_poisson + inter_t, src_poisson))

	# Handle remaining incast flows (in case poisson ended early but simulation time isn't over)
	while incast_idx < total_incast:
		t_incast, src_inc, dst_inc, size_inc = incast_flows[incast_idx]
		if t_incast <= time + base_t:
			ofile.write("%d %d 3 %d %.9f\n"%(src_inc, dst_inc, size_inc, t_incast * 1e-9))
			n_flow += 1
		incast_idx += 1

	ofile.seek(0)
	ofile.write("%d"%n_flow)
	ofile.close()

'''
	f_list = []
	avg = customRand.getAvg()
	avg_inter_arrival = 1/(bandwidth*load/8./avg)*1000000000
	# print avg_inter_arrival
	for i in range(nhost):
		t = base_t
		while True:
			inter_t = int(poisson(avg_inter_arrival))
			t += inter_t
			dst = random.randint(0, nhost-1)
			while (dst == i):
				dst = random.randint(0, nhost-1)
			if (t > time + base_t):
				break
			size = int(customRand.rand())
			if size <= 0:
				size = 1
			f_list.append(Flow(i, dst, size, t * 1e-9))

	f_list.sort(key = lambda x: x.t)

	print len(f_list)
	for f in f_list:
		print f
'''
