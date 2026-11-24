FROM node:20-slim

# Common dev tools
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-pip python3-venv \
    git curl wget \
    && rm -rf /var/lib/apt/lists/*

# Non-root user for security
RUN useradd -m -u 1000 sandbox
USER sandbox
WORKDIR /workspace

ENV PORT=3000
ENV HOST=0.0.0.0

EXPOSE 3000

CMD ["sleep", "infinity"]
