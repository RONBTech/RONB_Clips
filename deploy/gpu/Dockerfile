# The speech-to-text machine: the same code, built for a graphics card (NVIDIA, CUDA 12).
# Build from the project root:  docker build -f deploy/gpu/Dockerfile -t ronbclips-asr .
# Pinned to a digest; the NVIDIA wheels below are pinned in the pip commands as well.
FROM python:3.11.18-slim-bookworm@sha256:a83ba18aef0f865b1a4a611c2cf460fe446f2795673bbb9fe43be570439e3b39

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ca-certificates=20250419~deb12u2 \
      bzip2=1.0.8-5+b1 \
      libasound2=1.2.8-1+b1 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt constraints.txt ./
# everything the app needs, with the graphics-card build of the speech library and the NVIDIA libraries it loads
RUN python -m pip install --upgrade "pip==26.3" \
 && grep -v '^sherpa-onnx' requirements.txt > r.txt \
 && python -m pip install --no-cache-dir -c constraints.txt -r r.txt \
 && python -m pip install --no-cache-dir --no-deps "sherpa-onnx==2.0.1+cuda12.cudnn9.onnxruntime1.28.2" -f https://k2-fsa.github.io/sherpa/onnx/cuda.html \
 && python -m pip install --no-cache-dir --no-deps nvidia-cublas-cu12==12.9.2.10 nvidia-cudnn-cu12==9.28.1.4 nvidia-cufft-cu12==11.4.1.4 nvidia-curand-cu12==10.3.10.19 \
      nvidia-cuda-runtime-cu12==12.9.79 nvidia-cuda-nvrtc-cu12==12.9.86 nvidia-nvjitlink-cu12==12.9.86
COPY studio studio

ENV N=/usr/local/lib/python3.11/site-packages/nvidia
ENV LD_LIBRARY_PATH=$N/cublas/lib:$N/cudnn/lib:$N/cufft/lib:$N/curand/lib:$N/cuda_runtime/lib:$N/cuda_nvrtc/lib:$N/nvjitlink/lib \
    RONBCLIPS_DATA=/data \
    RONBCLIPS_ASR_PROVIDER=cuda \
    RONBCLIPS_ASR_MODEL=sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-fp16 \
    PYTHONUNBUFFERED=1
VOLUME /data
EXPOSE 9000
HEALTHCHECK --interval=30s --timeout=5s --start-period=600s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:9000/healthz', timeout=4)"
# the first start downloads the speech model into /data/models (about 1.1 GB)
CMD ["sh", "-c", "python -m studio.fetch_model && python -m studio.asr_worker"]
