"""Shared declarations for dependency-injected Flask blueprints."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from flask import Blueprint, Flask


@dataclass(frozen=True)
class RouteSpec:
    rule: str
    handler: str
    methods: tuple[str, ...] | None = None
    defaults: dict[str, Any] | None = None
    provide_automatic_options: bool | None = None


def create_blueprint(
    name: str, specs: tuple[RouteSpec, ...], handlers: dict[str, Any]
) -> Blueprint:
    blueprint = Blueprint(name, __name__)
    for spec in specs:
        options: dict[str, Any] = {
            "endpoint": spec.handler,
            "view_func": handlers[spec.handler],
        }
        if spec.methods is not None:
            options["methods"] = spec.methods
        if spec.defaults is not None:
            options["defaults"] = spec.defaults
        if spec.provide_automatic_options is not None:
            options["provide_automatic_options"] = spec.provide_automatic_options
        blueprint.add_url_rule(spec.rule, **options)
    return blueprint


def add_legacy_build_aliases(app: Flask, specs: tuple[RouteSpec, ...]) -> None:
    """Keep existing url_for endpoint names while dispatching through blueprints."""
    for spec in specs:
        app.add_url_rule(
            spec.rule,
            endpoint=spec.handler,
            build_only=True,
            defaults=spec.defaults,
        )
