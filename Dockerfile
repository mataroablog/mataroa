FROM python:3.13

ENV PYTHONUNBUFFERED=1
ENV PYTHONFAULTHANDLER=1
ENV PYTHONDONTWRITEBYTECODE=1

WORKDIR /app
ADD https://astral.sh/uv/0.8.13/install.sh /uv-installer.sh
RUN sh /uv-installer.sh
ENV PATH="/root/.local/bin/:$PATH"

COPY pyproject.toml uv.lock* ./
RUN uv sync --frozen --no-cache

COPY . .
