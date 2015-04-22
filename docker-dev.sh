#!/bin/bash
docker-compose kill
docker-compose build
docker-compose run --service-ports web -d "$@"

