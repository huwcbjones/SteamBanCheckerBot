# Use an official Python runtime as a parent image
FROM python:3

# Install pipenv
RUN set -ex && pip install pipenv --upgrade

# Set the working directory to /app
WORKDIR /app

# Copy the current directory contents into the container at /app
ADD . /app

# Install any needed packages specified in requirements.txt
RUN pipenv install

# Args
ARG discord_api_token=''
ENV DISCORD_API_TOKEN=$discord_api_token

ARG steam_api_token=''
ENV STEAM_API_TOKEN=$steam_api_token

# Run app.py when the container launches
CMD ["pipenv", "run", "python3 main.py"]
