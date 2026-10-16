"""FastAPI app (spec §7): upload → draft, chat, undo, preview HTML, files, export."""
import copy
import os
import threading
from pathlib import Path

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel

from . import agent, render, store
from .llm import LLMError

# Real product notes and copy never leave the Mac unless the user opts in (spec 2026-10-16-langchain-adoption §4).
if os.environ.get("PAGEGEN_TRACE") != "1":
    os.environ["LANGSMITH_TRACING"] = os.environ["LANGCHAIN_TRACING_V2"] = "false"

app = FastAPI(title="pagegen")
STATIC = Path(__file__).resolve().parent.parent / "static"
# ponytail: one model job at a time for the single local user; per-project locks if this ever serves more people
_model_lock = threading.Lock()


def _error(status: int, message: str) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)


@app.exception_handler(store.NotFound)
def _not_found(request, exc):
    return _error(404, "없는 프로젝트나 파일입니다.")


@app.exception_handler(store.BadInput)
def _bad_input(request, exc):
    return _error(400, str(exc))


@app.exception_handler(RequestValidationError)
def _invalid_request(request, exc):
    return _error(400, "입력이 올바르지 않습니다. 사진·상품명을 확인해 주세요.")


@app.exception_handler(store.NothingToUndo)
def _nothing_to_undo(request, exc):
    return _error(409, "되돌릴 변경이 없습니다.")


@app.exception_handler(LLMError)
def _model_error(request, exc):
    return _error(502, str(exc))


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.post("/projects")
def create_project(photos: list[UploadFile] = File(...), name: str = Form(...), notes: str = Form("")):
    if not name.strip():
        raise store.BadInput("상품명을 입력해 주세요.")
    pid, records = store.create([f.file.read(store.MAX_BYTES + 1) for f in photos])
    product = {"name": name.strip(), "notes": notes.strip()}
    with _model_lock:
        page, message = agent.make_draft(store.path(pid), pid, product, records)
    store.save(pid, page, [message])
    return {"id": pid, "version": page["version"], "messages": [message]}


@app.get("/projects/{pid}")
def get_project(pid: str):
    page, chat = store.load(pid)
    return {"page": page, "chat": chat}


class ChatIn(BaseModel):
    text: str


@app.post("/projects/{pid}/chat")
def post_chat(pid: str, body: ChatIn):
    text = body.text.strip()
    if not text:
        raise store.BadInput("메시지를 입력해 주세요.")
    with _model_lock:
        page, chat = store.load(pid)
        before = copy.deepcopy(page)
        try:
            reply, changed = agent.chat_turn(store.path(pid), page, chat, text)
        except LLMError:
            store.save_chat(pid, chat)  # keep the user's message; page.json stays as it was (spec §7)
            raise
        if changed:
            page["version"] += 1
        store.save(pid, page, chat, snapshot=before if changed else None)
    return {"reply": reply, "version": page["version"]}


@app.post("/projects/{pid}/undo")
def post_undo(pid: str):
    with _model_lock:
        return {"version": store.undo(pid)}


@app.get("/projects/{pid}/page", response_class=HTMLResponse)
def get_page(pid: str):
    page, _ = store.load(pid)
    html = render.render_html(page, lambda file: f"/projects/{pid}/{file}")
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


@app.get("/projects/{pid}/photos/{name}")
def get_photo(pid: str, name: str):
    return FileResponse(store.served_file(pid, f"photos/{name}"))


@app.get("/projects/{pid}/export/{rest:path}")
def get_export_file(pid: str, rest: str):
    return FileResponse(store.served_file(pid, f"export/{rest}"), headers={"Cache-Control": "no-store"})


@app.post("/projects/{pid}/export")
def post_export(pid: str, request: Request):
    store.load(pid)
    folder = store.path(pid)
    files = render.export(f"{request.base_url}projects/{pid}/page", folder / "export")

    def url(file: Path) -> str:
        return f"/projects/{pid}/{file.relative_to(folder).as_posix()}"

    return {"png": url(files["png"]), "jpg": url(files["jpg"]), "sections": [url(f) for f in files["sections"]]}


def main() -> None:
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
