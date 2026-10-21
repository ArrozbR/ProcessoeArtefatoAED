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

_estado = {"notificacoes": [], "contador": 0, "processados": set()}


def erro(status: int, codigo: str, mensagem: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"erro": codigo, "mensagem": mensagem})


@app.exception_handler(RequestValidationError)
async def _validacao(request: Request, exc: RequestValidationError):
    return erro(422, "DADOS_INVALIDOS", "Dados inválidos.")


@app.exception_handler(StarletteHTTPException)
async def _http(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        return erro(404, "ROTA_NAO_ENCONTRADA", "Rota não encontrada.")
    if exc.status_code == 405:
        return erro(405, "METODO_NAO_PERMITIDO", "Método não permitido.")
    return erro(exc.status_code, "ERRO", str(exc.detail))


async def ler_corpo(request: Request):
    try:
        return await request.json()
    except Exception:
        return None


def chave_id(v) -> str:
    return repr(v)


def registrar(tipo: str, dados: dict, ocorrido_em) -> None:
    pedido_id = dados.get("pedido_id")
    codigo = dados.get("codigo_rastreio")
    _estado["contador"] += 1
    _estado["notificacoes"].append({
        "id": "NOT-%06d" % _estado["contador"],
        "email": dados.get("destinatario_email"),
        "pedido_id": pedido_id,
        "codigo_rastreio": codigo,
        "tipo": tipo,
        "mensagem": MENSAGENS[tipo].format(pedido_id=pedido_id, codigo_rastreio=codigo),
        "criada_em": ocorrido_em,
    })


@app.post("/eventos")
async def eventos(request: Request):
    corpo = await ler_corpo(request)
    if not isinstance(corpo, dict):
        return erro(422, "DADOS_INVALIDOS", "Envelope inválido.")
    for campo in ("id", "tipo", "ocorrido_em", "dados"):
        if campo not in corpo:
            return erro(422, "DADOS_INVALIDOS", "Envelope sem o campo %s." % campo)
    dados = corpo["dados"]
    if not isinstance(dados, dict):
        return erro(422, "DADOS_INVALIDOS", "Campo dados inválido.")

    eid = chave_id(corpo["id"])
    if eid in _estado["processados"]:
        return JSONResponse(status_code=200, content={"processado": False})

    tipo = corpo["tipo"]
    if tipo == "envio.criado":
        registrar("CRIADO", dados, corpo["ocorrido_em"])
    elif tipo == "envio.status_atualizado":
        status = dados.get("status")
        if isinstance(status, str) and status in MENSAGENS and status != "CRIADO":
            registrar(status, dados, corpo["ocorrido_em"])
    else:
        return JSONResponse(status_code=200, content={"processado": False})

    _estado["processados"].add(eid)
    return JSONResponse(status_code=200, content={"processado": True})


@app.get("/notificacoes")
async def listar(request: Request):
    email = request.query_params.get("email")
    if email is None:
        return erro(422, "DADOS_INVALIDOS", "Parâmetro email obrigatório.")
    resultado = [dict(n) for n in _estado["notificacoes"] if n["email"] == email]
    resultado.sort(key=lambda n: n["id"])
    return JSONResponse(status_code=200, content=resultado)


@app.post("/admin/reset")
async def reset():
    _estado["notificacoes"] = []
    _estado["contador"] = 0
    _estado["processados"] = set()
    return Response(status_code=204)
