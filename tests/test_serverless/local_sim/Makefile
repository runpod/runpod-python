.PHONY: localhost worker all

all: localhost worker

localhost:
	python localhost.py &

worker:
	python worker.py

clean:
	find . -type f -name ".runpod_jobs.pkl" -delete
	find . -type f -name "*.pyc" -delete
	find . -type d -name "__pycache__" -delete
