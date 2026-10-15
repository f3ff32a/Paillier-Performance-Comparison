CXX := g++
BUILD_DIR ?= build
CPPFLAGS += -Isrc/cpp -Isrc/cpp/app $(EXTRA_CPPFLAGS)
CXXFLAGS := -std=c++17 -O3 -Wall -Wextra -Wno-sign-compare -Wno-unused-variable -Wno-unused-parameter
CXXFLAGS += $(EXTRA_CXXFLAGS)
LDLIBS := -lntl -lgmp -pthread
LDFLAGS += $(EXTRA_LDFLAGS)
ifeq ($(NATIVE),1)
CXXFLAGS += -march=native
endif

# Compile on the target computer. This baseline never enables OpenMP or CUDA.
SOURCES := $(shell find src/cpp/app -name '*.cpp' -type f)
OBJECTS := $(patsubst %.cpp,$(BUILD_DIR)/%.o,$(SOURCES))
TARGET := $(BUILD_DIR)/serial_benchmark

.PHONY: all clean check smoke build-info FORCE
all: $(TARGET) build-info

$(BUILD_DIR)/.build-config: FORCE
	@python3 scripts/build_config.py --compiler "$(CXX)" --flags "$(CPPFLAGS) $(CXXFLAGS) $(LDFLAGS) $(LDLIBS)" --out "$@"

$(OBJECTS) $(BUILD_DIR)/tools/serial_benchmark.o $(BUILD_DIR)/tests/serial_core_check.o: $(BUILD_DIR)/.build-config

$(BUILD_DIR)/%.o: %.cpp
	@mkdir -p $(dir $@)
	$(CXX) $(CPPFLAGS) $(CXXFLAGS) -MMD -MP -c $< -o $@

$(TARGET): $(OBJECTS) $(BUILD_DIR)/tools/serial_benchmark.o
	$(CXX) $(LDFLAGS) $^ $(LDLIBS) -o $@
	chmod +x $@

$(BUILD_DIR)/serial_core_check: $(OBJECTS) $(BUILD_DIR)/tests/serial_core_check.o
	$(CXX) $(LDFLAGS) $^ $(LDLIBS) -o $@
	chmod +x $@

build-info: $(TARGET)
	@python3 scripts/build_info.py --compiler "$(CXX)" --flags "$(CPPFLAGS) $(CXXFLAGS) $(LDFLAGS) $(LDLIBS)" --binary "$(TARGET)" --out "$(BUILD_DIR)/build-info.json"

check: all $(BUILD_DIR)/serial_core_check
	$(BUILD_DIR)/serial_core_check
	python3 scripts/test_experiment_scripts.py
	python3 tests/test_cli.py --binary "$(TARGET)"

smoke: all
	python3 scripts/run_experiments.py --suite smoke --repeat 1

clean:
	rm -rf -- $(BUILD_DIR)

-include $(OBJECTS:.o=.d) $(BUILD_DIR)/tools/serial_benchmark.d $(BUILD_DIR)/tests/serial_core_check.d
