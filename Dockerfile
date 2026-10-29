FROM debian:bookworm-slim
ENV DEBIAN_FRONTEND=noninteractive LANG=C.UTF-8
RUN apt-get update && apt-get install -y --no-install-recommends \
  bash ca-certificates curl git openssh-client python3 jq ripgrep util-linux \
  latexmk texlive-latex-base texlive-latex-recommended \
  texlive-latex-extra texlive-fonts-recommended texlive-xetex \
  poppler-utils && rm -rf /var/lib/apt/lists/*
RUN useradd --uid 1000 --create-home --shell /bin/bash sandbox || true
WORKDIR /workspace
CMD ["sleep", "infinity"]
