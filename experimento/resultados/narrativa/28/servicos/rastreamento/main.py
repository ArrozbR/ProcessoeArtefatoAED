# arquivo: servicos/rastreamento/main.py
import json
import re
from datetime import date
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI()

DATA_REGEX = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
STATUS_FINAIS = {"ENTREGUE", "CANCELADO", "DEVOLVIDO"}


class ErroApi(Exception):
    def __init__(self, status: int, codigo: str, mensagem: str):
        self.status = status
        self.codigo = codigo
        self.mensagem = mensagem


def resposta_erro(status: int, codigo: str, mensagem: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"erro": codigo, "mensagem": mensagem})


@app.exception_handler(ErroApi)
async def tratar_erro_api(request: Request, exc: ErroApi):
    return resposta_erro(exc.status, exc.codigo, exc.mensagem)


@app.exception_handler(RequestValidationError)
async def tratar_validacao(request: Request, exc: RequestValidationError):
    return resposta_erro(422, "DADOS_INVALIDOS", "Dados invalidos na requisicao.")


@app.exception_handler(StarletteHTTPException)
async def tratar_http(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        return resposta_erro(404, "NAO_ENCONTRADO", "Recurso nao encontrado.")
    if exc.status_code == 405:
        return resposta_erro(405, "METODO_NAO_PERMITIDO", "Metodo nao permitido.")
    return resposta_erro(exc.status_code, "ERRO", str(exc.detail))


rastreamentos = {}
eventos_processados = set()


def dados_invalidos():
    return ErroApi(422, "DADOS_INVALIDOS", "Dados invalidos na requisicao.")


async def ler_evento(request: Request) -> dict:
    try:
        corpo = await request.body()
        evento = json.loads(corpo)
    except Exception:
        raise dados_invalidos()
    if not isinstance(evento, dict):
        raise dados_invalidos()
    for campo in ("id", "tipo", "ocorrido_em", "dados"):
        if campo not in evento or evento[campo] is None:
            raise dados_invalidos()
    if not isinstance(evento["dados"], dict):
        raise dados_invalidos()
    if not isinstance(evento["id"], (str, int)) or not isinstance(evento["tipo"], str):
        raise dados_invalidos()
    return evento


@app.post("/admin/reset", status_code=204)
def reset():
    rastreamentos.clear()
    eventos_processados.clear()
    return Response(status_code=204)


@app.post("/eventos")
async def receber_evento(request: Request):
    evento = await ler_evento(request)
    chave = str(evento["id"])
    if chave in eventos_processados:
        return {"processado": False}
    tipo = evento["tipo"]
    dados = evento["dados"]

    if tipo == "envio.criado":
        codigo = dados.get("codigo_rastreio")
        rastreamentos[codigo] = {
            "codigo_rastreio": codigo,
            "envio_id": dados.get("envio_id"),
            "pedido_id": dados.get("pedido_id"),
            "transportadora_id": dados.get("transportadora_id"),
            "data_prevista_entrega": dados.get("data_prevista_entrega"),
            "historico": [{"status": "CRIADO", "data_hora": dados.get("data_hora"), "local": None}],
        }
        eventos_processados.add(chave)
        return {"processado": True}

    if tipo == "envio.status_atualizado":
        codigo = dados.get("codigo_rastreio")
        if codigo not in rastreamentos:
            return {"processado": False}
        rastreamentos[codigo]["historico"].append({
            "status": dados.get("status"),
            "data_hora": dados.get("data_hora"),
            "local": dados.get("local"),
        })
        eventos_processados.add(chave)
        return {"processado": True}

    return {"processado": False}


@app.get("/rastreamento/{codigo_rastreio}")
def obter_rastreamento(codigo_rastreio: str, data: Optional[str] = None):
    data_ref = None
    if data is not None:
        if not DATA_REGEX.fullmatch(data):
            raise dados_invalidos()
        try:
            data_ref = date.fromisoformat(data)
        except ValueError:
            raise dados_invalidos()
    if codigo_rastreio not in rastreamentos:
        raise ErroApi(404, "RASTREIO_NAO_ENCONTRADO", "Codigo de rastreio nao encontrado.")
    r = rastreamentos[codigo_rastreio]
    status = r["historico"][-1]["status"]
    atrasado = False
    if data_ref is not None and status not in STATUS_FINAIS:
        prevista = r["data_prevista_entrega"]
        try:
            atrasado = data_ref > date.fromisoformat(prevista)
        except (TypeError, ValueError):
            atrasado = False
    return {
        "codigo_rastreio": r["codigo_rastreio"],
        "envio_id": r["envio_id"],
        "pedido_id": r["pedido_id"],
        "transportadora_id": r["transportadora_id"],
        "data_prevista_entrega": r["data_prevista_entrega"],
        "status": status,
        "entregue": status == "ENTREGUE",
        "atrasado": atrasado,
        "historico": r["historico"],
    }
