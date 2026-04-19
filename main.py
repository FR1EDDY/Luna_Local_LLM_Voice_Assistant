#!/usr/bin/env python3
"""Project entry: double-clap + LUNA wake word (on by default) and welcome sequence (Python 3.12; see .python-version)."""

from jarvis.cli import main

if __name__ == "__main__":
    main()

#python3 main.py --llm-backend ollama --ollama-model gemma4:e4b