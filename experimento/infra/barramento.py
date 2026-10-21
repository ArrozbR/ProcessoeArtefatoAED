"""Barramento de eventos fornecido pronto (não faz parte do código gerado pela LLM).

Repassa cada evento publicado, em ordem e de forma síncrona, para POST {url}/eventos de cada
assinante listado na variável ASSINANTES (URLs separadas por vírgula). Guarda um registro dos
eventos publicados em GET /eventos, usado pelos testes.
"""
import os

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

app = FastAPI(title="Barramento")
_publicados = []


@app.post("/publicar", status_code=202)
async def publicar(request: Request):
    evento = await request.json()
    _publicados.append(evento)
    assinantes = [u.strip() for u in os.environ.get("ASSINANTES", "").split(",") if u.strip()]
    entregues = 0
    async with httpx.AsyncClient(timeout=10) as cliente:
        for url in assinantes:
            resposta = await cliente.post(f"{url}/eventos", json=evento)
            if resposta.status_code < 300:
                entregues += 1
    return JSONResponse({"entregue_para": entregues}, status_code=202)


@app.get("/eventos")
def eventos():
    return _publicados


@app.post("/admin/reset", status_code=204)
def reset():
    _publicados.clear()
    return Response(status_code=204)
