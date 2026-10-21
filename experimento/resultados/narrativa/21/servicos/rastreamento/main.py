# arquivo: servicos/rastreamento/main.py
import copy
import re
import threading
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

RE_DATA = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
STATUS_FINAIS_SEM_ATRASO = {"ENTREGUE", "CANCELADO", "DEVOLVIDO"}


class ErroAPI(Exception):
    def __init__(self, status: int, codigo: str, mensagem: str):
        super().__init__(mensagem)
        self.status = status
        self.codigo = codigo
        self.mensagem = mensagem


app = FastAPI()


@app.exception_handler(ErroAPI)
async def _tratar_erro_api(request: Request, exc: ErroAPI):
    return JSONResponse(status_code=exc.status, content={"erro": exc.codigo, "mensagem": exc.mensagem})


@app.exception_handler(RequestValidationError)
async def _tratar_validacao(request: Request, exc: RequestValidationError):
    return JSONResponse(status_code=422, content={"erro": "DADOS_INVALIDOS", "mensagem": "Dados inválidos."})


@app.exception_handler(StarletteHTTPException)
async def _tratar_http(request: Request, exc: StarletteHTTPException):
    codigo = "NAO_ENCONTRADO" if exc.status_code == 404 else "ERRO_HTTP"
    return JSONResponse(status_code=exc.status_code, content={"erro": codigo, "mensagem": str(exc.detail)})


_lock = threading.Lock()
_rastreamentos = {}
_eventos_processados = set()


def _dados_invalidos():
    return ErroAPI(422, "DADOS_INVALIDOS", "Dados inválidos.")


def _parse_data(v: str):
    if not RE_DATA.fullmatch(v):
        raise ValueError("data inválida")
    return datetime.strptime(v, "%Y-%m-%d").date()


@app.post("/admin/reset", status_code=204)
def reset():
    with _lock:
        _rastreamentos.clear()
        _eventos_processados.clear()
    return Response(status_code=204)


@app.post("/eventos")
async def receber_evento(request: Request):
    try:
        corpo = await request.json()
    except Exception:
        raise _dados_invalidos()
    if not isinstance(corpo, dict):
        raise _dados_invalidos()
    for campo in ("id", "tipo", "ocorrido_em", "dados"):
        if corpo.get(campo) is None:
            raise _dados_invalidos()
    evento_id = corpo["id"]
    tipo = corpo["tipo"]
    dados = corpo["dados"]
    if not isinstance(evento_id, str) or not isinstance(tipo, str) or not isinstance(dados, dict):
        raise _dados_invalidos()

    with _lock:
        if evento_id in _eventos_processados:
            return {"processado": False}

        if tipo == "envio.criado":
            codigo = dados.get("codigo_rastreio")
            _rastreamentos[codigo] = {
                "codigo_rastreio": codigo,
                "envio_id": dados.get("envio_id"),
                "pedido_id": dados.get("pedido_id"),
                "transportadora_id": dados.get("transportadora_id"),
                "data_prevista_entrega": dados.get("data_prevista_entrega"),
                "historico": [{"status": "CRIADO", "data_hora": dados.get("data_hora"), "local": None}],
            }
            _eventos_processados.add(evento_id)
            return {"processado": True}

        if tipo == "envio.status_atualizado":
            codigo = dados.get("codigo_rastreio")
            r = _rastreamentos.get(codigo) if isinstance(codigo, str) else None
            if r is None:
                return {"processado": False}
            r["historico"].append({
                "status": dados.get("status"),
                "data_hora": dados.get("data_hora"),
                "local": dados.get("local"),
            })
            _eventos_processados.add(evento_id)
            return {"processado": True}

        return {"processado": False}


@app.get("/rastreamento/{codigo_rastreio}")
def obter_rastreamento(codigo_rastreio: str, data: Optional[str] = None):
    data_ref = None
    if data is not None:
        try:
            data_ref = _parse_data(data)
        except ValueError:
            raise _dados_invalidos()

    with _lock:
        r = _rastreamentos.get(codigo_rastreio)
        if r is None:
            raise ErroAPI(404, "RASTREIO_NAO_ENCONTRADO", "Rastreio não encontrado.")
        r = copy.deepcopy(r)

    status = r["historico"][-1]["status"] if r["historico"] else "CRIADO"
    atrasado = False
    if data_ref is not None and status not in STATUS_FINAIS_SEM_ATRASO:
        try:
            prevista = datetime.strptime(r["data_prevista_entrega"], "%Y-%m-%d").date()
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
        "historico": r["historico"],
    }
