from datetime import date
from typing import Optional

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel

TERMINAIS = {"ENTREGUE", "CANCELADO", "DEVOLVIDO"}

app = FastAPI(title="Rastreamento")
_rastreios = {}
_processados = set()


class ErroApi(Exception):
    def __init__(self, status, erro, mensagem):
        self.status, self.erro, self.mensagem = status, erro, mensagem


@app.exception_handler(ErroApi)
def _erro_api(_: Request, e: ErroApi):
    return JSONResponse({"erro": e.erro, "mensagem": e.mensagem}, status_code=e.status)


@app.exception_handler(RequestValidationError)
def _erro_validacao(_: Request, e: RequestValidationError):
    return JSONResponse({"erro": "DADOS_INVALIDOS", "mensagem": "dados inválidos"}, status_code=422)


class Evento(BaseModel):
    id: str
    tipo: str
    ocorrido_em: str
    dados: dict


@app.post("/eventos")
def receber(evento: Evento):
    if evento.id in _processados:
        return {"processado": False}
    d = evento.dados
    if evento.tipo == "envio.criado":
        _rastreios[d["codigo_rastreio"]] = {
            "codigo_rastreio": d["codigo_rastreio"],
            "envio_id": d["envio_id"],
            "pedido_id": d["pedido_id"],
            "transportadora_id": d["transportadora_id"],
            "data_prevista_entrega": d["data_prevista_entrega"],
            "status": "CRIADO",
            "historico": [{"status": "CRIADO", "data_hora": d["data_hora"], "local": None}],
        }
    elif evento.tipo == "envio.status_atualizado" and d.get("codigo_rastreio") in _rastreios:
        r = _rastreios[d["codigo_rastreio"]]
        r["status"] = d["status"]
        r["historico"].append({"status": d["status"], "data_hora": d["data_hora"], "local": d["local"]})
    else:
        return {"processado": False}
    _processados.add(evento.id)
    return {"processado": True}


@app.get("/rastreamento/{codigo_rastreio}")
def consultar(codigo_rastreio: str, data: Optional[date] = None):
    if codigo_rastreio not in _rastreios:
        raise ErroApi(404, "RASTREIO_NAO_ENCONTRADO", "código de rastreio não encontrado")
    r = _rastreios[codigo_rastreio]
    atrasado = (
        data is not None
        and r["status"] not in TERMINAIS
        and data > date.fromisoformat(r["data_prevista_entrega"])
    )
    return {**r, "entregue": r["status"] == "ENTREGUE", "atrasado": atrasado}


@app.post("/admin/reset", status_code=204)
def reset():
    _rastreios.clear()
    _processados.clear()
    return Response(status_code=204)
