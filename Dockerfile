FROM python:2

RUN apt-get update && apt-get install -y postgresql-client

COPY requirements.txt /usr/src/app/
WORKDIR /usr/src/app
RUN pip install -r requirements.txt

ENV PYTHONUNBUFFERED 1
EXPOSE 8000

COPY ampcrowd/docker-entrypoint.sh /usr/src/app/ampcrowd
ENTRYPOINT ["bash", "ampcrowd/docker-entrypoint.sh"]
CMD ["-s", "-f"]

