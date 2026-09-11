# Optional worker image for archive.unpack. The host must support unprivileged
# user/network namespaces; installing bwrap alone does not grant that support.
# Build: docker build -f docker/runner.Dockerfile --build-arg BASE_IMAGE=deep-research-agent:local -t deep-research-runner:local .
ARG BASE_IMAGE=deep-research-agent:local
FROM ${BASE_IMAGE}
USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends bubblewrap libarchive-tools util-linux \
    && rm -rf /var/lib/apt/lists/*
USER appuser
ENV DR_RUNNER_ISOLATION=required \
    DR_RUNNER_ALLOWED_OPERATIONS=archive.unpack
