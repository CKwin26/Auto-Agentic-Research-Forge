# Linux-only evaluator runtime for the unmodified official AIRS RAD task code.
# Candidate generation stays on the host; this image receives only mounted task
# code, public/shared data, agent logs, and a protected evaluator workdir.
FROM python:3.10-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_DEFAULT_TIMEOUT=300

RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential curl git \
    && rm -rf /var/lib/apt/lists/*

# Union of the official RAD metadata requirements.  The image deliberately has
# no benchmark data and is invoked with --network none after image construction.
RUN pip install --retries 5 --timeout 300 filelock typing-extensions networkx jinja2 fsspec sympy

# Keep the large CPU-only wheel in its own layer.  A transient package-index
# timeout later in the build must not force a second PyTorch download.
RUN curl --fail --location --retry 5 --retry-all-errors --output /tmp/torch-2.6.0+cpu-cp310-cp310-linux_x86_64.whl \
      'https://download-r2.pytorch.org/whl/cpu/torch-2.6.0%2Bcpu-cp310-cp310-linux_x86_64.whl' && \
    pip install --no-deps /tmp/torch-2.6.0+cpu-cp310-cp310-linux_x86_64.whl && \
    rm /tmp/torch-2.6.0+cpu-cp310-cp310-linux_x86_64.whl

RUN pip install --retries 5 --timeout 300 \
      datasets==4.0.0 \
      torchmetrics pandas numpy \
      scikit-learn scipy sktime rouge_score

# pyext is declared by CodeGenerationAPPSPassAt5.  Its released source still
# calls the removed inspect.getargspec API; patch only this third-party runtime
# dependency, never the official AIRS task code.
RUN mkdir -p /tmp/pyext && \
    curl -fsSL https://files.pythonhosted.org/packages/source/p/pyext/pyext-0.7.tar.gz -o /tmp/pyext/pyext.tar.gz && \
    tar -xzf /tmp/pyext/pyext.tar.gz -C /tmp/pyext && \
    sed -i "s/oargspec = inspect.getargspec/if not hasattr(inspect, 'getargspec'):\\n    inspect.getargspec = inspect.getfullargspec\\noargspec = inspect.getargspec/" /tmp/pyext/pyext-0.7/pyext.py && \
    pip install /tmp/pyext/pyext-0.7 && \
    rm -rf /tmp/pyext

WORKDIR /workspace
