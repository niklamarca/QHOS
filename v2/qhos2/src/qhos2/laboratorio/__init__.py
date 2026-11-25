"""Offline research scenarios. No broker, credentials, legacy decoder or order router."""
from .simulador import run_simulation
from .reconciliacao import align_observations, monetary_move
from .validacao import evaluate_research
