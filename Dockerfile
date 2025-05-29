FROM python:3.13-slim AS base
RUN pip install sncli==0.4.3 requests

WORKDIR /app
COPY sn2ssg.py /app/
COPY templates /app/templates

ENTRYPOINT [ "python", "/app/sn2ssg.py" ]
