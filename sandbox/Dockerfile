# syntax=docker/dockerfile:1
# Python sandbox for the agent's run_python tool: only pandas, numpy, scipy, statsmodels and
# scikit-learn are installed, the server code lives in site-packages so `python -I` finds it.
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --only-binary=:all: -r /tmp/requirements.txt \
    && rm -f /tmp/requirements.txt
# no installer left behind for code that gets past every other layer
RUN pip uninstall -y pip
# gid 1000 is the api user's group: the socket is 0660 so only these two users can open it.
RUN groupadd --gid 1000 ipc \
    && useradd --uid 10001 --gid 1000 --no-create-home --shell /usr/sbin/nologin sandbox \
    && mkdir -p /ipc && chown 10001:1000 /ipc && chmod 0770 /ipc
COPY sandbox_server /usr/local/lib/python3.12/site-packages/sandbox_server
RUN python -m compileall -q /usr/local/lib/python3.12/site-packages/sandbox_server
USER 10001:1000
CMD ["python", "-I", "-m", "sandbox_server.server"]
