# arquivo: servicos/notificacoes/main.py
import threading
from typing import Any, Dict

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, StrictStr
from starlette.exceptions import HTTPException as StarletteHTTPException

MENSAGENS_STATUS = {
    "SAIU_PARA_ENTREGA": "Pedido {pedido_id}: saiu para entrega.",
    "ENTREGUE": "Pedido {pedido_id}: entregue.",
    "FALHA_ENTREGA": "Pedido {pedido_id}: tentativa de entrega sem sucesso.",
    "DEVOLVIDO": "Pedido {pedido_id}: devolvido ao remetente.",
    "CANCELADO": "Pedido {pedido_id}: envio cancelado.",
}
MENSAGEM_CRIADO = "Pedido {pedido_id}: envio criado. Rastreio: {codigo_rastreio}."


class ErroAPI(Exception):
    def __init__(self, status: int, codigo: str, mensagem: str):
        super().__init__(mensagem)
        self.status = status
        self.codigo = codigo
        self.mensagem = mensagem


app = FastAPI()

_lock = threading.Lock()
_estado = {"contador": 0, "notificacoes": [], "processados": set()}


def _erro(status: int, codigo: str, mensagem: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"erro": codigo, "mensagem": mensagem})


@app.exception_handler(ErroAPI)
async def _tratar_erro_api(request: Request, exc: ErroAPI):
    return _erro(exc.status, exc.codigo, exc.mensagem)


@app.exception_handler(RequestValidationError)
async def _tratar_validacao(request: Request, exc: RequestValidationError):
    return _erro(422, "DADOS_INVALIDOS", "Dados invalidos na requisicao.")


@app.exception_handler(StarletteHTTPException)
async def _tratar_http(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        return _erro(404, "NAO_ENCONTRADO", "Recurso nao encontrado.")
    if exc.status_code == 405:
        return _erro(405, "METODO_NAO_PERMITIDO", "Metodo nao permitido.")
    return _erro(exc.status_code, "ERRO", str(exc.detail))


class EventoEntrada(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: StrictStr
    tipo: StrictStr
    ocorrido_em: StrictStr
    dados: Dict[str, Any]


def _registrar(evento: EventoEntrada, tipo: str, mensagem: str) -> None:
    numero = _estado["contador"] + 1
    dados = evento.dados
    _estado["notificacoes"].append(
        {
            "id": "NOT-%06d" % numero,
            "email": dados.get("destinatario_email"),
            "pedido_id": dados.get("pedido_id"),
            "codigo_rastreio": dados.get("codigo_rastreio"),
            "tipo": tipo,
            "mensagem": mensagem,
            "criada_em": evento.ocorrido_em,
        }
    )
    _estado["contador"] = numero


@app.post("/admin/reset")
def reset():
    with _lock:
        _estado["contador"] = 0
        _estado["notificacoes"].clear()
        _estado["processados"].clear()
    return Response(status_code=204)


@app.post("/eventos")
def receber_evento(evento: EventoEntrada):
    with _lock:
        if evento.id in _estado["processados"]:
            return {"processado": False}
        dados = evento.dados
        pedido_id = dados.get("pedido_id")
        if evento.tipo == "envio.criado":
            mensagem = MENSAGEM_CRIADO.format(pedido_id=pedido_id, codigo_rastreio=dados.get("codigo_rastreio"))
            _registrar(evento, "CRIADO", mensagem)
            _estado["processados"].add(evento.id)
            return {"processado": True}
        if evento.tipo == "envio.status_atualizado":
            status = dados.get("status")
            modelo = MENSAGENS_STATUS.get(status) if isinstance(status, str) else None
            if modelo is not None:
                _registrar(evento, status, modelo.format(pedido_id=pedido_id))
            _estado["processados"].add(evento.id)
            return {"processado": True}
        return {"processado": False}


@app.get("/notificacoes")
def listar_notificacoes(email: str):
    with _lock:
        lista = [dict(n) for n in _estado["notificacoes"] if n["email"] == email]
    lista.sort(key=lambda n: n["id"])
    return lista
