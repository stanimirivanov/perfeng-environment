FROM docker.io/nginxinc/nginx-unprivileged:1.30.5-alpine-slim@sha256:7f79f463c6eb0d0bf8782992bb9636434398e3e5848398805aef2c9b025d8609

ARG VCS_REF
ARG TARGET_SHA256
LABEL org.opencontainers.image.source="https://github.com/stanimirivanov/perfeng-environment" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.version="1.0.0" \
      org.opencontainers.image.title="Perfeng controlled native browser candidate" \
      org.opencontainers.image.licenses="Apache-2.0" \
      dev.perfeng.target.sha256="${TARGET_SHA256}"

COPY nginx.conf /etc/nginx/nginx.conf
COPY index.html search.js /usr/share/nginx/html/
COPY LICENSE /usr/share/licenses/perfeng-native-search/LICENSE
USER 101:101
EXPOSE 8080
ENTRYPOINT ["nginx"]
CMD ["-g", "daemon off;"]
