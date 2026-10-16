from pagegen import agent


def nodes(graph):
    return [n for n in graph.get_graph().nodes if not n.startswith("__")]


def test_chat_graph_has_model_and_tools(page, tmp_path):
    assert nodes(agent.build_chat_graph(tmp_path, page, "", "", llm=None)) == ["model", "tools"]


def test_draft_graph_runs_the_fixed_order(tmp_path):
    assert agent.DRAFT_STEPS == ("analyze", "caption", "write_copy", "assemble", "check", "edit_scene", "first_message")
    assert nodes(agent.build_draft_graph(tmp_path, llm=None)) == list(agent.DRAFT_STEPS)
