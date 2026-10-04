# The devcontainer should use the developer target and run as root with podman
# or docker with user namespaces.
FROM ghcr.io/diamondlightsource/ubuntu-devcontainer:noble AS developer

ENV DOCKER=docker-28.5.1
ENV DOCKER_COMPOSE_RELEASE_TAG=v2.40.3

# Add any system dependencies for the developer/build environment here
RUN apt-get update -y && apt-get install -y --no-install-recommends \
    graphviz \
    curl \
    && apt-get dist-clean

# install the docker ce cli binary
RUN curl -O https://download.docker.com/linux/static/stable/x86_64/${DOCKER}.tgz && \
    tar xvf ${DOCKER}.tgz && \
    cp docker/docker /usr/bin && \
    rm -r ${DOCKER}.tgz docker

# install docker-compose plugin
RUN mkdir -p /usr/libexec/docker/cli-plugins/ && \
    curl -SL https://github.com/docker/compose/releases/download/${DOCKER_COMPOSE_RELEASE_TAG}/docker-compose-linux-x86_64 -o /usr/libexec/docker/cli-plugins/docker-compose && \
    chmod +x /usr/libexec/docker/cli-plugins/docker-compose

# The build stage installs the context into the venv
FROM developer AS build

# Change the working directory to the `app` directory
# and copy in the project
WORKDIR /app
COPY . /app
RUN chmod o+wrX .

# Tell uv sync to install python in a known location so we can copy it out later
ENV UV_PYTHON_INSTALL_DIR=/python

# Sync the project without its dev dependencies
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-editable --no-dev --managed-python


# The runtime stage copies the built venv into a runtime container
FROM ubuntu:resolute AS runtime

# Copy the python installation from the build stage
COPY --from=build /python /python

# Copy the environment, but not the source code
COPY --from=build /app/.venv /app/.venv
ENV PATH=/app/.venv/bin:$PATH

# change this entrypoint if it is not the same as the repo
ENTRYPOINT ["pva-to-video"]
CMD ["--port", "8080"]
