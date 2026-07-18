FROM kalilinux/kali-rolling

# Which Kali metapackage to install. Override at build time with:
#   docker build --build-arg KALI_META=kali-linux-large -t kali-lab .
# Tiers (roughly smallest -> largest):
#   kali-linux-headless   ~ the recommended CLI toolset, no desktop GUI  (best for Docker)
#   kali-linux-default    ~ headless + the GUI apps from a normal Kali desktop
#   kali-linux-large      ~ default + a big pile of extra tools
#   kali-linux-everything ~ literally every Kali tool (tens of GB)
ARG KALI_META=kali-linux-headless

ENV DEBIAN_FRONTEND=noninteractive
ENV LANG=C.UTF-8

RUN apt-get update && \
    apt-get -y dist-upgrade && \
    apt-get -y install --no-install-recommends \
        ${KALI_META} \
        seclists \
        metasploit-framework \
        ca-certificates \
        curl wget git vim less \
        iproute2 iputils-ping net-tools dnsutils \
        tmux python3 python3-pip pipx && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /root/work
CMD ["/bin/bash"]
