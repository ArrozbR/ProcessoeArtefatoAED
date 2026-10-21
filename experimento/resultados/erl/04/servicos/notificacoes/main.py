# arquivo: servicos/notificacoes/main.py
import copy
import threading
from typing import Any, Optional

from fastapi import Body, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI()

MENSAGENS = {
    "CRIADO": "Pedido {pedido_id}: envio criado. Rastreio: {codigo_rastreio}.",
    "SAIU_PARA_ENTREGA": "Pedido {pedido_id}: saiu para entrega.",
    "ENTREGUE": "Pedido {pedido_id}: entregue.",
    "FALHA_ENTREGA": "Pedido {pedido_id}: tentativa de entrega sem sucesso.",
    "DEVOLVIDO": "Pedido {pedido_id}: devolvido ao remetente.",
    "CANCELADO": "Pedido {pedido_id}: envio cancelado.",
}

_lock = threading.RLock()
_estado = {"contador": 0, "processados": set(), "notificacoes": []}


def erro(status: int, codigo: str, mensagem: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"erro": codigo, "mensagem": mensagem})


@app.exception_handler(RequestValidationError)
async def _validacao(request: Request, exc: RequestValidationError):
    return erro(422, "DADOS_INVALIDOS", "Dados inválidos na requisição.")


@app.exception_handler(StarletteHTTPException)
async def _http(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        return erro(404, "RECURSO_NAO_ENCONTRADO", "Rota não encontrada.")
    if exc.status_code == 405:
        return erro(405, "METODO_NAO_PERMITIDO", "Método não permitido.")
    return erro(exc.status_code, "ERRO", str(exc.detail))


def _registrar(tipo: str, dados: dict, ocorrido_em: Any) -> None:
    pedido_id = dados.get("pedido_id")
    codigo = dados.get("codigo_rastreio")
    mensagem = MENSAGENS[tipo].replace("{pedido_id}", str(pedido_id)).replace(
        "{codigo_rastreio}", str(codigo)
    )
    _estado["contador"] += 1
    _estado["notificacoes"].append(
        {
            "id": "NOT-%06d" % _estado["contador"],
            "email": dados.get("destinatario_email"),
            "pedido_id": pedido_id,
            "codigo_rastreio": codigo,
            "tipo": tipo,
            "mensagem": mensagem,
            "criada_em": ocorrido_em,
        }
    )


@app.post("/eventos")
def receber_evento(corpo: Any = Body(default=None)):
    if not isinstance(corpo, dict):
        return erro(422, "DADOS_INVALIDOS", "Envelope inválido.")
    for campo in ("id", "tipo", "ocorrido_em", "dados"):
        if campo not in corpo:
            return erro(422, "DADOS_INVALIDOS", "Envelope sem o campo %s." % campo)
    dados = corpo["dados"]
    if not isinstance(dados, dict):
        return erro(422, "DADOS_INVALIDOS", "Campo dados inválido.")
    evento_id = str(corpo["id"])
    tipo = corpo["tipo"]
    ocorrido_em = corpo["ocorrido_em"]

    with _lock:
        if evento_id in _estado["processados"]:
            return JSONResponse(status_code=200, content={"processado": False})

        if tipo == "envio.criado":
            _registrar("CRIADO", dados, ocorrido_em)
            _estado["processados"].add(evento_id)
            return JSONResponse(status_code=200, content={"processado": True})

        if tipo == "envio.status_atualizado":
            status = dados.get("status")
            if isinstance(status, str) and status in MENSAGENS and status != "CRIADO":
                _registrar(status, dados, ocorrido_em)
            _estado["processados"].add(evento_id)
            return JSONResponse(status_code=200, content={"processado": True})

    return JSONResponse(status_code=200, content={"processado": False})


@app.get("/notificacoes")
def listar(email: Optional[str] = None):
    if email is None:
        return erro(422, "DADOS_INVALIDOS", "Parâmetro email é obrigatório.")
    with _lock:
        itens = [copy.deepcopy(n) for n in _estado["notificacoes"] if n["email"] == email]
    itens.sort(key=lambda n: n["id"])
    return JSONResponse(status_code=200, content=itens)


@app.post("/admin/reset")
def reset():
    with _lock:
        _estado["contador"] = 0
        _estado["processados"].clear()
        _estado["notificacoes"].clear()
    return Response(status_code=204)
