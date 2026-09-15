#!/usr/bin/env python3
"""Generate and optionally enforce an architecture baseline.

The report measures structure. ``--check-policy`` compares debt-shaped sets with
a reviewed allowlist: existing debt may disappear, but new debt fails the gate.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
from collections import Counter, defaultdict, deque
from pathlib import Path
from typing import Iterable


SCHEMA_VERSION = "video-localization-architecture-baseline-v1"
POLICY_SCHEMA_VERSION = "video-localization-architecture-policy-v1"
DEFAULT_POLICY_PATH = Path(__file__).resolve().parent / "architecture" / "video_localization_architecture_policy.json"
DOMAIN_PREFIX = "app.domains.video_localization"
FRONTEND_IMPORT_PATTERN = re.compile(r"""(?:from\s+|import\s*\(\s*|import\s+)[\"']([^\"']+)[\"']""")
API_PREFIXES = {
    "video_localization": "/api/projects",
    "projects": "/api/projects",
    "history": "/api/history",
    "tasks": "/api/tasks",
    "batches": "/api/batches",
    "longform": "/api/longform",
}
HTTP_TEST_METHODS = {
    "get": "GET",
    "post": "POST",
    "put": "PUT",
    "patch": "PATCH",
    "delete": "DELETE",
    "websocket_connect": "WEBSOCKET",
}


def _line_count(path: Path) -> int:
    with path.open("r", encoding="utf-8") as handle:
        return sum(1 for _line in handle)


def _call_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        stem = _call_name(node.value)
        return f"{stem}.{node.attr}" if stem else node.attr
    return None


def _literal_value(node: ast.AST | None):
    return node.value if isinstance(node, ast.Constant) else None


def _string_shape(
    node: ast.AST,
    names: dict[str, str] | None = None,
) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return (names or {}).get(node.id)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _string_shape(node.left, names)
        right = _string_shape(node.right, names)
        if left is None or right is None:
            return None
        return f"{left}{right}"
    if isinstance(node, ast.JoinedStr):
        parts = []
        for part in node.values:
            if isinstance(part, ast.Constant) and isinstance(part.value, str):
                parts.append(part.value)
            elif isinstance(part, ast.FormattedValue):
                parts.append("{}")
            else:
                return None
        return "".join(parts)
    return None


def _normalized_route_path(path: str) -> str:
    value = path.split("?", 1)[0]
    if value != "/":
        value = value.rstrip("/")
    return value or "/"


def _route_path_pattern(path: str) -> re.Pattern[str]:
    normalized = _normalized_route_path(path)
    pieces = []
    cursor = 0
    for match in re.finditer(r"\{[^{}]+\}", normalized):
        pieces.append(re.escape(normalized[cursor : match.start()]))
        pieces.append(r"(?:[^/{}]+|\{\})")
        cursor = match.end()
    pieces.append(re.escape(normalized[cursor:]))
    return re.compile(rf"^{''.join(pieces)}/?$")


def _test_http_references(tests_root: Path) -> list[dict]:
    references = []
    for path in sorted(tests_root.rglob("test*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue

        class ReferenceVisitor(ast.NodeVisitor):
            def __init__(self):
                self._scopes: list[dict[str, str]] = [{}]

            def _visible_names(self) -> dict[str, str]:
                visible = {}
                for scope in self._scopes:
                    visible.update(scope)
                return visible

            def _remember_assignment(
                self,
                target: ast.AST,
                value: ast.AST,
            ) -> None:
                if not isinstance(target, ast.Name):
                    return
                shape = _string_shape(value, self._visible_names())
                if shape is None:
                    self._scopes[-1].pop(target.id, None)
                else:
                    self._scopes[-1][target.id] = shape

            def visit_Assign(self, node: ast.Assign) -> None:
                for target in node.targets:
                    self._remember_assignment(target, node.value)
                self.generic_visit(node)

            def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
                if node.value is not None:
                    self._remember_assignment(node.target, node.value)
                self.generic_visit(node)

            def _visit_function(
                self,
                node: ast.FunctionDef | ast.AsyncFunctionDef,
            ) -> None:
                self._scopes.append({})
                for statement in node.body:
                    self.visit(statement)
                self._scopes.pop()

            def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
                self._visit_function(node)

            def visit_AsyncFunctionDef(
                self,
                node: ast.AsyncFunctionDef,
            ) -> None:
                self._visit_function(node)

            def visit_Call(self, node: ast.Call) -> None:
                if isinstance(node.func, ast.Attribute) and node.func.attr in HTTP_TEST_METHODS and node.args:
                    shape = _string_shape(
                        node.args[0],
                        self._visible_names(),
                    )
                    if shape is not None:
                        references.append(
                            {
                                "method": HTTP_TEST_METHODS[node.func.attr],
                                "path": _normalized_route_path(shape),
                                "file": str(path.relative_to(tests_root.parent)),
                                "line": node.lineno,
                            }
                        )
                self.generic_visit(node)

        ReferenceVisitor().visit(tree)
    return references


def _python_modules(backend_root: Path) -> dict[str, Path]:
    modules: dict[str, Path] = {}
    for path in sorted((backend_root / "app").rglob("*.py")):
        parts = list(path.relative_to(backend_root).with_suffix("").parts)
        if parts[-1] == "__init__":
            parts.pop()
        modules[".".join(parts)] = path
    return modules


def _domain_display_name(module_name: str) -> str:
    if module_name == DOMAIN_PREFIX:
        return "__init__"
    return module_name.removeprefix(f"{DOMAIN_PREFIX}.")


class _ImportVisitor(ast.NodeVisitor):
    def __init__(
        self,
        *,
        module_name: str,
        known_modules: set[str],
        include_type_checking: bool,
    ) -> None:
        self._package = module_name.split(".")[:-1]
        self._known_modules = known_modules
        self._include_type_checking = include_type_checking
        self.references: set[str] = set()

    def visit_If(self, node: ast.If) -> None:
        if not self._include_type_checking and isinstance(node.test, ast.Name) and node.test.id == "TYPE_CHECKING":
            for child in node.orelse:
                self.visit(child)
            return
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        self.references.update(alias.name for alias in node.names)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.level:
            package_prefix = self._package[: len(self._package) - node.level + 1]
            module_parts = node.module.split(".") if node.module else []
            stem = ".".join([*package_prefix, *module_parts])
        else:
            stem = node.module or ""
        if stem:
            self.references.add(stem)
        for alias in node.names:
            child = f"{stem}.{alias.name}" if stem else alias.name
            if child in self._known_modules:
                self.references.add(child)


def _python_imports(
    module_name: str,
    path: Path,
    *,
    known_modules: set[str],
    include_type_checking: bool = False,
) -> set[str]:
    visitor = _ImportVisitor(
        module_name=module_name,
        known_modules=known_modules,
        include_type_checking=include_type_checking,
    )
    visitor.visit(ast.parse(path.read_text(encoding="utf-8")))
    return visitor.references


def _strongly_connected_components(
    graph: dict[str, set[str]],
) -> list[list[str]]:
    indexes: dict[str, int] = {}
    low_links: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    components: list[list[str]] = []

    def visit(node: str) -> None:
        indexes[node] = len(indexes)
        low_links[node] = indexes[node]
        stack.append(node)
        on_stack.add(node)
        for dependency in sorted(graph.get(node, set())):
            if dependency not in indexes:
                visit(dependency)
                low_links[node] = min(low_links[node], low_links[dependency])
            elif dependency in on_stack:
                low_links[node] = min(low_links[node], indexes[dependency])
        if low_links[node] != indexes[node]:
            return
        component: list[str] = []
        while stack:
            dependency = stack.pop()
            on_stack.remove(dependency)
            component.append(dependency)
            if dependency == node:
                break
        components.append(sorted(component))

    for node in sorted(graph):
        if node not in indexes:
            visit(node)
    return sorted(
        (component for component in components if len(component) > 1),
        key=lambda component: (-len(component), component),
    )


def _route_metrics(api_path: Path) -> dict:
    tree = ast.parse(api_path.read_text(encoding="utf-8"))
    top_level_functions = {
        node.name: node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }

    def local_helper_closure(function_name: str) -> list[str]:
        discovered: set[str] = set()
        pending = [function_name]
        while pending:
            current_name = pending.pop()
            current = top_level_functions[current_name]
            for child in ast.walk(current):
                if not (
                    isinstance(child, ast.Name)
                    and isinstance(child.ctx, ast.Load)
                    and child.id in top_level_functions
                    and child.id != function_name
                    and child.id not in discovered
                ):
                    continue
                discovered.add(child.id)
                pending.append(child.id)
        return sorted(discovered)

    domain_aliases: dict[str, str] = {}
    local_app_aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            stem = node.module or ""
            if stem.startswith("app."):
                for alias in node.names:
                    local_app_aliases[alias.asname or alias.name] = f"{stem}.{alias.name}"
            if stem.startswith(DOMAIN_PREFIX):
                for alias in node.names:
                    imported = f"{stem}.{alias.name}" if stem == DOMAIN_PREFIX else stem
                    domain_aliases[alias.asname or alias.name] = imported.removeprefix(f"{DOMAIN_PREFIX}.")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("app."):
                    local_app_aliases[alias.asname or alias.name.rsplit(".", 1)[-1]] = alias.name
                if not alias.name.startswith(f"{DOMAIN_PREFIX}."):
                    continue
                imported = alias.name.removeprefix(f"{DOMAIN_PREFIX}.")
                domain_aliases[alias.asname or alias.name.rsplit(".", 1)[-1]] = imported

    routes: list[dict] = []
    dependency_counts: Counter[str] = Counter()
    async_unprotected_app_calls: list[str] = []
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        methods: list[str] = []
        paths: list[str] = []
        for decorator in node.decorator_list:
            if not (
                isinstance(decorator, ast.Call)
                and isinstance(decorator.func, ast.Attribute)
                and isinstance(decorator.func.value, ast.Name)
                and decorator.func.value.id == "router"
            ):
                continue
            methods.append(decorator.func.attr.upper())
            if (
                decorator.args
                and isinstance(decorator.args[0], ast.Constant)
                and isinstance(decorator.args[0].value, str)
            ):
                paths.append(decorator.args[0].value)
        if not methods:
            continue
        local_helpers = local_helper_closure(node.name)
        analysis_nodes = [
            node,
            *(top_level_functions[name] for name in local_helpers),
        ]
        dependencies = sorted(
            {
                domain_aliases[child.id]
                for child in ast.walk(node)
                if isinstance(child, ast.Name) and child.id in domain_aliases
            }
        )
        dependency_counts.update(dependencies)
        parent_by_child = {child: parent for parent in ast.walk(node) for child in ast.iter_child_nodes(parent)}
        has_to_thread = any(
            isinstance(child, ast.Call)
            and isinstance(child.func, ast.Attribute)
            and isinstance(child.func.value, ast.Name)
            and child.func.value.id == "asyncio"
            and child.func.attr == "to_thread"
            for child in ast.walk(node)
        )
        has_native_await = any(
            isinstance(child, ast.Await)
            and not (
                isinstance(child.value, ast.Call)
                and isinstance(child.value.func, ast.Attribute)
                and isinstance(child.value.func.value, ast.Name)
                and child.value.func.value.id == "asyncio"
                and child.value.func.attr == "to_thread"
            )
            for child in ast.walk(node)
        )
        if isinstance(node, ast.FunctionDef):
            execution_class = "sync_threadpool"
        elif has_to_thread and has_native_await:
            execution_class = "async_hybrid"
        elif has_to_thread:
            execution_class = "async_offloaded"
        else:
            execution_class = "async_native"
        direct_app_calls = set()
        business_owners = {
            local_app_aliases[child.id]
            for analysis_node in analysis_nodes
            for child in ast.walk(analysis_node)
            if (
                isinstance(child, ast.Name)
                and child.id in local_app_aliases
                and (
                    local_app_aliases[child.id].startswith("app.services")
                    or local_app_aliases[child.id].startswith(DOMAIN_PREFIX)
                )
            )
        }
        for child in ast.walk(node):
            if not isinstance(child, ast.Call):
                continue
            call_target = None
            owner = None
            if (
                isinstance(child.func, ast.Attribute)
                and isinstance(child.func.value, ast.Name)
                and child.func.value.id in local_app_aliases
            ):
                owner = local_app_aliases[child.func.value.id]
                call_target = f"{owner}.{child.func.attr}"
            elif isinstance(child.func, ast.Name) and child.func.id in local_app_aliases:
                call_target = local_app_aliases[child.func.id]
                owner = call_target.rsplit(".", 1)[0]
            if call_target:
                direct_app_calls.add(call_target)
            if owner and (owner.startswith("app.services") or owner.startswith(DOMAIN_PREFIX)):
                business_owners.add(owner)
        route_dependency_nodes = [
            *node.decorator_list,
            *node.args.defaults,
            *node.args.kw_defaults,
            *(
                argument.annotation
                for argument in [
                    *node.args.posonlyargs,
                    *node.args.args,
                    *node.args.kwonlyargs,
                ]
                if argument.annotation is not None
            ),
        ]
        route_dependencies = sorted(
            {
                (_call_name(child.args[0]) or "<dynamic>") if child.args else "<anonymous>"
                for dependency_node in route_dependency_nodes
                if dependency_node is not None
                for child in ast.walk(dependency_node)
                if (isinstance(child, ast.Call) and _call_name(child.func) in {"Depends", "fastapi.Depends"})
            }
        )
        declared_errors = []
        for analysis_node in analysis_nodes:
            for child in ast.walk(analysis_node):
                if not isinstance(child, ast.Call):
                    continue
                error_name = _call_name(child.func)
                if error_name not in {
                    "AppException",
                    "HTTPException",
                    "fastapi.HTTPException",
                }:
                    continue
                status = _literal_value(child.args[0] if child.args else None)
                code = _literal_value(child.args[1] if (error_name == "AppException" and len(child.args) > 1) else None)
                if status is None:
                    status = next(
                        (_literal_value(keyword.value) for keyword in child.keywords if keyword.arg == "status_code"),
                        None,
                    )
                declared_errors.append(
                    {
                        "status": status,
                        "code": code,
                    }
                )
        declared_errors = sorted(
            {(error["status"], error["code"]) for error in declared_errors},
            key=lambda item: (
                item[0] is None,
                item[0] if item[0] is not None else 0,
                item[1] or "",
            ),
        )
        unprotected_app_calls: list[str] = []
        if isinstance(node, ast.AsyncFunctionDef):
            for child in ast.walk(node):
                if not (
                    isinstance(child, ast.Call)
                    and isinstance(child.func, ast.Attribute)
                    and isinstance(child.func.value, ast.Name)
                    and child.func.value.id in local_app_aliases
                ):
                    continue
                current: ast.AST = child
                protected = False
                while current in parent_by_child:
                    current = parent_by_child[current]
                    if isinstance(current, ast.Await):
                        protected = True
                        break
                    if (
                        isinstance(current, ast.Call)
                        and isinstance(current.func, ast.Attribute)
                        and isinstance(current.func.value, ast.Name)
                        and current.func.value.id == "asyncio"
                        and current.func.attr == "to_thread"
                    ):
                        protected = True
                        break
                if not protected:
                    unprotected_app_calls.append(f"{child.func.value.id}.{child.func.attr}")
        unprotected_app_calls = sorted(set(unprotected_app_calls))
        async_unprotected_app_calls.extend(f"{node.name}:{call}" for call in unprotected_app_calls)
        routes.append(
            {
                "name": node.name,
                "line": node.lineno,
                "methods": methods,
                "paths": paths,
                "async": isinstance(node, ast.AsyncFunctionDef),
                "has_await": any(isinstance(child, ast.Await) for child in ast.walk(node)),
                "execution_class": execution_class,
                "direct_domain_dependencies": dependencies,
                "direct_app_calls": sorted(direct_app_calls),
                "local_helper_dependencies": local_helpers,
                "business_owners": sorted(business_owners),
                "route_level_authorization": {
                    "declared": bool(route_dependencies),
                    "dependencies": route_dependencies,
                    "scope": "route-level-static-only",
                },
                "declared_errors": [
                    {
                        "status": status,
                        "code": code,
                    }
                    for status, code in declared_errors
                ],
                "unprotected_app_calls": unprotected_app_calls,
            }
        )
    async_without_await = [route["name"] for route in routes if route["async"] and not route["has_await"]]
    execution_class_counts = Counter(route["execution_class"] for route in routes)
    return {
        "line_count": _line_count(api_path),
        "route_count": len(routes),
        "async_route_count": sum(bool(route["async"]) for route in routes),
        "sync_route_count": sum(not route["async"] for route in routes),
        "async_without_await_count": len(async_without_await),
        "async_without_await": async_without_await,
        "async_unprotected_app_call_count": len(async_unprotected_app_calls),
        "async_unprotected_app_calls": sorted(async_unprotected_app_calls),
        "execution_class_counts": {
            name: execution_class_counts.get(name, 0)
            for name in (
                "sync_threadpool",
                "async_offloaded",
                "async_native",
                "async_hybrid",
            )
        },
        "domain_dependency_route_counts": dict(
            sorted(
                dependency_counts.items(),
                key=lambda item: (-item[1], item[0]),
            )
        ),
        "routes": routes,
    }


def _api_scope_metrics(
    api_paths: dict[str, Path],
    *,
    tests_root: Path,
) -> dict:
    """Aggregate route metrics across every API surface used by the workbench."""

    test_http_references = _test_http_references(tests_root)
    file_metrics = {name: _route_metrics(path) for name, path in api_paths.items()}
    routes = []
    for source, metrics in file_metrics.items():
        prefix = API_PREFIXES[source]
        for route in metrics["routes"]:
            route_path = route["paths"][0] if route["paths"] else ""
            full_path = _normalized_route_path(f"{prefix}{route_path}")
            route_pattern = _route_path_pattern(full_path)
            test_references = sorted(
                {
                    f"{reference['file']}:{reference['line']}"
                    for reference in test_http_references
                    if reference["method"] in route["methods"] and route_pattern.fullmatch(reference["path"])
                }
            )
            routes.append(
                {
                    **route,
                    "source": source,
                    "qualified_name": f"{source}:{route['name']}",
                    "full_paths": [full_path],
                    "test_references": test_references,
                }
            )
    dependency_counts: Counter[str] = Counter()
    execution_class_counts: Counter[str] = Counter()
    for metrics in file_metrics.values():
        dependency_counts.update(metrics["domain_dependency_route_counts"])
        execution_class_counts.update(metrics["execution_class_counts"])
    async_without_await = [
        f"{source}:{name}" for source, metrics in file_metrics.items() for name in metrics["async_without_await"]
    ]
    async_unprotected_app_calls = [
        f"{source}:{call}"
        for source, metrics in file_metrics.items()
        for call in metrics["async_unprotected_app_calls"]
    ]
    return {
        "line_count": sum(metrics["line_count"] for metrics in file_metrics.values()),
        "file_count": len(file_metrics),
        "files": list(file_metrics),
        "file_metrics": file_metrics,
        "route_count": len(routes),
        "async_route_count": sum(bool(route["async"]) for route in routes),
        "sync_route_count": sum(not route["async"] for route in routes),
        "async_without_await_count": len(async_without_await),
        "async_without_await": async_without_await,
        "async_unprotected_app_call_count": len(async_unprotected_app_calls),
        "async_unprotected_app_calls": async_unprotected_app_calls,
        "execution_class_counts": {
            name: execution_class_counts.get(name, 0)
            for name in (
                "sync_threadpool",
                "async_offloaded",
                "async_native",
                "async_hybrid",
            )
        },
        "domain_dependency_route_counts": dict(
            sorted(
                dependency_counts.items(),
                key=lambda item: (-item[1], item[0]),
            )
        ),
        "route_test_reference_count": sum(bool(route["test_references"]) for route in routes),
        "routes_without_test_references": [route["qualified_name"] for route in routes if not route["test_references"]],
        "manifest_scope": {
            "business_owners": (
                "direct route references plus transitive top-level helper references within the same API module"
            ),
            "authorization": (
                "route-level Depends declarations only; global middleware is outside this static inventory"
            ),
            "errors": (
                "explicit AppException/HTTPException calls in the route or its transitive top-level helper closure"
            ),
            "tests": "direct HTTP method/path references in Python tests only",
        },
        "routes": routes,
    }


def _backend_metrics(repo_root: Path) -> dict:
    backend_root = repo_root / "backend"
    modules = _python_modules(backend_root)
    known_modules = set(modules)
    domain_modules = {
        module: path
        for module, path in modules.items()
        if module == DOMAIN_PREFIX or module.startswith(f"{DOMAIN_PREFIX}.")
    }
    implementation_modules = {module: path for module, path in domain_modules.items() if module != DOMAIN_PREFIX}
    runtime_imports = {
        module: _python_imports(
            module,
            path,
            known_modules=known_modules,
        )
        for module, path in modules.items()
    }
    full_domain_graph = {
        module: {dependency for dependency in runtime_imports[module] if dependency in domain_modules}
        for module in domain_modules
    }
    direct_domain_graph = {
        module: {dependency for dependency in runtime_imports[module] if dependency in implementation_modules}
        for module in implementation_modules
    }
    incoming: defaultdict[str, set[str]] = defaultdict(set)
    for module, dependencies in runtime_imports.items():
        for dependency in dependencies:
            if dependency in domain_modules:
                incoming[dependency].add(module)

    module_rows = []
    for module, path in domain_modules.items():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        callables = [
            node
            for node in tree.body
            if isinstance(
                node,
                (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef),
            )
        ]
        module_rows.append(
            {
                "module": _domain_display_name(module),
                "line_count": _line_count(path),
                "top_level_callable_count": len(callables),
                "public_callable_count": sum(not node.name.startswith("_") for node in callables),
                "internal_dependency_count": len(full_domain_graph[module]),
                "internal_incoming_count": sum(importer in domain_modules for importer in incoming[module]),
                "external_incoming_count": sum(importer not in domain_modules for importer in incoming[module]),
            }
        )
    runtime_cycles = [
        [_domain_display_name(module) for module in component]
        for component in _strongly_connected_components(direct_domain_graph)
    ]
    package_runtime_cycles = [
        [_domain_display_name(module) for module in component]
        for component in _strongly_connected_components(full_domain_graph)
    ]
    cross_layer_importers = {
        module: sorted(_domain_display_name(dependency) for dependency in references if dependency in domain_modules)
        for module, references in runtime_imports.items()
        if module not in domain_modules and any(dependency in domain_modules for dependency in references)
    }
    return {
        "domain_file_count": len(domain_modules),
        "domain_line_count": sum(row["line_count"] for row in module_rows),
        "modules": sorted(
            module_rows,
            key=lambda row: (-row["line_count"], row["module"]),
        ),
        "runtime_cycles": runtime_cycles,
        "package_runtime_cycles": package_runtime_cycles,
        "cross_layer_importers": dict(sorted(cross_layer_importers.items())),
        "no_production_importer": sorted(
            _domain_display_name(module) for module in domain_modules if not incoming[module]
        ),
        "api": _api_scope_metrics(
            {
                name: backend_root / "app" / "api" / f"{name}.py"
                for name in (
                    "video_localization",
                    "projects",
                    "history",
                    "tasks",
                    "batches",
                    "longform",
                )
            },
            tests_root=repo_root / "tests",
        ),
    }


def _resolve_frontend_import(
    source: Path,
    specifier: str,
    known_paths: dict[Path, Path],
) -> Path | None:
    if not specifier.startswith("."):
        return None
    base = source.parent / specifier
    candidates = (
        base,
        base.with_suffix(".ts"),
        base.with_suffix(".svelte"),
        base / "index.ts",
    )
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved in known_paths:
            return known_paths[resolved]
    return None


def _frontend_metrics(repo_root: Path) -> dict:
    route_root = repo_root / "frontend" / "src" / "routes" / "video-localization"
    production_files = sorted(
        path
        for path in route_root.rglob("*")
        if path.suffix in {".ts", ".svelte"} and not path.name.endswith(".test.ts")
    )
    known_paths = {path.resolve(): path for path in production_files}
    graph: dict[Path, set[Path]] = {path: set() for path in production_files}
    for path in production_files:
        text = path.read_text(encoding="utf-8")
        for specifier in FRONTEND_IMPORT_PATTERN.findall(text):
            dependency = _resolve_frontend_import(
                path,
                specifier,
                known_paths,
            )
            if dependency is not None:
                graph[path].add(dependency)

    entry = route_root / "+page.svelte"
    reachable: set[Path] = {entry}
    queue = deque([entry])
    while queue:
        path = queue.popleft()
        for dependency in graph.get(path, set()):
            if dependency not in reachable:
                reachable.add(dependency)
                queue.append(dependency)
    unreachable = sorted(str(path.relative_to(route_root)) for path in set(production_files) - reachable)
    test_files = sorted(route_root.rglob("*.test.ts"))
    source_string_markers = (
        "readFileSync",
        "componentSource",
        "pageSource",
        "timelineSource",
        "previewSource",
        "inspectorSource",
    )
    component_test_markers = (
        "@testing-library/svelte",
        "mount(",
        "render(",
    )
    source_string_tests = []
    component_behavior_tests = []
    for path in test_files:
        text = path.read_text(encoding="utf-8")
        relative = str(path.relative_to(route_root))
        if any(marker in text for marker in source_string_markers):
            source_string_tests.append(relative)
        if any(marker in text for marker in component_test_markers):
            component_behavior_tests.append(relative)

    rows = [
        {
            "file": str(path.relative_to(route_root)),
            "line_count": _line_count(path),
            "local_dependency_count": len(graph[path]),
        }
        for path in production_files
    ]
    return {
        "production_file_count": len(production_files),
        "test_file_count": len(test_files),
        "production_files": sorted(
            rows,
            key=lambda row: (-row["line_count"], row["file"]),
        ),
        "entry_local_dependency_count": len(graph.get(entry, set())),
        "unreachable_from_page": unreachable,
        "unreachable_svelte_components": [path for path in unreachable if path.endswith(".svelte")],
        "source_string_test_count": len(source_string_tests),
        "source_string_tests": source_string_tests,
        "component_behavior_test_count": len(component_behavior_tests),
        "component_behavior_tests": component_behavior_tests,
    }


def build_report(repo_root: Path) -> dict:
    root = repo_root.resolve()
    return {
        "schema_version": SCHEMA_VERSION,
        "scope": "video_localization",
        "repo_root": str(root),
        "backend": _backend_metrics(root),
        "frontend": _frontend_metrics(root),
    }


def load_policy(path: Path) -> dict:
    policy = json.loads(path.read_text(encoding="utf-8"))
    if policy.get("schema_version") != POLICY_SCHEMA_VERSION:
        raise ValueError(f"unsupported architecture policy schema: {policy.get('schema_version')!r}")
    if policy.get("baseline_schema_version") != SCHEMA_VERSION:
        raise ValueError(
            f"architecture policy targets a different baseline schema: {policy.get('baseline_schema_version')!r}"
        )
    if policy.get("mode") != "no-new-debt":
        raise ValueError("architecture policy mode must be 'no-new-debt'")
    if not isinstance(policy.get("owner"), str) or not policy["owner"].strip():
        raise ValueError("architecture policy owner must be non-empty")
    if not isinstance(policy.get("exception_exit_conditions"), dict):
        raise ValueError("architecture policy exception_exit_conditions must be an object")
    required_lists = (
        "allowed_runtime_cycles",
        "allowed_package_runtime_cycles",
        "allowed_cross_layer_importers",
        "allowed_async_without_await",
        "allowed_async_unprotected_app_calls",
        "allowed_unreachable_svelte_components",
        "allowed_source_string_tests",
    )
    for field in required_lists:
        if not isinstance(policy.get(field), list):
            raise ValueError(f"architecture policy field {field!r} must be a list")
        if not policy["exception_exit_conditions"].get(field):
            raise ValueError(f"architecture policy field {field!r} needs an exit condition")
    return policy


def _canonical_cycles(cycles: Iterable[Iterable[str]]) -> set[tuple[str, ...]]:
    return {tuple(sorted(cycle)) for cycle in cycles}


def policy_violations(report: dict, policy: dict) -> list[dict]:
    """Return only debt that is present now but absent from the allowlist."""

    backend = report["backend"]
    frontend = report["frontend"]
    comparisons = (
        (
            "new-runtime-cycle",
            _canonical_cycles(backend["runtime_cycles"]),
            _canonical_cycles(policy["allowed_runtime_cycles"]),
        ),
        (
            "new-package-runtime-cycle",
            _canonical_cycles(backend["package_runtime_cycles"]),
            _canonical_cycles(policy["allowed_package_runtime_cycles"]),
        ),
        (
            "new-cross-layer-importer",
            set(backend["cross_layer_importers"]),
            set(policy["allowed_cross_layer_importers"]),
        ),
        (
            "new-async-without-await",
            set(backend["api"]["async_without_await"]),
            set(policy["allowed_async_without_await"]),
        ),
        (
            "new-async-unprotected-app-call",
            set(backend["api"]["async_unprotected_app_calls"]),
            set(policy["allowed_async_unprotected_app_calls"]),
        ),
        (
            "new-unreachable-svelte-component",
            set(frontend["unreachable_svelte_components"]),
            set(policy["allowed_unreachable_svelte_components"]),
        ),
        (
            "new-source-string-test",
            set(frontend["source_string_tests"]),
            set(policy["allowed_source_string_tests"]),
        ),
    )
    violations = []
    for code, current, allowed in comparisons:
        for item in sorted(current - allowed):
            violations.append(
                {
                    "code": code,
                    "item": list(item) if isinstance(item, tuple) else item,
                }
            )
    return violations


def _format_cycle(cycle: Iterable[str]) -> str:
    return " ↔ ".join(cycle)


def format_text(report: dict) -> str:
    backend = report["backend"]
    api = backend["api"]
    frontend = report["frontend"]
    lines = [
        f"Schema: {report['schema_version']}",
        (f"Backend domain: {backend['domain_file_count']} files / {backend['domain_line_count']} lines"),
        (
            "API: "
            f"{api['file_count']} files / {api['route_count']} routes; "
            f"{api['async_route_count']} async; "
            f"{api['async_without_await_count']} async without await; "
            f"{api['async_unprotected_app_call_count']} unprotected "
            "app calls"
        ),
        (
            "API execution classes: "
            + "; ".join(f"{name}={count}" for name, count in api["execution_class_counts"].items())
        ),
        (f"API direct test references: {api['route_test_reference_count']}/{api['route_count']} routes"),
        "Runtime import cycles:",
    ]
    cycles = backend["runtime_cycles"]
    lines.extend(f"  - {_format_cycle(cycle)}" for cycle in cycles)
    if not cycles:
        lines.append("  - none")
    package_cycles = backend["package_runtime_cycles"]
    if package_cycles != cycles:
        lines.append("Package-expanded runtime import cycles:")
        lines.extend(f"  - {_format_cycle(cycle)}" for cycle in package_cycles)
    lines.extend(
        [
            (
                "Frontend route: "
                f"{frontend['production_file_count']} production files / "
                f"{frontend['test_file_count']} tests"
            ),
            (f"Page dependencies: {frontend['entry_local_dependency_count']}"),
            (f"Unreachable Svelte components: {len(frontend['unreachable_svelte_components'])}"),
            (
                "Source-string tests / component behavior tests: "
                f"{frontend['source_string_test_count']} / "
                f"{frontend['component_behavior_test_count']}"
            ),
        ]
    )
    return "\n".join(lines)


def format_policy_result(violations: list[dict]) -> str:
    if not violations:
        return "Architecture policy: PASS (no new structural debt)"
    lines = [f"Architecture policy: FAIL ({len(violations)} violation(s))"]
    for violation in violations:
        item = violation["item"]
        display = _format_cycle(item) if isinstance(item, list) else item
        lines.append(f"  - {violation['code']}: {display}")
    return "\n".join(lines)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
    )
    parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
    )
    parser.add_argument(
        "--check-policy",
        action="store_true",
        help="fail when current debt is absent from the reviewed allowlist",
    )
    parser.add_argument(
        "--policy",
        type=Path,
        default=DEFAULT_POLICY_PATH,
        help="policy JSON used with --check-policy",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    report = build_report(args.repo_root)
    violations: list[dict] = []
    if args.check_policy:
        policy = load_policy(args.policy)
        violations = policy_violations(report, policy)
    if args.format == "json":
        payload = (
            {
                "report": report,
                "policy_path": str(args.policy.resolve()),
                "policy_violations": violations,
            }
            if args.check_policy
            else report
        )
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(format_text(report))
        if args.check_policy:
            print(format_policy_result(violations))
    return 1 if violations else 0


if __name__ == "__main__":
    raise SystemExit(main())
