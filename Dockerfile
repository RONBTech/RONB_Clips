# RONB Clips: one image with the web server, the clip pipeline and ffmpeg.
# Pinned to a digest so a rebuild cannot silently pick up a different base image. Update deliberately.
FROM python:3.11-slim@sha256:07d4824036af339fa1b6ef769cc99d9c10a141769538b52b3b597c84f7a7b535

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ffmpeg=7:5.1.9-0+deb12u1 ca-certificates=20230311+deb12u1 bzip2=1.0.8-5+b1 \
      fonts-noto-core=20201225-1 fonts-noto-cjk=1:20220127+repack1-1 \
 && rm -rf /var/lib/apt/lists/*
# yt-dlp needs a JavaScript runtime for YouTube
COPY --from=denoland/deno:bin@sha256:b507583d3c68ba69f3ed2ce5969ae5b8b70f43421c781279c7aea06435263543 /deno /usr/local/bin/deno

WORKDIR /app
COPY requirements.txt constraints.txt ./
RUN python -m pip install --upgrade "pip==26.3" \
 && python -m pip install --no-cache-dir -c constraints.txt -r requirements.txt
COPY studio studio
COPY web web

ENV RONBCLIPS_DATA=/data \
    RONBCLIPS_HOST=0.0.0.0 \
    RONBCLIPS_PORT=8765 \
    RONBCLIPS_TRUST_PROXY=1 \
    PYTHONUNBUFFERED=1
VOLUME /data
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/healthz', timeout=4)"
# the first start downloads the speech model into /data/models by itself (about 480 MB)
CMD ["python", "-m", "studio.server"]
