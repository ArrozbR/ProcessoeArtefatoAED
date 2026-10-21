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


@app.exception_handler(RequestValidationError)
async def _tratar_validacao(request: Request, exc: RequestValidationError):
    return erro(422, "DADOS_INVALIDOS", "Dados inválidos na requisição.")


@app.exception_handler(StarletteHTTPException)
async def _tratar_http(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        return erro(404, "RECURSO_NAO_ENCONTRADO", "Recurso não encontrado.")
    if exc.status_code == 405:
        return erro(405, "METODO_NAO_PERMITIDO", "Método não permitido.")
    return erro(exc.status_code, "ERRO", str(exc.detail))


async def _ler_json(request: Request):
    try:
        return await request.json()
    except Exception:
        return None


def _registrar(tipo: str, dados: dict, ocorrido_em) -> None:
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
        "criada_em": ocorrido_em,
    })


@app.post("/eventos")
async def consumir(request: Request):
    env = await _ler_json(request)
    if (
        not isinstance(env, dict)
        or any(c not in env for c in ("id", "tipo", "ocorrido_em", "dados"))
        or not isinstance(env["dados"], dict)
    ):
        return erro(422, "DADOS_INVALIDOS", "Envelope de evento inválido.")

    eid = env["id"]
    try:
        hash(eid)
    except TypeError:
        return erro(422, "DADOS_INVALIDOS", "Identificador de evento inválido.")
    if eid in _estado["processados"]:
        return JSONResponse(status_code=200, content={"processado": False})

    tipo = env["tipo"]
    dados = env["dados"]
    if tipo == "envio.criado":
        _registrar("CRIADO", dados, env["ocorrido_em"])
    elif tipo == "envio.status_atualizado":
        status = dados.get("status")
        if isinstance(status, str) and status in MENSAGENS and status != "CRIADO":
            _registrar(status, dados, env["ocorrido_em"])
    else:
        return JSONResponse(status_code=200, content={"processado": False})

    _estado["processados"].add(eid)
    return JSONResponse(status_code=200, content={"processado": True})


@app.get("/notificacoes")
async def listar(request: Request):
    email = request.query_params.get("email")
    if email is None:
        return erro(422, "DADOS_INVALIDOS", "Parâmetro email é obrigatório.")
    lista = [n for n in _estado["notificacoes"] if n["email"] == email]
    return JSONResponse(status_code=200, content=lista)


@app.post("/admin/reset")
async def reset():
    _estado["contador"] = 0
    _estado["processados"].clear()
    _estado["notificacoes"].clear()
    return Response(status_code=204)
