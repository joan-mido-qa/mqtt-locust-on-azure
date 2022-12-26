FROM python:3.11

COPY consumer ./consumer

RUN pip install -r consumer/requirements.txt

CMD [ "python", "-m", "consumer.main" ]
