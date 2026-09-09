# syntax=docker/dockerfile:1

FROM golang:1.22.12-bookworm@sha256:3d699e4d15d0f8f13c9195c0632a16702b8cbdece2955af1c23b37ae5d55a253 AS pebble-builder

ARG PEBBLE_VERSION=v2.6.0
ENV CGO_ENABLED=0 GOBIN=/out

RUN mkdir -p /out \
    && go install "github.com/letsencrypt/pebble/v2/cmd/pebble@${PEBBLE_VERSION}" \
    && go install "github.com/letsencrypt/pebble/v2/cmd/pebble-challtestsrv@${PEBBLE_VERSION}" \
    && test -x /out/pebble \
    && test -x /out/pebble-challtestsrv

FROM debian:bookworm-slim@sha256:88200866dfff7ea7f5cbcb6ec7c8a701889efe6fe859fe64d6990e4b07ea4171

ARG DEBIAN_FRONTEND=noninteractive
ARG APR_SOURCE_VERSION=1.7.2-3+deb12u1
ARG APR_UTIL_SOURCE_VERSION=1.6.3-1+deb12u1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        autoconf \
        automake \
        build-essential \
        ca-certificates \
        clang \
        curl \
        file \
        libapr1-dev \
        libaprutil1-dev \
        libbrotli-dev \
        libclang-rt-dev \
        libcurl4-openssl-dev \
        libjansson-dev \
        liblua5.3-dev \
        libnghttp2-dev \
        libpcre2-dev \
        libssl-dev \
        libtool \
        libxml2-dev \
        llvm \
        make \
        nghttp2-client \
        pkg-config \
        procps \
        python3 \
        python3-dev \
        python3-pip \
        python3-venv \
        zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

RUN python3 -m venv /opt/coverage-venv \
    && /opt/coverage-venv/bin/pip install --no-cache-dir \
        filelock==3.18.0 \
        packaging==25.0 \
        pyOpenSSL==25.1.0 \
        pytest==8.4.1 \
        python-multipart==0.0.20 \
        requests==2.32.4 \
        websockets==17.1

COPY --from=pebble-builder /out/pebble /out/pebble-challtestsrv /usr/local/bin/

WORKDIR /opt/spec2code

COPY data/benchmark/mapping.csv data/benchmark/all_func.csv data/benchmark/
COPY data/repos/httpd/rawcode/ data/repos/httpd/rawcode/

WORKDIR /opt/spec2code/data/repos/httpd/rawcode

# The dataset snapshot omits distribution documentation and stock config
# templates. They are not used by the pytest harness, but configure/install
# still require them to exist.
RUN mkdir -p \
         docs/conf/extra \
         docs/docroot \
         docs/error \
         docs/icons \
         docs/cgi-examples \
         docs/man \
         docs/manual \
    && for config in \
         httpd.conf \
         extra/httpd-autoindex.conf \
         extra/httpd-dav.conf \
         extra/httpd-default.conf \
         extra/httpd-info.conf \
         extra/httpd-languages.conf \
         extra/httpd-manual.conf \
         extra/httpd-mpm.conf \
         extra/httpd-multilang-errordoc.conf \
         extra/httpd-policy.conf \
         extra/httpd-ssl.conf \
         extra/httpd-userdir.conf \
         extra/httpd-vhosts.conf \
         extra/proxy-html.conf \
         extra/httpd-macro.conf; \
       do touch "docs/conf/${config}.in"; done \
    && cp test/pyhttpd/conf/mime.types docs/conf/mime.types \
    && touch \
         docs/conf/magic \
         docs/man/httpd.1 \
         docs/man/httpd.8 \
         docs/manual/README

