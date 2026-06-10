FROM mysql:8.0

# Set environment variables
ENV MYSQL_ROOT_PASSWORD=root_password
ENV MYSQL_DATABASE=chonkychonk
ENV MYSQL_USER=chonky_user
ENV MYSQL_PASSWORD=chonky_password

# Copy initialization scripts
COPY 01-schema.sql /docker-entrypoint-initdb.d/
COPY 02-init-data.sql /docker-entrypoint-initdb.d/

EXPOSE 3306

HEALTHCHECK --interval=10s --timeout=20s --start-period=30s --retries=5 \
  CMD mysqladmin ping -h localhost -u root -proot_password
