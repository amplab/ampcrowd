#!/bin/bash
docker-compose kill
docker-compose build
docker-compose run --rm --service-ports web -d "$@"

