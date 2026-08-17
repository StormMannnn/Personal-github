import asyncio

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph
from typing import TypedDict

from ppi_loop.runner import stable_thread_id


class State(TypedDict):
    value: int
    terminal_status: str


def test_sqlite_checkpoint_survives_reopen(tmp_path):
    async def run():
        path = tmp_path / "checkpoint.sqlite"
        builder = StateGraph(State)
        builder.add_node("finish", lambda state: {"value": state["value"] + 1,
                                                   "terminal_status": "VERIFIED"})
        builder.add_edge(START, "finish")
        builder.add_edge("finish", END)
        cfg = {"configurable": {"thread_id": "image-1"}}
        async with AsyncSqliteSaver.from_conn_string(str(path)) as saver:
            graph = builder.compile(checkpointer=saver)
            result = await graph.ainvoke({"value": 1, "terminal_status": ""}, config=cfg)
            assert result["value"] == 2
        async with AsyncSqliteSaver.from_conn_string(str(path)) as saver:
            graph = builder.compile(checkpointer=saver)
            snapshot = await graph.aget_state(cfg)
            assert snapshot.values["terminal_status"] == "VERIFIED"
            assert snapshot.next == ()
    asyncio.run(run())


def test_thread_id_changes_when_mapping_or_loop_input_changes(monkeypatch):
    first = stable_thread_id("url", "brand", "title", ["A"], {"A": "X"})
    assert first == stable_thread_id("url", "brand", "title", ["A"], {"A": "X"})
    assert first != stable_thread_id("url", "brand", "title", ["A"], {"A": "Y"})

