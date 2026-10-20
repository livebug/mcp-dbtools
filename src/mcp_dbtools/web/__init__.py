"""Web 控制台（/ui）：零构建原生 HTML/JS 单页应用。

- 页面外壳与静态资源由本模块提供，不依赖 node 构建链，适配内网离线部署；
- 前端通过 ``/api/v1`` 取数，Token 存 sessionStorage（也可用 ``?token=`` 引导后自动清除 URL）；
- 路由使用 History API，``/ui/<section>`` 全部回落到同一份 index.html。
"""

from __future__ import annotations

from pathlib import Path

from starlette.responses import HTMLResponse, RedirectResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

STATIC_DIR = Path(__file__).parent / "static"


def register_web(app) -> None:
    index_file = STATIC_DIR / "index.html"

    async def shell(request) -> HTMLResponse:
        return HTMLResponse(index_file.read_text(encoding="utf-8"))

    async def to_ui(request) -> RedirectResponse:
        return RedirectResponse("/ui/", status_code=307)

    routes = [
        Route("/", to_ui),
        Route("/ui", to_ui),
        # 静态资源必须先于 SPA 回落路由注册
        Mount("/ui/assets", app=StaticFiles(directory=STATIC_DIR / "assets"), name="ui-assets"),
        Route("/ui/{section:path}", shell),
    ]
    app.router.routes[0:0] = routes
