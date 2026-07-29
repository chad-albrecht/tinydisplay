ARG BUILD_FROM
FROM ${BUILD_FROM}

# Which commit of tinydisplay to install. Supplied by build.yaml.
ARG TINYDISPLAY_REF=main

# libusb and libudev are what the hidapi wheel links against at runtime; git is
# needed only because the packages are installed from the repository rather
# than from PyPI, which they are not published to yet.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        python3 \
        python3-pip \
        python3-venv \
        git \
        libusb-1.0-0 \
        libudev1 \
    && rm -rf /var/lib/apt/lists/*

# A virtualenv rather than --break-system-packages: the base image's Python is
# Debian's, and this add-on has no business editing it.
RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}"

# core first and explicitly, because tinydisplay-ht32 declares it as an
# ordinary dependency and pip would otherwise look for it on PyPI, where it
# does not exist.
RUN pip install --no-cache-dir \
        "tinydisplay-core @ git+https://github.com/chad-albrecht/tinydisplay@${TINYDISPLAY_REF}#subdirectory=packages/core" \
    && pip install --no-cache-dir \
        "tinydisplay-ht32[all] @ git+https://github.com/chad-albrecht/tinydisplay@${TINYDISPLAY_REF}#subdirectory=packages/ht32"

COPY run.sh /run.sh
RUN chmod a+x /run.sh

CMD ["/run.sh"]
