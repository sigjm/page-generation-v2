from pagegen import agent


def nodes(graph):
    return [n for n in graph.get_graph().nodes if not n.startswith("__")]


def test_chat_graph_has_model_and_tools(page, tmp_path):
    assert nodes(agent.build_chat_graph(tmp_path, page, "", "", llm=None)) == ["model", "tools"]
