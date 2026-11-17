FROM python:3.13-slim
WORKDIR /app
COPY src/ /app/
RUN useradd --system --uid 10001 agent && mkdir /data && chown agent /data
USER 10001
ENV PERSONAL_AGENT_CONFIG=/config/server-config.json
CMD ["python", "-u", "/app/server.py"]
