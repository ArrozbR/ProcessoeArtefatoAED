# arquivo: servicos/notificacoes/main.py
import copy
import threading
from typing import Any, Dict

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, StrictStr
from starlette.exceptions import HTTPException as StarletteHTTPException


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
    return JSONResponse(status_code=422, content={"erro": "DADOS_INVALIDOS", "mensagem": "Dados inválidos na requisição."})


@app.exception_handler(StarletteHTTPException)
async def _tratar_http(request: Request, exc: StarletteHTTPException):
    codigos = {404: "NAO_ENCONTRADO", 405: "METODO_NAO_PERMITIDO"}
    return JSONResponse(
        status_code=exc.status_code,
        content={"erro": codigos.get(exc.status_code, "ERRO"), "mensagem": str(exc.detail)},
    )


MENSAGENS_STATUS = {
    "SAIU_PARA_ENTREGA": "Pedido {pedido_id}: saiu para entrega.",
    "ENTREGUE": "Pedido {pedido_id}: entregue.",
    "FALHA_ENTREGA": "Pedido {pedido_id}: tentativa de entrega sem sucesso.",
    "DEVOLVIDO": "Pedido {pedido_id}: devolvido ao remetente.",
    "CANCELADO": "Pedido {pedido_id}: envio cancelado.",
}

_lock = threading.Lock()
_notificacoes: list = []
_processados: set = set()
_estado = {"contador": 0}


class Evento(BaseModel):
    id: StrictStr
    tipo: StrictStr
    ocorrido_em: StrictStr
    dados: Dict[str, Any]


def _exigir(dados: dict, campos) -> None:
    for c in campos:
        if c not in dados:
            raise ErroAPI(422, "DADOS_INVALIDOS", "Dados do evento inválidos.")


def _registrar(email, pedido_id, codigo, tipo, mensagem, criada_em) -> None:
    _estado["contador"] += 1
    _notificacoes.append(
        {
            "id": "NOT-%06d" % _estado["contador"],
            "email": email,
            "pedido_id": pedido_id,
            "codigo_rastreio": codigo,
            "tipo": tipo,
            "mensagem": mensagem,
            "criada_em": criada_em,
        }
    )


@app.post("/admin/reset", status_code=204)
def reset():
    with _lock:
        _notificacoes.clear()
        _processados.clear()
        _estado["contador"] = 0
    return Response(status_code=204)


@app.post("/eventos")
def receber_evento(evento: Evento):
    with _lock:
        if evento.id in _processados:
            return {"processado": False}
        d = evento.dados
        if evento.tipo == "envio.criado":
            _exigir(d, ["pedido_id", "codigo_rastreio", "destinatario_email"])
            mensagem = "Pedido {pedido_id}: envio criado. Rastreio: {codigo}.".format(
                pedido_id=d["pedido_id"], codigo=d["codigo_rastreio"]
            )
            _registrar(d["destinatario_email"], d["pedido_id"], d["codigo_rastreio"], "CRIADO", mensagem, evento.ocorrido_em)
            _processados.add(evento.id)
            return {"processado": True}
        if evento.tipo == "envio.status_atualizado":
            _exigir(d, ["pedido_id", "codigo_rastreio", "status", "destinatario_email"])
            modelo = MENSAGENS_STATUS.get(d["status"])
            if modelo is not None:
                mensagem = modelo.format(pedido_id=d["pedido_id"])
                _registrar(
                    d["destinatario_email"], d["pedido_id"], d["codigo_rastreio"], d["status"], mensagem, evento.ocorrido_em
                )
            _processados.add(evento.id)
            return {"processado": True}
        return {"processado": False}


@app.get("/notificacoes")
def listar_notificacoes(email: str = Query(...)):
    with _lock:
        return [copy.deepcopy(n) for n in _notificacoes if n["email"] == email]
