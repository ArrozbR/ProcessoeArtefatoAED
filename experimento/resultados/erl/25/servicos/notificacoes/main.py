# arquivo: servicos/notificacoes/main.py
from typing import Any

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

_estado: dict[str, Any] = {"processados": set(), "contador": 0, "notificacoes": []}


def erro(status: int, codigo: str, mensagem: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"erro": codigo, "mensagem": mensagem})


def invalido(mensagem: str = "Dados inválidos.") -> JSONResponse:
    return erro(422, "DADOS_INVALIDOS", mensagem)


@app.exception_handler(RequestValidationError)
async def _validacao(request: Request, exc: RequestValidationError):
    return invalido("Dados inválidos na requisição.")


@app.exception_handler(StarletteHTTPException)
async def _http(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        return erro(404, "RECURSO_NAO_ENCONTRADO", "Recurso não encontrado.")
    if exc.status_code == 405:
        return erro(405, "METODO_NAO_PERMITIDO", "Método não permitido.")
    return erro(exc.status_code, "ERRO", str(exc.detail))


async def ler_json(request: Request) -> tuple[bool, Any]:
    try:
        return True, await request.json()
    except Exception:
        return False, None


def registrar(tipo: str, dados: dict, ocorrido_em: Any) -> None:
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
async def eventos(request: Request):
    ok, env = await ler_json(request)
    if not ok or not isinstance(env, dict):
        return invalido("Envelope inválido.")
    for campo in ("id", "tipo", "ocorrido_em", "dados"):
        if campo not in env:
            return invalido(f"Campo '{campo}' ausente no envelope.")
    dados = env["dados"]
    if not isinstance(dados, dict):
        return invalido("Campo 'dados' inválido.")
    eid = env["id"]
    chave = eid if isinstance(eid, str) else repr(eid)
    tipo = env["tipo"]

    if chave in _estado["processados"]:
        return JSONResponse(status_code=200, content={"processado": False})

    if tipo == "envio.criado":
        registrar("CRIADO", dados, env["ocorrido_em"])
    elif tipo == "envio.status_atualizado":
        status = dados.get("status")
        if isinstance(status, str) and status in MENSAGENS and status != "CRIADO":
            registrar(status, dados, env["ocorrido_em"])
    else:
        return JSONResponse(status_code=200, content={"processado": False})

    _estado["processados"].add(chave)
    return JSONResponse(status_code=200, content={"processado": True})


@app.get("/notificacoes")
async def listar(request: Request):
    email = request.query_params.get("email")
    if email is None:
        return invalido("Parâmetro 'email' é obrigatório.")
    itens = [dict(n) for n in _estado["notificacoes"] if n["email"] == email]
    return JSONResponse(status_code=200, content=itens)


@app.post("/admin/reset")
async def reset():
    _estado["processados"] = set()
    _estado["contador"] = 0
    _estado["notificacoes"] = []
    return Response(status_code=204)
