BUILD_DIR := build
DEEPSTREAM_DIR ?= /opt/nvidia/deepstream/deepstream
CUDA_DIR       ?= /usr/local/cuda
INSTALL_DIR    ?= $(DEEPSTREAM_DIR)/lib/gst-plugins

.PHONY: all clean install

all:
	@mkdir -p $(BUILD_DIR)
	cd $(BUILD_DIR) && cmake .. \
		-DDEEPSTREAM_DIR=$(DEEPSTREAM_DIR) \
		-DCUDA_DIR=$(CUDA_DIR) \
		-DGST_PLUGIN_INSTALL_DIR=$(INSTALL_DIR) \
		-DCMAKE_BUILD_TYPE=Release
	cmake --build $(BUILD_DIR) -j$$(nproc)

install: all
	cmake --install $(BUILD_DIR)

clean:
	rm -rf $(BUILD_DIR)
