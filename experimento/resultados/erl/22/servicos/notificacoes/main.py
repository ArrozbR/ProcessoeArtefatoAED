# arquivo: servicos/notificacoes/main.py
from fastapi import FastAPI, Request
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

_estado = {"contador": 0, "processados": set(), "notificacoes": []}


def erro(status: int, codigo: str, mensagem: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"erro": codigo, "mensagem": mensagem})


def dados_invalidos(mensagem: str = "Dados inválidos.") -> JSONResponse:
    return erro(422, "DADOS_INVALIDOS", mensagem)


@app.exception_handler(RequestValidationError)
async def _handler_validacao(request: Request, exc: RequestValidationError):
    return dados_invalidos("Requisição com dados inválidos.")


@app.exception_handler(StarletteHTTPException)
async def _handler_http(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        return erro(404, "NAO_ENCONTRADO", "Recurso não encontrado.")
    if exc.status_code == 405:
        return erro(405, "METODO_NAO_PERMITIDO", "Método não permitido.")
    return erro(exc.status_code, "ERRO", str(exc.detail))


def _registrar(tipo: str, dados: dict, criada_em) -> None:
    pedido_id = dados.get("pedido_id")
    codigo = dados.get("codigo_rastreio")
    _estado["contador"] += 1
    _estado["notificacoes"].append({
        "id": f"NOT-{_estado['contador']:06d}",
        "email": dados.get("destinatario_email"),
        "pedido_id": pedido_id,
        "codigo_rastreio": codigo,
        "tipo": tipo,
        "mensagem": MENSAGENS[tipo].format(pedido_id=pedido_id, codigo_rastreio=codigo),
        "criada_em": criada_em,
    })


@app.post("/eventos")
async def eventos(request: Request):
    try:
        corpo = await request.json()
    except Exception:
        return dados_invalidos("Corpo JSON inválido.")
    if not isinstance(corpo, dict):
        return dados_invalidos("Envelope inválido.")
    for campo in ("id", "tipo", "ocorrido_em", "dados"):
        if campo not in corpo:
            return dados_invalidos(f"Envelope sem o campo '{campo}'.")

    eid = corpo["id"]
    tipo = corpo["tipo"]
    dados = corpo["dados"]
    try:
        if eid in _estado["processados"]:
            return JSONResponse(status_code=200, content={"processado": False})
    except TypeError:
        return dados_invalidos("Campo 'id' inválido.")

    if tipo not in ("envio.criado", "envio.status_atualizado"):
        return JSONResponse(status_code=200, content={"processado": False})
    if not isinstance(dados, dict):
        return dados_invalidos("Campo 'dados' inválido.")

    if tipo == "envio.criado":
        _registrar("CRIADO", dados, corpo["ocorrido_em"])
    else:
        status = dados.get("status")
        if isinstance(status, str) and status in MENSAGENS and status != "CRIADO":
            _registrar(status, dados, corpo["ocorrido_em"])
    _estado["processados"].add(eid)
    return JSONResponse(status_code=200, content={"processado": True})


@app.get("/notificacoes")
async def listar(request: Request):
    email = request.query_params.get("email")
    if email is None:
        return dados_invalidos("Parâmetro 'email' é obrigatório.")
    itens = [n for n in _estado["notificacoes"] if n["email"] == email]
    itens.sort(key=lambda n: n["id"])
    return JSONResponse(status_code=200, content=itens)


@app.post("/admin/reset")
async def reset():
    _estado["contador"] = 0
    _estado["processados"] = set()
    _estado["notificacoes"] = []
    return Response(status_code=204)
