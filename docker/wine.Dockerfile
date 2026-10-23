FROM ubuntu:24.04 AS compiler
RUN apt-get update && apt-get install -y --no-install-recommends gcc-mingw-w64-x86-64 g++-mingw-w64-x86-64 && rm -rf /var/lib/apt/lists/*
COPY docker/console-input.c docker/db-probe.c docker/dbwin-monitor.c /src/
RUN x86_64-w64-mingw32-gcc /src/console-input.c -o /console-input.exe -municode -static -lkernel32 -luser32 && \
    x86_64-w64-mingw32-gcc /src/db-probe.c -o /db-probe.exe -static -lole32 -loleaut32 -luuid -luser32 && \
    x86_64-w64-mingw32-gcc /src/dbwin-monitor.c -o /dbwin-monitor.exe -static -lkernel32

FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive DISPLAY=:99 WINEPREFIX=/var/lib/rh-wine WINEDEBUG=-all
RUN dpkg --add-architecture i386 && apt-get update && \
    apt-get install -y --no-install-recommends ca-certificates curl && \
    mkdir -p /etc/apt/keyrings && \
    curl -fsSL https://dl.winehq.org/wine-builds/winehq.key -o /etc/apt/keyrings/winehq-archive.key && \
    curl -fsSL https://dl.winehq.org/wine-builds/ubuntu/dists/noble/winehq-noble.sources -o /etc/apt/sources.list.d/winehq-noble.sources && \
    apt-get update && apt-get install -y --install-recommends winehq-stable xvfb xauth python3 procps cabextract && \
    rm -rf /var/lib/apt/lists/*
COPY --from=compiler /console-input.exe /db-probe.exe /opt/rh/
COPY docker/wine-entrypoint.sh docker/configure-server.py /opt/rh/
COPY docker/sqloledb.reg /opt/rh/
RUN chmod +x /opt/rh/wine-entrypoint.sh
WORKDIR /srv/game
ENTRYPOINT ["/opt/rh/wine-entrypoint.sh"]
