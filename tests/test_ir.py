"""Tests for the IR: nodes, edges and the program graph."""

import pytest

from vibepiler.ir.model import Edge, EdgeKind, Node, NodeKind
from vibepiler.ir.graph import ProgramGraph


def make_graph() -> ProgramGraph:
    g = ProgramGraph()
    g.add_node(Node(id="mod:a", kind=NodeKind.MODULE, name="a", file="a.py", language="python"))
    g.add_node(Node(id="mod:b", kind=NodeKind.MODULE, name="b", file="b.py", language="python"))
    g.add_node(Node(id="sym:a::f", kind=NodeKind.FUNCTION, name="f", qualname="f", module="mod:a", file="a.py", language="python", start_line=1, end_line=3))
    g.add_node(Node(id="sym:b::g", kind=NodeKind.FUNCTION, name="g", qualname="g", module="mod:b", file="b.py", language="python", start_line=1, end_line=3))
    g.add_node(Node(id="pkg:requests", kind=NodeKind.PACKAGE, name="requests"))
    g.add_edge(Edge(src="mod:a", dst="sym:a::f", kind=EdgeKind.CONTAINS))
    g.add_edge(Edge(src="mod:b", dst="sym:b::g", kind=EdgeKind.CONTAINS))
    g.add_edge(Edge(src="mod:a", dst="mod:b", kind=EdgeKind.IMPORTS))
    g.add_edge(Edge(src="mod:a", dst="pkg:requests", kind=EdgeKind.IMPORTS))
    g.add_edge(Edge(src="sym:a::f", dst="sym:b::g", kind=EdgeKind.CALLS, line=2))
    return g


def test_nodes_are_retrievable_by_id():
    g = make_graph()
    assert g.node("sym:a::f").name == "f"
    assert g.node("sym:a::f").kind is NodeKind.FUNCTION


def test_nodes_can_be_filtered_by_kind():
    g = make_graph()
    assert {n.id for n in g.nodes(kind=NodeKind.MODULE)} == {"mod:a", "mod:b"}
    assert {n.id for n in g.nodes(kind=NodeKind.PACKAGE)} == {"pkg:requests"}


def test_adding_same_edge_twice_increments_count_instead_of_duplicating():
    g = make_graph()
    g.add_edge(Edge(src="sym:a::f", dst="sym:b::g", kind=EdgeKind.CALLS, line=3))
    calls = g.edges(kind=EdgeKind.CALLS)
    assert len(calls) == 1
    assert calls[0].count == 2


def test_out_and_in_edges_filtered_by_kind():
    g = make_graph()
    assert [e.dst for e in g.out_edges("mod:a", kind=EdgeKind.IMPORTS)] == ["mod:b", "pkg:requests"]
    assert [e.src for e in g.in_edges("sym:b::g", kind=EdgeKind.CALLS)] == ["sym:a::f"]
    assert g.out_edges("mod:a", kind=EdgeKind.CALLS) == []


def test_module_graph_view_contains_only_import_edges_between_modules_and_packages():
    g = make_graph()
    mg = g.module_graph()
    assert set(mg.nodes) == {"mod:a", "mod:b", "pkg:requests"}
    assert set(mg.edges) == {("mod:a", "mod:b"), ("mod:a", "pkg:requests")}


def test_call_graph_view_contains_only_call_edges():
    g = make_graph()
    cg = g.call_graph()
    assert set(cg.edges) == {("sym:a::f", "sym:b::g")}


def test_children_of_module_are_contained_symbols():
    g = make_graph()
    assert [n.id for n in g.children("mod:a")] == ["sym:a::f"]


def test_graph_round_trips_through_dict():
    g = make_graph()
    data = g.to_dict()
    g2 = ProgramGraph.from_dict(data)
    assert {n.id for n in g2.nodes()} == {n.id for n in g.nodes()}
    assert len(g2.edges()) == len(g.edges())
    assert g2.node("sym:a::f").start_line == 1
    assert g2.edges(kind=EdgeKind.CALLS)[0].line == 2


def test_node_attrs_are_free_form_and_persist():
    g = make_graph()
    g.node("sym:a::f").attrs["complexity"] = 7
    assert ProgramGraph.from_dict(g.to_dict()).node("sym:a::f").attrs["complexity"] == 7


def test_unknown_node_raises_key_error():
    g = make_graph()
    with pytest.raises(KeyError):
        g.node("nope")


def test_edge_to_unknown_node_raises():
    g = make_graph()
    with pytest.raises(KeyError):
        g.add_edge(Edge(src="mod:a", dst="sym:missing", kind=EdgeKind.CALLS))
