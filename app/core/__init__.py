"""Core cross-cutting concerns: lifecycle, errors, logging, DI container.

This package contains infrastructure shared by every module. Nothing here
should depend on a specific domain module — only on the standard library
and well-known third-party packages (pydantic, structlog, etc.).
"""
