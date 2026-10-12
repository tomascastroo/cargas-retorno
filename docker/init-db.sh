#!/bin/sh
# Se ejecuta una sola vez, al crear el volumen de PostgreSQL.
# La aplicación usa un rol SIN superusuario ni BYPASSRLS: así Row-Level Security se aplica
# siempre. El superusuario (POSTGRES_USER) queda sólo para administración.
set -eu
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres <<SQL
CREATE ROLE ${CARGAS_DB_USER} LOGIN PASSWORD '${CARGAS_DB_PASSWORD}' NOSUPERUSER NOBYPASSRLS NOCREATEROLE CREATEDB;
CREATE DATABASE ${CARGAS_DB_NAME} OWNER ${CARGAS_DB_USER};
CREATE DATABASE ${CARGAS_DB_NAME}_test OWNER ${CARGAS_DB_USER};
SQL
