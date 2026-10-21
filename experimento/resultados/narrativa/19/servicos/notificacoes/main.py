# arquivo: servicos/notificacoes/main.py
import copy
import threading

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, StrictStr
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI(title="Notificacoes")

MENSAGENS_STATUS = {
    "SAIU_PARA_ENTREGA": "Pedido {pedido_id}: saiu para entrega.",
    "ENTREGUE": "Pedido {pedido_id}: entregue.",
    "FALHA_ENTREGA": "Pedido {pedido_id}: tentativa de entrega sem sucesso.",
    "DEVOLVIDO": "Pedido {pedido_id}: devolvido ao remetente.",
    "CANCELADO": "Pedido {pedido_id}: envio cancelado.",
}
MENSAGEM_CRIADO = "Pedido {pedido_id}: envio criado. Rastreio: {codigo_rastreio}."

_lock = threading.Lock()
_notificacoes = []
_eventos_processados = set()
_estado = {"contador": 0}


class ErroAPI(Exception):
    def __init__(self, status: int, codigo: str, mensagem: str):
        super().__init__(mensagem)
        self.status = status
        self.codigo = codigo
        self.mensagem = mensagem


def _resposta_erro(status: int, codigo: str, mensagem: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"erro": codigo, "mensagem": mensagem})


@app.exception_handler(ErroAPI)
async def _tratar_erro_api(request: Request, exc: ErroAPI):
    return _resposta_erro(exc.status, exc.codigo, exc.mensagem)


@app.exception_handler(RequestValidationError)
async def _tratar_validacao(request: Request, exc: RequestValidationError):
    return _resposta_erro(422, "DADOS_INVALIDOS", "Dados inválidos na requisição.")


@app.exception_handler(StarletteHTTPException)
async def _tratar_http(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        return _resposta_erro(404, "NAO_ENCONTRADO", "Recurso não encontrado.")
    if exc.status_code == 405:
        return _resposta_erro(405, "METODO_NAO_PERMITIDO", "Método não permitido.")
    return _resposta_erro(exc.status_code, "ERRO", str(exc.detail))


@app.exception_handler(Exception)
async def _tratar_inesperado(request: Request, exc: Exception):
    return _resposta_erro(500, "ERRO_INTERNO", "Erro interno.")


class EventoIn(BaseModel):
    id: StrictStr
    tipo: StrictStr
    ocorrido_em: StrictStr
    dados: dict


def _registrar(dados: dict, tipo: str, mensagem: str, criada_em: str) -> None:
    _estado["contador"] += 1
    _notificacoes.append({
        "id": f"NOT-{_estado['contador']:06d}",
        "email": dados.get("destinatario_email"),
        "pedido_id": dados.get("pedido_id"),
        "codigo_rastreio": dados.get("codigo_rastreio"),
        "tipo": tipo,
        "mensagem": mensagem,
        "criada_em": criada_em,
    })


@app.post("/admin/reset", status_code=204)
def reset():
    with _lock:
        _notificacoes.clear()
        _eventos_processados.clear()
        _estado["contador"] = 0
    return Response(status_code=204)


@app.post("/eventos")
def receber_evento(evento: EventoIn):
    with _lock:
        if evento.id in _eventos_processados:
            return {"processado": False}
        dados = evento.dados
        pedido_id = dados.get("pedido_id")
        if evento.tipo == "envio.criado":
            mensagem = MENSAGEM_CRIADO.format(pedido_id=pedido_id, codigo_rastreio=dados.get("codigo_rastreio"))
            _registrar(dados, "CRIADO", mensagem, evento.ocorrido_em)
            _eventos_processados.add(evento.id)
            return {"processado": True}
        if evento.tipo == "envio.status_atualizado":
            status = dados.get("status")
            if status in MENSAGENS_STATUS:
                mensagem = MENSAGENS_STATUS[status].format(pedido_id=pedido_id)
                _registrar(dados, status, mensagem, evento.ocorrido_em)
            _eventos_processados.add(evento.id)
            return {"processado": True}
        return {"processado": False}


@app.get("/notificacoes")
def listar_notificacoes(email: str = Query(...)):
    with _lock:
        lista = [copy.deepcopy(n) for n in _notificacoes if n["email"] == email]
    lista.sort(key=lambda n: n["id"])
    return lista
