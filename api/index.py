import traceback

try:
    from web import app
except Exception:
    from fastapi import FastAPI
    from fastapi.responses import PlainTextResponse

    app = FastAPI()

    @app.get("/{path:path}")
    async def error():
        return PlainTextResponse(traceback.format_exc(), status_code=500)
