# Build/run environment for 32-bit Linux pawncc (matches CI: -m32, Ninja).
FROM ubuntu:22.04
ENV DEBIAN_FRONTEND=noninteractive
RUN dpkg --add-architecture i386 \
 && apt-get update \
 && apt-get install -y --no-install-recommends \
      gcc-multilib g++-multilib cmake ninja-build python3 time file \
      libc6:i386 libstdc++6:i386 \
 && rm -rf /var/lib/apt/lists/*
