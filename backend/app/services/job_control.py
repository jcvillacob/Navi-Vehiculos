"""
Primitivas compartidas de control de jobs de calculo.

Vive en un modulo aparte para evitar imports circulares entre
``rendimientos.py`` (calculo) y ``rendimientos_jobs.py`` (orquestacion).
"""

from __future__ import annotations

from typing import Callable

ShouldStop = Callable[[], bool]


class JobCancelled(Exception):
    """
    Lanzada de forma cooperativa cuando el job fue cancelado desde fuera.

    Los providers NUNCA deben tragarse esta excepcion en sus ``except Exception``
    por placa: deben re-lanzarla para que el orquestador cierre el job.
    """


def check_stop(should_stop: ShouldStop | None) -> None:
    """Lanza JobCancelled si ``should_stop`` existe y devuelve True."""
    if should_stop is not None and should_stop():
        raise JobCancelled("Job cancelado por el usuario")
