DEEPSTREAM_DIR ?= /opt/nvidia/deepstream/deepstream
CUDA_DIR ?= /usr/local/cuda
TENSORRT_INCLUDE_DIR ?= /usr/include/aarch64-linux-gnu
INSTALL_DIR ?= $(CURDIR)/../../.local/lib

CXX := g++
TARGET := libnvdsinfer_yolov11s.so
SOURCE := nvdsinfer_yolov11s.cpp
TEST_TARGET := test_yolov11s_parser
TEST_SOURCE := tests/nvdsinfer_yolov11s_test.cpp

CXXFLAGS := -std=c++17 -Wall -Werror -O2 -fPIC \
  -I$(DEEPSTREAM_DIR)/sources/includes \
  -I$(CUDA_DIR)/include \
  -I$(TENSORRT_INCLUDE_DIR)
LDFLAGS := -shared -Wl,-no-undefined \
  -L$(DEEPSTREAM_DIR)/lib -lnvds_infer \
  -Wl,-rpath,$(DEEPSTREAM_DIR)/lib

.PHONY: all install test clean

all: $(TARGET)

$(TARGET): $(SOURCE)
	$(CXX) $(CXXFLAGS) -o $@ $< $(LDFLAGS)

install: $(TARGET)
	mkdir -p $(INSTALL_DIR)
	cp -f $(TARGET) $(INSTALL_DIR)/$(TARGET)

$(TEST_TARGET): $(SOURCE) $(TEST_SOURCE)
	$(CXX) $(CXXFLAGS) -o $@ $(SOURCE) $(TEST_SOURCE)

test: $(TEST_TARGET)
	./$(TEST_TARGET)

clean:
	rm -f $(TARGET) $(TEST_TARGET)
