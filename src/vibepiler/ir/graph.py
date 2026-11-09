"""ProgramGraph: a typed multigraph over the whole project.

Backed by networkx so passes can use its algorithms (SCC, reachability,
PageRank, ...) directly on the views this class exposes.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import networkx as nx

from vibepiler.ir.model import Edge, EdgeKind, Node, NodeKind


class ProgramGraph:
    def __init__(self) -> None:
        self._g: nx.MultiDiGraph = nx.MultiDiGraph()
        #: free-form project-level metadata (resolution stats, name registries, ...)
        self.meta: dict[str, Any] = {}

    # ----- nodes -----------------------------------------------------------
    def add_node(self, node: Node) -> Node:
        self._g.add_node(node.id, obj=node)
        return node

    def has_node(self, node_id: str) -> bool:
        return node_id in self._g

    def node(self, node_id: str) -> Node:
        try:
            return self._g.nodes[node_id]["obj"]
        except KeyError as exc:
            raise KeyError(f"unknown node {node_id!r}") from exc

    def nodes(self, kind: NodeKind | Iterable[NodeKind] | None = None) -> list[Node]:
        if kind is None:
            return [d["obj"] for _, d in self._g.nodes(data=True)]
        kinds = {kind} if isinstance(kind, NodeKind) else set(kind)
        return [d["obj"] for _, d in self._g.nodes(data=True) if d["obj"].kind in kinds]

    def remove_node(self, node_id: str) -> None:
        self._g.remove_node(node_id)

    # ----- edges -----------------------------------------------------------
    def add_edge(self, edge: Edge) -> Edge:
        if edge.src not in self._g:
            raise KeyError(f"unknown source node {edge.src!r}")
        if edge.dst not in self._g:
            raise KeyError(f"unknown target node {edge.dst!r}")
        key = edge.kind.value
        if self._g.has_edge(edge.src, edge.dst, key=key):
            existing: Edge = self._g.edges[edge.src, edge.dst, key]["obj"]
            existing.count += edge.count
            if existing.line is None:
                existing.line = edge.line
            for k, v in edge.attrs.items():
                existing.attrs.setdefault(k, v)
            return existing
        self._g.add_edge(edge.src, edge.dst, key=key, obj=edge)
        return edge

    def has_edge_kind(self, src: str, dst: str, kind: EdgeKind) -> bool:
        return self._g.has_edge(src, dst, key=kind.value)

    def edges(self, kind: EdgeKind | Iterable[EdgeKind] | None = None) -> list[Edge]:
        kinds = self._kinds(kind)
        return [d["obj"] for _, _, k, d in self._g.edges(keys=True, data=True) if kinds is None or k in kinds]

    def out_edges(self, node_id: str, kind: EdgeKind | Iterable[EdgeKind] | None = None) -> list[Edge]:
        return self._pick(self._g.out_edges(node_id, keys=True, data=True), kind)

    def in_edges(self, node_id: str, kind: EdgeKind | Iterable[EdgeKind] | None = None) -> list[Edge]:
        return self._pick(self._g.in_edges(node_id, keys=True, data=True), kind)

    def _pick(self, edge_view, kind: EdgeKind | Iterable[EdgeKind] | None) -> list[Edge]:  # noqa: ANN001
        kinds = self._kinds(kind)
        return [d["obj"] for _, _, k, d in edge_view if kinds is None or k in kinds]

    def children(self, node_id: str) -> list[Node]:
        return [self.node(e.dst) for e in self.out_edges(node_id, EdgeKind.CONTAINS)]

    def parent(self, node_id: str) -> Node | None:
        parents = self.in_edges(node_id, EdgeKind.CONTAINS)
        return self.node(parents[0].src) if parents else None

    def has_external_base(self, cls_id: str) -> bool:
        """True when a class inherits, at any depth, from a package or an unresolved base: a framework may call its methods."""
        if self.node(cls_id).attrs.get("unresolved_bases"):
            return True
        for e in self.out_edges(cls_id, EdgeKind.INHERITS):
            t = self.node(e.dst)
            if t.kind is NodeKind.PACKAGE or (t.kind is NodeKind.CLASS and self.has_external_base(t.id)):
                return True
        return False

    def subclasses(self, cls_id: str) -> list[str]:
        """Every class that inherits from `cls_id`, directly or not."""
        seen = {cls_id}
        stack = [cls_id]
        while stack:
            for e in self.in_edges(stack.pop(), EdgeKind.INHERITS):
                if e.src not in seen:
                    seen.add(e.src)
                    stack.append(e.src)
        seen.discard(cls_id)
        return sorted(seen)

    @staticmethod
    def _kinds(kind: EdgeKind | Iterable[EdgeKind] | None) -> set[str] | None:
        if kind is None:
            return None
        if isinstance(kind, EdgeKind):
            return {kind.value}
        return {k.value for k in kind}

    # ----- views -----------------------------------------------------------
    def module_graph(self) -> nx.DiGraph:
        """Modules and packages connected by import edges."""
        view = nx.DiGraph()
        for n in self.nodes((NodeKind.MODULE, NodeKind.PACKAGE)):
            view.add_node(n.id, obj=n)
        for e in self.edges(EdgeKind.IMPORTS):
            if e.src in view and e.dst in view:
                view.add_edge(e.src, e.dst, obj=e)
        return view

    def call_graph(self, include_references: bool = False) -> nx.DiGraph:
        """Functions/methods/classes/modules connected by call (and optionally reference) edges."""
        kinds = [EdgeKind.CALLS] + ([EdgeKind.REFERENCES] if include_references else [])
        view = nx.DiGraph()
        for n in self.nodes():
            if n.kind is not NodeKind.VARIABLE:
                view.add_node(n.id, obj=n)
        for e in self.edges(kinds):
            if e.src in view and e.dst in view:
                if view.has_edge(e.src, e.dst):
                    view.edges[e.src, e.dst]["weight"] += e.count
                else:
                    view.add_edge(e.src, e.dst, obj=e, weight=e.count)
        return view

    @property
    def nx(self) -> nx.MultiDiGraph:
        return self._g

    # ----- serialization ---------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            "meta": self.meta,
            "nodes": [n.to_dict() for n in self.nodes()],
            "edges": [e.to_dict() for e in self.edges()],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProgramGraph:
        g = cls()
        g.meta = dict(data.get("meta", {}))
        for nd in data["nodes"]:
            g.add_node(Node.from_dict(nd))
        for ed in data["edges"]:
            g.add_edge(Edge.from_dict(ed))
        return g

    def __len__(self) -> int:
        return self._g.number_of_nodes()

    def __repr__(self) -> str:
        return f"ProgramGraph(nodes={self._g.number_of_nodes()}, edges={self._g.number_of_edges()})"
