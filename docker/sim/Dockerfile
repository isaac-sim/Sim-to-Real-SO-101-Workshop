FROM nvcr.io/nvidia/isaac-lab:2.3.2

ENV PYTHON=/workspace/isaaclab/_isaac_sim/python.sh
ENV PATH="/workspace/isaaclab/_isaac_sim/kit/python/bin:$PATH"


WORKDIR /workspace
RUN git clone https://github.com/huggingface/lerobot.git
WORKDIR /workspace/lerobot
RUN git checkout e670ac5daf9b76

# Install lerobot without deps to avoid upgrading packages that break
# Isaac Lab's symlinked vendored copies (packaging, numpy, torch, etc.)
RUN $PYTHON -m pip install --no-deps -e .

# Pin packages already in the base image to prevent upgrades that break
# Isaac Lab's internal symlinks (packaging, numpy, lxml, torch, etc.).
# torch/torchvision are pinned per-arch to what the base image ships
# (x86-64: 2.7.0/0.22.0, arm64: 2.9.0/0.24.0).
RUN if [ "$(uname -m)" = "aarch64" ]; then TORCH=2.9.0; TV=0.24.0; else TORCH=2.7.0; TV=0.22.0; fi && \
    printf '%s\n' \
    "packaging==23.0" \
    "numpy==1.26.0" \
    "lxml==4.9.4" \
    "torch==${TORCH}" \
    "torchvision==${TV}" \
    "imageio==2.37.0" \
    > /tmp/constraints.txt

# Install only the deps not already in the base image.
# torchcodec: arm64 wheels only exist from 0.11 on.
RUN if [ "$(uname -m)" = "aarch64" ]; then TORCHCODEC="torchcodec>=0.11,<0.12"; else TORCHCODEC="torchcodec>=0.2.1,<0.6.0"; fi && \
    $PYTHON -m pip install -c /tmp/constraints.txt \
    "datasets>=4.0.0,<4.2.0" \
    "diffusers>=0.27.2,<0.36.0" \
    "huggingface-hub[hf-transfer,cli]>=0.34.2,<0.36.0" \
    "accelerate>=1.10.0,<2.0.0" \
    "cmake>=3.29.0.1,<4.2.0" \
    "av>=15.0.0,<16.0.0" \
    "jsonlines>=4.0.0,<5.0.0" \
    "pynput>=1.7.7,<1.9.0" \
    "pyserial>=3.5,<4.0" \
    "wandb>=0.20.0,<0.22.0" \
    "${TORCHCODEC}" \
    "draccus==0.10.0" \
    "deepdiff>=7.0.1,<9.0.0" \
    "feetech-servo-sdk>=1.0.0,<2.0.0"

# FFmpeg: the BtbN release below is x86-64-only; use the apt package on arm64.
RUN if [ "$(uname -m)" = "aarch64" ]; then \
        apt-get update && apt-get install -y --no-install-recommends ffmpeg && \
        rm -rf /var/lib/apt/lists/*; \
    else \
        curl --proto "=https" --tlsv1.2 -sSf -L -o /tmp/ffmpeg.tar.xz \
        https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-n7.1-latest-linux64-lgpl-shared-7.1.tar.xz && \
        tar -xf /tmp/ffmpeg.tar.xz -C /usr/local --strip-components=1 && \
        ldconfig && \
        rm /tmp/ffmpeg.tar.xz; \
    fi

RUN apt-get update && apt-get install -y --no-install-recommends \
    libx11-6 libxcursor1 libxrandr2 libxi6 libxinerama1 \
    libxkbcommon0 libxkbcommon-x11-0 \
    && rm -rf /var/lib/apt/lists/*

# rerun-sdk requires numpy>=2 in its metadata but works fine with 1.26.0
RUN $PYTHON -m pip install --no-deps "rerun-sdk>=0.24.0,<0.27.0"
RUN $PYTHON -m pip install "pyzmq"

WORKDIR /workspace/Sim-to-Real-SO-101-Workshop

COPY ./docker/sim/entrypoint.sh /workspace/Sim-to-Real-SO-101-Workshop/docker/sim/entrypoint.sh
COPY ./docker/utils.sh /workspace/Sim-to-Real-SO-101-Workshop/docker/utils.sh
RUN chmod +x docker/sim/entrypoint.sh

RUN $PYTHON -m pip install --upgrade pip

RUN mkdir -p /tmp/pycache && chmod 1777 /tmp/pycache
ENV PYTHONPYCACHEPREFIX=/tmp/pycache

RUN cat docker/utils.sh >> /root/.bashrc
ENTRYPOINT ["/workspace/Sim-to-Real-SO-101-Workshop/docker/sim/entrypoint.sh"]
CMD ["bash"]

