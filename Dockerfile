FROM python:3.14

# RUN pip install poetry
COPY . .
RUN pip install .
# RUN poetry install
# RUN echo poetry env activate --quiet >> ~/.bashrc

ENTRYPOINT ["/bin/bash"]
