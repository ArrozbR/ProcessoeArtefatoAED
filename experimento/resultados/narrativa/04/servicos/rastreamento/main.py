# arquivo: servicos/rastreamento/main.py
import copy
import re
import threading
from datetime import date
from typing import Any, Dict, Optional

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, StrictStr
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI(title="Rastreamento")

STATUS_FINAIS_SEM_ATRASO = {"ENTREGUE", "CANCELADO", "DEVOLVIDO"}

_lock = threading.Lock()
_rastreamentos = {}
_eventos_processados = set()


def erro(status, codigo, mensagem):
    return JSONResponse(status_code=status, content={"erro": codigo, "mensagem": mensagem})


def _descrever_erros(exc):
    partes = []
    try:
        for e in exc.errors():
            loc = ".".join(str(p) for p in e.get("loc", ()))
            partes.append(f"{loc}: {e.get('msg', '')}")
    except Exception:
        pass
    return "Dados inválidos" + (": " + "; ".join(partes) if partes else ".")


@app.exception_handler(RequestValidationError)
async def _validacao(request: Request, exc: RequestValidationError):
    return erro(422, "DADOS_INVALIDOS", _descrever_erros(exc))


@app.exception_handler(StarletteHTTPException)
async def _http(request: Request, exc: StarletteHTTPException):
    codigo = "RECURSO_NAO_ENCONTRADO" if exc.status_code == 404 else "ERRO_HTTP"
    return erro(exc.status_code, codigo, str(exc.detail))


class Evento(BaseModel):
    id: StrictStr
    tipo: StrictStr
    ocorrido_em: StrictStr
    dados: Dict[str, Any]


@app.post("/admin/reset")
def reset():
    with _lock:
        _rastreamentos.clear()
        _eventos_processados.clear()
    return Response(status_code=204)


@app.post("/eventos")
def receber_evento(evento: Evento):
    with _lock:
        if evento.id in _eventos_processados:
            return {"processado": False}
        dados = evento.dados
        if evento.tipo == "envio.criado":
            codigo = dados.get("codigo_rastreio")
            if not isinstance(codigo, str):
                return {"processado": False}
            _rastreamentos[codigo] = {
                "codigo_rastreio": codigo,
                "envio_id": dados.get("envio_id"),
                "pedido_id": dados.get("pedido_id"),
                "transportadora_id": dados.get("transportadora_id"),
                "data_prevista_entrega": dados.get("data_prevista_entrega"),
                "historico": [{"status": "CRIADO", "data_hora": dados.get("data_hora"), "local": None}],
            }
            _eventos_processados.add(evento.id)
            return {"processado": True}
        if evento.tipo == "envio.status_atualizado":
            codigo = dados.get("codigo_rastreio")
            r = _rastreamentos.get(codigo) if isinstance(codigo, str) else None
            if r is None:
                return {"processado": False}
            r["historico"].append({
                "status": dados.get("status"),
                "data_hora": dados.get("data_hora"),
                "local": dados.get("local"),
            })
            _eventos_processados.add(evento.id)
            return {"processado": True}
        return {"processado": False}


def _data_valida(v):
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", v):
        return False
    try:
        date.fromisoformat(v)
    except ValueError:
        return False
    return True


@app.get("/rastreamento/{codigo_rastreio}")
def obter_rastreamento(codigo_rastreio: str, data: Optional[str] = None):
    if data is not None and not _data_valida(data):
        return erro(422, "DADOS_INVALIDOS", "data deve estar no formato AAAA-MM-DD.")
    with _lock:
        r = _rastreamentos.get(codigo_rastreio)
        if r is None:
            return erro(404, "RASTREIO_NAO_ENCONTRADO", f"Rastreio {codigo_rastreio} não encontrado.")
        historico = copy.deepcopy(r["historico"])
        status = historico[-1]["status"] if historico else None
        atrasado = False
        prevista = r["data_prevista_entrega"]
        if data is not None and status not in STATUS_FINAIS_SEM_ATRASO and isinstance(prevista, str):
            atrasado = data > prevista
        return JSONResponse(status_code=200, content={
            "codigo_rastreio": r["codigo_rastreio"],
            "envio_id": r["envio_id"],
            "pedido_id": r["pedido_id"],
            "transportadora_id": r["transportadora_id"],
            "data_prevista_entrega": prevista,
            "status": status,
            "entregue": status == "ENTREGUE",
            "atrasado": atrasado,
            "historico": historico,
        })
