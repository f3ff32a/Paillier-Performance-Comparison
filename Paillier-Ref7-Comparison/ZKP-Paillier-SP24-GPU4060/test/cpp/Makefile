# Compatibility entry point. The root CMake project owns CPU and CUDA builds.
ROOT_DIR ?= ../..
BUILD ?= build
CUDA ?= OFF
JOBS ?= 4

.PHONY: all library compile_program compile_test run_test clean
all library compile_program compile_test:
	$(MAKE) -C $(ROOT_DIR) BUILD=$(BUILD) CUDA=$(CUDA) JOBS=$(JOBS) all
run_test:
	ctest --test-dir $(ROOT_DIR)/$(BUILD) --output-on-failure
clean:
	$(MAKE) -C $(ROOT_DIR) BUILD=$(BUILD) clean
