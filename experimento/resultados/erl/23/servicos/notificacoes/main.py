# arquivo: servicos/notificacoes/main.py
import copy
from typing import Any

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

_estado = {"processados": set(), "contador": 0, "notificacoes": []}


def erro(status: int, codigo: str, mensagem: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"erro": codigo, "mensagem": mensagem})


@app.exception_handler(RequestValidationError)
async def _validacao(request: Request, exc: RequestValidationError):
    return erro(422, "DADOS_INVALIDOS", "Dados inválidos na requisição.")


@app.exception_handler(StarletteHTTPException)
async def _http(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        return erro(404, "RECURSO_NAO_ENCONTRADO", "Recurso não encontrado.")
    if exc.status_code == 405:
        return erro(405, "METODO_NAO_PERMITIDO", "Método não permitido.")
    return erro(exc.status_code, "ERRO", str(exc.detail))


@app.post("/admin/reset")
def reset():
    _estado["processados"] = set()
    _estado["contador"] = 0
    _estado["notificacoes"] = []
    return Response(status_code=204)


def _registrar(tipo: str, dados: dict, ocorrido_em: Any) -> None:
    pedido_id = dados.get("pedido_id")
    codigo = dados.get("codigo_rastreio")
    mensagem = MENSAGENS[tipo].format(pedido_id=pedido_id, codigo_rastreio=codigo)
    _estado["contador"] += 1
    _estado["notificacoes"].append({
        "id": "NOT-%06d" % _estado["contador"],
        "email": dados.get("destinatario_email"),
        "pedido_id": pedido_id,
        "codigo_rastreio": codigo,
        "tipo": tipo,
        "mensagem": mensagem,
        "criada_em": ocorrido_em,
    })


@app.post("/eventos")
def eventos(corpo: Any = Body(None)):
    if not isinstance(corpo, dict):
        return erro(422, "DADOS_INVALIDOS", "Envelope inválido.")
    for campo in ("id", "tipo", "ocorrido_em", "dados"):
        if campo not in corpo:
            return erro(422, "DADOS_INVALIDOS", "Envelope sem o campo %s." % campo)
    dados = corpo["dados"]
    if not isinstance(dados, dict):
        return erro(422, "DADOS_INVALIDOS", "Campo dados inválido.")

    evento_id = corpo["id"]
    chave = evento_id if isinstance(evento_id, str) else repr(evento_id)
    if chave in _estado["processados"]:
        return JSONResponse(status_code=200, content={"processado": False})

    tipo = corpo["tipo"]
    if tipo == "envio.criado":
        _registrar("CRIADO", dados, corpo["ocorrido_em"])
        _estado["processados"].add(chave)
        return JSONResponse(status_code=200, content={"processado": True})

    if tipo == "envio.status_atualizado":
        status = dados.get("status")
        if isinstance(status, str) and status in MENSAGENS and status != "CRIADO":
            _registrar(status, dados, corpo["ocorrido_em"])
        _estado["processados"].add(chave)
        return JSONResponse(status_code=200, content={"processado": True})

    return JSONResponse(status_code=200, content={"processado": False})


@app.get("/notificacoes")
def listar(request: Request):
    if "email" not in request.query_params:
        return erro(422, "DADOS_INVALIDOS", "Parâmetro email obrigatório.")
    email = request.query_params["email"]
    lista = [copy.deepcopy(n) for n in _estado["notificacoes"] if n["email"] == email]
    return JSONResponse(status_code=200, content=lista)
