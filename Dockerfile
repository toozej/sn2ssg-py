FROM python:slim as base
RUN pip install sncli==0.4.2 requests

WORKDIR /app
COPY sn2ssg.py /app/
COPY templates /app/templates

ENTRYPOINT [ "python", "/app/sn2ssg.py" ]
