FROM mysql:8.0

# Non-secret config — safe to bake in.
ENV MYSQL_DATABASE=chonkychonk
ENV MYSQL_USER=chonky_user

# MYSQL_ROOT_PASSWORD and MYSQL_PASSWORD are deliberately NOT set here.
# Baking a password into an ENV instruction embeds it in the image's
# layers/history (visible to anyone with `docker history`/`docker inspect`
# access), even for a throwaway local-dev value. Pass them at container
# start instead, e.g.:
#   docker run -e MYSQL_ROOT_PASSWORD=root_password \
#              -e MYSQL_PASSWORD=chonky_password \
#              chonky-mysql

# Copy initialization scripts
COPY 01-schema.sql /docker-entrypoint-initdb.d/
COPY 02-init-data.sql /docker-entrypoint-initdb.d/

EXPOSE 3306

# Shell form so $MYSQL_ROOT_PASSWORD expands from whatever was passed at
# `docker run` time, rather than a value baked in here. Passed via MYSQL_PWD
# (an env var mysqladmin reads directly), not a -p CLI flag — a CLI arg
# would put the expanded password in the process list (`ps aux` inside the
# container), visible to anything that can see other processes there.
HEALTHCHECK --interval=10s --timeout=20s --start-period=30s --retries=5 \
  CMD MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysqladmin ping -h localhost -u root

USER mysql
