# Use an official Python runtime as a parent image
FROM python:3.8

# Install poetry
RUN set -ex && pip install poetry

VOLUME ["/data", "/config"]

# Set the working directory/python path to /app
WORKDIR /app
ENV PYTHONPATH /app

# Install any needed packages from pyproject
COPY pyproject.toml poetry.lock /app/
COPY external /app/external
RUN poetry install --no-dev --no-interaction

# Copy the current directory contents into the container at /app
COPY steambot /app/steambot

# Run app.py when the container launches
CMD ["python", "steambot/main.py"]
