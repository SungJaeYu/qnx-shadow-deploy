FROM ubuntu:24.04

# The proprietary SDK and licenses are mounted at runtime, never copied here.
RUN apt-get update && apt-get install -y --no-install-recommends \
    bash build-essential ca-certificates cmake file ninja-build python3 openssh-client \
    libglib2.0-0 libncurses6 libtinfo6 libxml2 zlib1g \
    && rm -rf /var/lib/apt/lists/*

ENV LANG=C.UTF-8
WORKDIR /work
CMD ["/bin/bash"]
