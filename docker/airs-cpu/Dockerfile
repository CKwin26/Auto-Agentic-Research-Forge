FROM python:3.12-slim@sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de

LABEL org.opencontainers.image.title="Research Forge AIRS CPU environment" \
      org.opencontainers.image.version="v1" \
      org.research-forge.controlled="true" \
      org.research-forge.environment="rf-airs-cpu" \
      org.research-forge.environment-version="v1" \
      org.research-forge.capability-probe="/opt/research-forge/verify_runtime.py"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONHASHSEED=0 \
    OMP_NUM_THREADS=1 \
    OPENBLAS_NUM_THREADS=1 \
    MKL_NUM_THREADS=1 \
    NUMEXPR_NUM_THREADS=1 \
    VECLIB_MAXIMUM_THREADS=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /opt/research-forge
COPY requirements.lock ./requirements.lock
RUN python -m pip install \
      --no-cache-dir \
      --only-binary=:all: \
      --require-hashes \
      --requirement requirements.lock

COPY capabilities.json ./capabilities.json
COPY verify_runtime.py ./verify_runtime.py
RUN python ./verify_runtime.py --json

# Experiments cannot install new packages even if this image is ever started
# outside the normal Research Forge --network none runtime.
ENV PIP_NO_INDEX=1 \
    PIP_REQUIRE_VIRTUALENV=1

USER 65534:65534
WORKDIR /workspace
CMD ["python3", "/opt/research-forge/verify_runtime.py", "--json"]
