# Stage 1: builder — needs gcc/g++ to compile tree-sitter C extensions
FROM python:3.12 AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY pyproject.toml ./
COPY src/ ./src/

RUN pip install --no-cache-dir --prefix=/install .

# Stage 2: slim runtime — no build tools needed
FROM python:3.12-slim AS runtime

COPY --from=builder /install /usr/local

WORKDIR /app

# Non-root user for security
RUN useradd -m -u 1000 reviewer
USER reviewer

ENTRYPOINT ["pr-reviewer"]
CMD ["review"]