# Build matching Debian APR sources with Clang in an isolated prefix. Debian's
# installed APR libtool is configured for GCC and cannot link Clang's
# profile-instrumented HTTPD shared modules.
RUN sed -i 's/^Types: deb$/Types: deb deb-src/' /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && cd /tmp \
    && apt-get source "apr=${APR_SOURCE_VERSION}" "apr-util=${APR_UTIL_SOURCE_VERSION}" \
    && mkdir -p /opt/dependencies \
    && mv /tmp/apr-1.7.2 /opt/dependencies/apr \
    && mv /tmp/apr-util-1.6.3 /opt/dependencies/apr-util \
    && rm -rf /var/lib/apt/lists/* /tmp/apr_* /tmp/apr-util_*

RUN cd /opt/dependencies/apr \
    && ./buildconf \
    && CC=clang CFLAGS="-O0 -g" ./configure \
         --prefix=/opt/apr-coverage \
         --enable-shared \
         --disable-static \
    && make -j"$(nproc)" \
    && make install \
    && cd /opt/dependencies/apr-util \
    && ./buildconf --with-apr=/opt/dependencies/apr \
    && CC=clang CFLAGS="-O0 -g" ./configure \
         --prefix=/opt/apr-coverage \
         --with-apr=/opt/apr-coverage/bin/apr-1-config \
         --enable-shared \
         --disable-static \
    && make -j"$(nproc)" \
    && make install

ENV LD_LIBRARY_PATH=/opt/apr-coverage/lib

RUN ./buildconf \
         --with-apr=/opt/dependencies/apr \
         --with-apr-util=/opt/dependencies/apr-util \
    && CC=clang \
       CFLAGS="-O0 -g -fprofile-instr-generate -fcoverage-mapping" \
       LDFLAGS="-fprofile-instr-generate" \
       ./configure \
         --prefix=/opt/httpd-coverage \
         --with-apr=/opt/apr-coverage/bin/apr-1-config \
         --with-apr-util=/opt/apr-coverage/bin/apu-1-config \
         --with-pcre=/usr/bin/pcre2-config \
         --enable-so \
         --enable-mods-shared=all \
         --enable-ssl \
         --with-ssl=/usr \
         --enable-http2 \
         --enable-proxy-http2 \
    && make -j"$(nproc)" \
    && make install \
    && make -C test/clients h2ws \
    && test -x /opt/httpd-coverage/bin/httpd \
    && test -x test/clients/h2ws \
    && test -x /usr/bin/nghttp \
    && test -x /usr/bin/h2load

# h2ws stores getopt()'s integer result in a plain char. AArch64 uses an
# unsigned plain char by default, so getopt()'s -1 sentinel is otherwise
# interpreted as another option and every invocation fails.
RUN make -C test/clients clean \
    && make -C test/clients h2ws EXTRA_CFLAGS="-fsigned-char" \
    && test -x test/clients/h2ws

# Debian Bookworm ships curl 7.88, while four HTTP/2 tests require curl 8.0
# or 8.1. Build a checksum-pinned client without replacing HTTPD's build-time
# libcurl development package.
ARG CURL_SOURCE_VERSION=8.10.1
ARG CURL_SOURCE_SHA256=d15ebab765d793e2e96db090f0e172d127859d78ca6f6391d7eafecfd894bbc0
RUN curl -fsSL \
         "https://curl.se/download/curl-${CURL_SOURCE_VERSION}.tar.gz" \
         -o /tmp/curl.tar.gz \
    && echo "${CURL_SOURCE_SHA256}  /tmp/curl.tar.gz" | sha256sum -c - \
    && tar -xzf /tmp/curl.tar.gz -C /tmp \
    && cd "/tmp/curl-${CURL_SOURCE_VERSION}" \
    && ./configure \
         --prefix=/usr/local \
         --with-openssl=/usr \
         --with-nghttp2=/usr \
         --with-zlib=/usr \
         --with-brotli=/usr \
         --without-libpsl \
         --disable-static \
    && make -j"$(nproc)" \
    && make install \
    && LD_LIBRARY_PATH=/usr/local/lib:/opt/apr-coverage/lib \
         /usr/local/bin/curl --version | grep -q 'Features:.*HTTP2' \
    && LD_LIBRARY_PATH=/usr/local/lib:/opt/apr-coverage/lib \
         /usr/bin/ldd /usr/local/bin/curl | grep -q '/usr/local/lib/libcurl.so.4' \
    && rm -rf /tmp/curl.tar.gz "/tmp/curl-${CURL_SOURCE_VERSION}"

ENV LD_LIBRARY_PATH=/usr/local/lib:/opt/apr-coverage/lib

COPY data/benchmark/dynamic_coverage/collect_dynamic_coverage.py \
     /opt/spec2code/data/benchmark/dynamic_coverage/
COPY data/benchmark/dynamic_coverage/docker/entrypoint.sh /usr/local/bin/collect-httpd-coverage
RUN chmod +x /usr/local/bin/collect-httpd-coverage \
    && mkdir -p /results

WORKDIR /opt/spec2code/data/repos/httpd/rawcode

ENTRYPOINT ["/usr/local/bin/collect-httpd-coverage"]
CMD ["test"]
