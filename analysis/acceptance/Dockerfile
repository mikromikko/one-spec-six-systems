FROM mcr.microsoft.com/playwright@sha256:dcc5531e97840b9b5e794f2814476b21571c5124a3fca2267d73041f56e7580e
USER root
RUN apt-get update && apt-get install -y --no-install-recommends python3-venv && rm -rf /var/lib/apt/lists/*
WORKDIR /experiment
COPY runs/claude/run1 runs/claude/run1
COPY runs/claude/run2 runs/claude/run2
COPY runs/claude/run3 runs/claude/run3
COPY runs/copilot/run1 runs/copilot/run1
COPY runs/copilot/run2 runs/copilot/run2
COPY runs/copilot/run3 runs/copilot/run3
RUN for run in runs/claude/run1 runs/claude/run2 runs/claude/run3; do (cd "$run" && npm ci --ignore-scripts --no-audit --no-fund); done
COPY analysis/acceptance/python-rerun.lock analysis/acceptance/python-rerun.lock
RUN python3 -m venv runs/copilot/run1/.venv && runs/copilot/run1/.venv/bin/pip install --no-cache-dir -r analysis/acceptance/python-rerun.lock
COPY analysis/acceptance analysis/acceptance
COPY analysis/results-*.md analysis/
COPY spec.md acceptance.md ./
ENTRYPOINT ["python3", "analysis/acceptance/rerun.py"]
