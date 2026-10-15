CMAKE ?= cmake
BUILD ?= build
CUDA ?= OFF
JOBS ?= 4
CMAKE_ARGS ?=

.PHONY: all configure test bench clean cpp_build cpp_test lib_build rtx4060 validate-gpu diagnose
all: configure
	$(CMAKE) --build $(BUILD) --parallel $(JOBS)
configure:
	$(CMAKE) -S . -B $(BUILD) -DZKP_ENABLE_CUDA=$(CUDA) $(CMAKE_ARGS)
test: all
	cd $(BUILD) && ctest --output-on-failure
bench: all
	$(BUILD)/zkp_bench --help
clean:
	@if [ -f "$(BUILD)/CMakeCache.txt" ]; then $(CMAKE) --build "$(BUILD)" --target clean; fi

cpp_build lib_build: all
cpp_test: test

# Laptop profile: separate cache, system g++, native sm_89, CUDA + CPU.
rtx4060:
	python3 scripts/build.py --backend cuda --cmake "$(CMAKE)" --jobs $(JOBS)
validate-gpu:
	python3 scripts/validate_gpu.py
diagnose:
	python3 scripts/diagnose.py
