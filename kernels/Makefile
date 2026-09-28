# CUDA Kalman filter bank (Jetson TX2, sm_62, CUDA 10.2).
#
#   make                 build kalman_tracker + verify_math, run the host math check
#   ./kalman_tracker     N sweep, AoS vs SoA vs CPU, correctness check
#   sudo make profile    nvprof: gld/gst efficiency + achieved occupancy (needs root on Jetson)
#   make replay          cross-check against python/reference_kalman.py (needs numpy)
#
# Before benchmarking on the TX2:  sudo nvpmodel -m 0 && sudo jetson_clocks
NVCC   ?= /usr/local/cuda/bin/nvcc
NVPROF ?= /usr/local/cuda/bin/nvprof
ARCH   ?= sm_62
CXX    ?= g++

NVCCFLAGS := -O3 -std=c++11 -arch=$(ARCH) -lineinfo -Xcompiler -Wall -Xptxas -v   # -v: registers/spills per kernel
CXXFLAGS  := -O2 -std=c++11 -Wall -Wextra

all: verify kalman_tracker

kalman_tracker: kalman_tracker.cu kalman_math.h
	$(NVCC) $(NVCCFLAGS) -o $@ kalman_tracker.cu

verify_math: verify_math.cpp kalman_math.h
	$(CXX) $(CXXFLAGS) -o $@ verify_math.cpp

verify: verify_math
	./verify_math

replay: verify_math
	python3 ../python/reference_kalman.py --dump replay.txt
	./verify_math --check replay.txt

profile: kalman_tracker
	$(NVPROF) --metrics gld_efficiency,gst_efficiency,achieved_occupancy ./kalman_tracker --profile

clean:
	rm -f kalman_tracker verify_math replay.txt

.PHONY: all verify replay profile clean
