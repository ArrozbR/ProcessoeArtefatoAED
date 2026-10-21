# arquivo: servicos/rastreamento/main.py
import copy
import json
import re
import threading
from datetime import date
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI()

RE_DATA = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
TIPOS_CONHECIDOS = {"envio.criado", "envio.status_atualizado"}
FINAIS = {"ENTREGUE", "CANCELADO", "DEVOLVIDO"}


class ErroAPI(Exception):
    def __init__(self, status: int, codigo: str, mensagem: str):
        self.status = status
        self.codigo = codigo
        self.mensagem = mensagem


@app.exception_handler(ErroAPI)
async def _erro_api(request: Request, exc: ErroAPI):
    return JSONResponse(status_code=exc.status, content={"erro": exc.codigo, "mensagem": exc.mensagem})


@app.exception_handler(RequestValidationError)
async def _erro_validacao(request: Request, exc: RequestValidationError):
    return JSONResponse(status_code=422, content={"erro": "DADOS_INVALIDOS", "mensagem": "Dados inválidos."})


@app.exception_handler(StarletteHTTPException)
async def _erro_http(request: Request, exc: StarletteHTTPException):
    codigo = "NAO_ENCONTRADO" if exc.status_code == 404 else "ERRO_HTTP"
    return JSONResponse(status_code=exc.status_code, content={"erro": codigo, "mensagem": str(exc.detail)})


_lock = threading.Lock()
_rastreios = {}
_eventos_processados = set()


def _invalido():
    return ErroAPI(422, "DADOS_INVALIDOS", "Dados inválidos.")


@app.post("/admin/reset", status_code=204)
def reset():
    with _lock:
        _rastreios.clear()
        _eventos_processados.clear()
    return Response(status_code=204)


@app.post("/eventos")
async def receber_evento(request: Request):
    try:
        corpo = json.loads(await request.body())
    except Exception:
        raise _invalido()
    if not isinstance(corpo, dict):
        raise _invalido()
    for campo in ("id", "tipo", "ocorrido_em", "dados"):
        if corpo.get(campo) is None:
            raise _invalido()
    if not isinstance(corpo["dados"], dict):
        raise _invalido()
    try:
        id_evento = corpo["id"] if isinstance(corpo["id"], str) else json.dumps(corpo["id"], sort_keys=True)
    except Exception:
        raise _invalido()
    tipo = corpo["tipo"]
    dados = corpo["dados"]

    with _lock:
        if id_evento in _eventos_processados or tipo not in TIPOS_CONHECIDOS:
            return {"processado": False}

        if tipo == "envio.criado":
            codigo = dados.get("codigo_rastreio")
            if codigo is None:
                return {"processado": False}
            _rastreios[codigo] = {
                "codigo_rastreio": codigo,
                "envio_id": dados.get("envio_id"),
                "pedido_id": dados.get("pedido_id"),
                "transportadora_id": dados.get("transportadora_id"),
                "data_prevista_entrega": dados.get("data_prevista_entrega"),
                "historico": [{
                    "status": "CRIADO",
                    "data_hora": dados.get("data_hora", corpo["ocorrido_em"]),
                    "local": None,
                }],
            }
        else:
            codigo = dados.get("codigo_rastreio")
            r = _rastreios.get(codigo) if isinstance(codigo, str) else None
            if r is None:
                return {"processado": False}
            r["historico"].append({
                "status": dados.get("status"),
                "data_hora": dados.get("data_hora", corpo["ocorrido_em"]),
                "local": dados.get("local"),
            })

        _eventos_processados.add(id_evento)
        return {"processado": True}


@app.get("/rastreamento/{codigo_rastreio}")
def consultar(codigo_rastreio: str, data: Optional[str] = None):
    data_ref = None
    if data is not None:
        if not RE_DATA.fullmatch(data):
            raise _invalido()
        try:
            data_ref = date.fromisoformat(data)
        except ValueError:
            raise _invalido()

    with _lock:
        r = _rastreios.get(codigo_rastreio)
        if r is None:
            raise ErroAPI(404, "RASTREIO_NAO_ENCONTRADO", "Código de rastreio não encontrado.")
        status = r["historico"][-1]["status"]
        atrasado = False
        if data_ref is not None and status not in FINAIS:
            try:
                prevista = date.fromisoformat(r["data_prevista_entrega"])
                atrasado = data_ref > prevista
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
            "historico": copy.deepcopy(r["historico"]),
        }
